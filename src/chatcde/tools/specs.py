"""Tool definitions for the LLM, generated from the tool functions themselves.

The agent (and later an MCP server) offers the functions in
:mod:`chatcde.tools.core` to the model. Everything the model sees comes from
the code, so the two cannot drift apart (decision 0022):

* **Parameters:** a pydantic model built from the signature's type hints; its
  JSON schema, with ``$ref`` inlined and titles removed, is the ``parameters``
  of the OpenAI tool definition. Enums (``Dimension``) and nested models
  (``Filters``) come out as JSON-schema enums and objects.
* **Descriptions:** the docstring's text before ``Args:`` (plus its ``Returns:``
  section) describes the tool; each ``Args:`` entry describes its parameter.
* **Calls:** :func:`run_tool` parses the model's JSON arguments, validates
  them with the same pydantic model (so ``{"row": "sub_area"}`` becomes
  ``Dimension.SUB_AREA`` and a filters object becomes ``Filters``), calls the
  function and turns the result, or the error, into text for the model.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from pydantic_core import to_jsonable_python

from chatcde.tools import core
from chatcde.tools.context import ToolError

log = logging.getLogger(__name__)

#: The tools the agent can be offered, by name (``agent.tools`` picks from these).
CORE_TOOLS: dict[str, Callable[..., Any]] = {
    fn.__name__: fn
    for fn in (
        core.search_publications,
        core.get_publication,
        core.read_section,
        core.list_vocabulary,
        core.count_by,
        core.find_gaps,
        core.coverage_report,
    )
}

_SECTION_RE = re.compile(r"^(Args|Arguments|Returns|Raises|Yields|Examples?):\s*$")
_ARG_RE = re.compile(r"^(\w+)(?:\s*\([^)]*\))?:\s*(.*)$")


def parse_docstring(doc: str | None) -> tuple[str, dict[str, str], str | None]:
    """Split a Google-style docstring into description, argument docs and returns.

    Returns:
        ``(description, {argument: text}, returns_text)``. Continuation lines
        are joined with spaces; ``Raises:`` and other sections are dropped.
    """
    lines = inspect.cleandoc(doc or "").splitlines()
    description: list[str] = []
    args: dict[str, str] = {}
    returns: list[str] = []
    section: str | None = None
    current: str | None = None
    for line in lines:
        m = _SECTION_RE.match(line.strip()) if not line.startswith(" ") else None
        if m:
            section = m.group(1)
            current = None
            continue
        if section is None:
            description.append(line)
        elif section in ("Args", "Arguments"):
            stripped = line.strip()
            am = _ARG_RE.match(stripped)
            indent = len(line) - len(line.lstrip())
            if am and indent <= 4:
                current = am.group(1)
                args[current] = am.group(2).strip()
            elif current and stripped:
                args[current] = f"{args[current]} {stripped}".strip()
        elif section == "Returns":
            returns.append(line.strip())
    paragraphs = re.split(r"\n\s*\n", "\n".join(description).strip())
    text = "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())
    ret = " ".join(r for r in returns if r).strip() or None
    tidy = _tidy
    return tidy(text), {k: tidy(v) for k, v in args.items()}, ret and tidy(ret)


def _tidy(text: str) -> str:
    """Turn reST ``literals`` into Markdown `literals` (shorter for the model)."""
    return text.replace("``", "`")


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace every ``$ref`` with its definition and drop ``$defs`` and titles.

    Local models follow flat schemas more reliably than ``$ref`` chains, and
    the ``title`` keywords pydantic adds only repeat the property names.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].split("/")[-1]]
                merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
                return walk(merged)
            return {
                k: walk(v)
                for k, v in node.items()
                if k != "$defs" and not (k == "title" and isinstance(v, str))
            }
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


@dataclass(frozen=True)
class ToolSpec:
    """One tool: the function, its argument model and its description."""

    name: str
    fn: Callable[..., Any]
    description: str
    args_model: type[BaseModel]

    def parameters(self) -> dict[str, Any]:
        """JSON schema of the arguments (flat, no ``$ref``)."""
        schema = _inline_refs(self.args_model.model_json_schema())
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return schema

    def definition(self) -> dict[str, Any]:
        """The OpenAI ``tools`` entry for this function."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters(),
            },
        }


def build_spec(fn: Callable[..., Any]) -> ToolSpec:
    """Generate a :class:`ToolSpec` from a typed, documented function.

    Raises:
        TypeError: If a parameter has no type hint.
    """
    hints = get_type_hints(fn)
    description, arg_docs, returns = parse_docstring(fn.__doc__)
    if returns:
        description = f"{description}\n\nReturns: {returns}"
    fields: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if name not in hints:
            raise TypeError(f"{fn.__name__}: parameter {name!r} has no type hint")
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (
            hints[name],
            Field(default=default, description=arg_docs.get(name)),
        )
    model = create_model(  # type: ignore[call-overload]
        f"{fn.__name__}_arguments",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return ToolSpec(fn.__name__, fn, description, model)


def tool_specs(names: Iterable[str] | None = None) -> dict[str, ToolSpec]:
    """Specs of the named core tools (all of them if ``names`` is ``None``).

    Raises:
        ValueError: If a name is not a core tool.
    """
    chosen = list(CORE_TOOLS) if names is None else list(names)
    unknown = [n for n in chosen if n not in CORE_TOOLS]
    if unknown:
        raise ValueError(
            f"unknown tools {unknown}; choose from {', '.join(CORE_TOOLS)}"
        )
    return {n: build_spec(CORE_TOOLS[n]) for n in chosen}


# ---------------------------------------------------------------------------
# Running a call
# ---------------------------------------------------------------------------


@dataclass
class ToolOutcome:
    """What one tool call did: the content for the model and a log entry."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    content: str
    error: str | None = None
    seconds: float = 0.0
    data: Any = field(default=None, repr=False)


def compact(
    data: Any, *, omit_fields: Sequence[str] = (), max_ids: int | None = None
) -> Any:
    """Shrink a JSON-ready result for the model's context.

    Drops keys named in ``omit_fields`` (e.g. ``provenance``) at any depth, and
    cuts every ``record_ids`` list to ``max_ids`` entries, adding
    ``record_ids_omitted`` with the number left out, so counts stay exact.
    With ``max_ids=0`` the ``record_ids`` lists are dropped altogether (count
    cells already carry their ``count``; decision 0036).
    """
    omit = set(omit_fields)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in omit or (k == "record_ids" and max_ids == 0):
                    continue
                if (
                    k == "record_ids"
                    and isinstance(v, list)
                    and max_ids is not None
                    and len(v) > max_ids
                ):
                    out[k] = v[:max_ids]
                    out["record_ids_omitted"] = len(v) - max_ids
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(data)


class ResultTooLarge(Exception):
    """A result that may not be cut is longer than the limit (decision 0036)."""

    def __init__(self, chars: int, max_chars: int) -> None:
        """Keep the result's length and the limit it went over."""
        super().__init__(f"{chars} characters, over the limit of {max_chars}")
        self.chars = chars
        self.max_chars = max_chars


def render(
    data: Any,
    *,
    max_chars: int,
    omit_fields: Sequence[str] = (),
    max_ids: int | None = None,
    cut: bool = True,
) -> str:
    """JSON text of a result for the model, compacted and cut to ``max_chars``.

    Args:
        data: The JSON-ready result.
        max_chars: Longest text sent to the model.
        omit_fields: Keys dropped at any depth.
        max_ids: Longest ``record_ids`` list kept (see :func:`compact`).
        cut: Whether a longer text may be cut, with a note. Counts must not be:
            a model reads the missing cells of a cut table as zeros (#81).

    Raises:
        ResultTooLarge: If ``cut`` is false and the text is over ``max_chars``.
    """
    text = json.dumps(
        compact(data, omit_fields=omit_fields, max_ids=max_ids),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(text) <= max_chars:
        return text
    if not cut:
        raise ResultTooLarge(len(text), max_chars)
    return (
        text[:max_chars]
        + f" ... [cut: the result had {len(text)} characters; ask for less, "
        "e.g. a smaller k, a narrower page range or more filters]"
    )


def too_large_message(name: str, data: Any, exc: ResultTooLarge) -> str:
    """Error for a count result that is too large to send whole.

    Names the number of cells, if any, and the ways to ask for fewer.
    """
    cells = data.get("cells") if isinstance(data, dict) else None
    size = f"{len(cells)} cells, " if isinstance(cells, list) else ""
    return (
        f"the {name} result is too large to send whole ({size}{exc}). Counts are "
        "never sent cut, so nothing was returned. Ask for fewer cells: "
        "level='group', one dimension instead of two, or filters that narrow "
        "the rows or columns; then drill into the groups you need"
    )


def run_tool(
    spec: ToolSpec,
    raw_arguments: str | dict[str, Any] | None,
    *,
    max_chars: int,
    omit_fields: Sequence[str] = (),
    max_ids: int | None = None,
    cut: bool = True,
) -> ToolOutcome:
    """Validate the model's arguments, call the tool and render the outcome.

    Never raises for the model's mistakes or the tool's refusals: they come back
    as ``ok=False`` with an error the model can read and act on. Unexpected
    exceptions are logged with their traceback and reported briefly. With
    ``cut=False`` (counting tools), a result over ``max_chars`` is an error
    asking for fewer cells, never a partial table (decision 0036).
    """
    started = time.monotonic()
    args: dict[str, Any] = {}

    def failed(message: str) -> ToolOutcome:
        return ToolOutcome(
            spec.name,
            args,
            False,
            json.dumps({"error": message}, ensure_ascii=False),
            message,
            round(time.monotonic() - started, 3),
        )

    try:
        if isinstance(raw_arguments, dict):
            args = raw_arguments
        else:
            parsed = json.loads(raw_arguments or "{}")
            if not isinstance(parsed, dict):
                return failed("arguments must be a JSON object")
            args = parsed
    except json.JSONDecodeError as exc:
        return failed(f"arguments are not valid JSON: {exc}")
    try:
        validated = spec.args_model.model_validate(args)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
            for e in exc.errors()
        )
        return failed(f"invalid arguments for {spec.name}: {problems}")
    kwargs = {name: getattr(validated, name) for name in spec.args_model.model_fields}
    try:
        result = spec.fn(**kwargs)
    except ToolError as exc:
        return failed(str(exc))
    except NotImplementedError:
        return failed(f"{spec.name} is not available yet; answer without it")
    except Exception as exc:  # noqa: BLE001 - report, keep the conversation going
        log.exception("tool %s failed", spec.name)
        return failed(f"{spec.name} failed unexpectedly ({type(exc).__name__})")
    data = to_jsonable_python(result)
    try:
        content = render(
            data,
            max_chars=max_chars,
            omit_fields=omit_fields,
            max_ids=max_ids,
            cut=cut,
        )
    except ResultTooLarge as exc:
        log.warning("tool %s: result not sent (%s)", spec.name, exc)
        return failed(too_large_message(spec.name, data, exc))
    return ToolOutcome(
        spec.name,
        args,
        True,
        content,
        None,
        round(time.monotonic() - started, 3),
        data,
    )
