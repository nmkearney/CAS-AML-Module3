"""Helpers shared by the sub-apps in each subpackage's ``cli.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer

if TYPE_CHECKING:
    from chatcde.schemas import Filters

# Panels that group commands in ``chatcde --help``.
PANEL_ASSISTANT = "Assistant (uses the local LLM)"
PANEL_QUERY = "Query the catalogue (no LLM)"
PANEL_PIPELINE = "Pipeline stages"
PANEL_DATA = "Data bundles"
PANEL_SETUP = "Setup"


def not_implemented(command: str, issue: int | None = None) -> NoReturn:
    """Tell the user a command is a placeholder and exit with status 1.

    A non-zero exit keeps a stub from passing for success in scripts.

    Args:
        command: The command as typed, e.g. ``"harvest"``.
        issue: GitHub issue that implements it, if there is one.
    """
    where = f" (see issue #{issue})" if issue else ""
    typer.echo(f"`chatcde {command}` is not implemented yet{where}.", err=True)
    raise typer.Exit(code=1)


#: ``--tag dimension:CODE``: a filter on any tagged dimension (decision 0034).
TagOption = Annotated[
    list[str] | None,
    typer.Option(
        "--tag",
        metavar="DIM:CODE",
        help="Filter on any tagged dimension, e.g. impact_area:SG, "
        "setting:SET-mountains or method:MET-G-biophysical (repeatable; values "
        "of one dimension combine with OR, dimensions with AND).",
    ),
]


def build_filters(tags: list[str] | None, **fields: Any) -> Filters:
    """Build :class:`~chatcde.schemas.Filters` from CLI options plus ``--tag``.

    ``fields`` are the command's own filter options (``countries=[...]``);
    each ``--tag dimension:CODE`` adds ``CODE`` to that dimension's field, so
    ``--tag impact_area:SG`` and a dedicated option can be combined. Exits with
    status 2 on a malformed ``--tag`` or an unknown dimension.
    """
    from chatcde.schemas import Dimension, Filters
    from chatcde.store.filters import TAG_FIELDS

    field_of = {dim: field for field, dim in TAG_FIELDS.items()}
    values: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
    for raw in tags or []:
        dim_text, sep, code = raw.partition(":")
        try:
            dimension = Dimension(dim_text.strip())
        except ValueError:
            dimension = None
        if not sep or not code.strip() or dimension not in field_of:
            known = ", ".join(d.value for d in field_of)
            typer.echo(
                f"--tag {raw!r}: use dimension:CODE with one of: {known}.", err=True
            )
            raise typer.Exit(code=2)
        field = field_of[dimension]
        value: Any = code.strip()
        if dimension is Dimension.SDG:
            if not value.isdigit():
                typer.echo(f"--tag {raw!r}: an SDG is a number, 1-17.", err=True)
                raise typer.Exit(code=2)
            value = int(value)
        values[field] = [*values.get(field, []), value]
    return Filters(**values)
