"""What a tool call runs against: the config, the catalogue and the endpoint.

The tool signatures in :mod:`chatcde.tools.core` are the shared contract and are
what the agent sees, so they carry only the arguments an LLM should choose
(query, filters, IDs). Everything else (which config, which catalogue file,
which endpoint client) comes from the *active* :class:`ToolContext`:

* The CLI and the agent open one with :func:`use_context` around their calls.
* Without one, the first tool call builds a default context from
  :func:`chatcde.config.load_config` (handy in a Python shell or notebook).

Each call opens the catalogue read-only and closes it again, so the tools never
block a pipeline stage that writes, and they are safe to call from threads.
The endpoint client is created on first use and shared.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import duckdb

from chatcde import catalogue
from chatcde.config import Config, load_config
from chatcde.store.endpoint import EndpointClient

log = logging.getLogger(__name__)


class ToolError(Exception):
    """A tool cannot answer, with a message the user or agent can act on.

    Raised for things like an unknown record ID, a page range without text, or a
    missing index. The agent passes the message back to the model as the tool
    result, so it should say what to do instead.
    """


class ToolContext:
    """Config plus lazily opened resources for tool calls.

    Args:
        cfg: The validated configuration.
        client: Optional endpoint client to use (tests pass a fake one); by
            default one is built from the config on first use.
    """

    def __init__(self, cfg: Config, client: EndpointClient | None = None) -> None:
        """Keep the config; the client is created when first needed."""
        self.cfg = cfg
        self._client = client
        self._owns_client = client is None
        self._lock = threading.Lock()

    def endpoint(self) -> EndpointClient:
        """The endpoint client (query embeddings, reranking), created once."""
        with self._lock:
            if self._client is None:
                self._client = EndpointClient.from_config(self.cfg)
            return self._client

    @contextmanager
    def catalogue(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Open the catalogue read-only for one tool call.

        Raises:
            ToolError: If there is no catalogue file yet.
        """
        path = self.cfg.data.catalogue
        if not path.is_file():
            raise ToolError(
                f"no catalogue at {path}; run `chatcde fetch-data` or the pipeline"
            )
        con = catalogue.connect(path, read_only=True)
        try:
            yield con
        finally:
            con.close()

    def close(self) -> None:
        """Close the endpoint client if this context created it."""
        with self._lock:
            if self._client is not None and self._owns_client:
                self._client.close()
                self._client = None


_active: ToolContext | None = None
_default: ToolContext | None = None
_guard = threading.Lock()


def get_context() -> ToolContext:
    """Return the active context, or a default one built from the config files."""
    global _default
    with _guard:
        if _active is not None:
            return _active
        if _default is None:
            log.debug("no active tool context; loading the default config")
            _default = ToolContext(load_config())
        return _default


@contextmanager
def use_context(ctx: ToolContext) -> Iterator[ToolContext]:
    """Make ``ctx`` the active context inside a ``with`` block, then close it.

    Contexts nest: the previous one is restored on exit.
    """
    global _active
    with _guard:
        previous, _active = _active, ctx
    try:
        yield ctx
    finally:
        with _guard:
            _active = previous
        ctx.close()
