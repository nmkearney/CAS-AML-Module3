"""A small, polite client for the BORIS Portal REST API (DSpace 7 + DSpace-CRIS).

Rules it enforces (CLAUDE.md, "Be gentle with BORIS"):

* one request at a time, with ``request_delay_s`` (about 0.3 s) before each;
* read-only GETs;
* TLS verified against ``certifi``'s roots (python.org builds on macOS have none);
* every reply must be JSON. Off the UniBe network the API answers HTTP 200 with
  an HTML "Temporary Access Restriction" page; that raises :class:`NotOnVPNError`
  instead of being parsed as data.

Timeouts, connection errors and 5xx replies are retried ``max_retries`` times
with a growing pause; anything else raises :class:`BorisError`.
"""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import Callable
from typing import Any

import certifi
import httpx

from chatcde import __version__
from chatcde.config import BorisConfig

log = logging.getLogger(__name__)

USER_AGENT = f"ChatCDE/{__version__} (CDE, University of Bern; read-only harvest)"

VPN_HINT = (
    "BORIS sent {ctype} instead of JSON. The REST API only answers on the "
    "UniBe network or VPN: connect to the VPN and try again."
)


class BorisError(Exception):
    """BORIS gave an unusable reply (HTTP error, bad JSON, retries used up)."""


class NotOnVPNError(BorisError):
    """BORIS answered with its HTML access page: not on the UniBe network/VPN."""


class BorisClient:
    """Sequential BORIS REST client with a fixed delay and a JSON check.

    Args:
        cfg: The ``boris`` config section.
        transport: Optional httpx transport, for tests (``httpx.MockTransport``).
        sleep: Function used to pause; tests pass a recorder instead of
            :func:`time.sleep`.
    """

    def __init__(
        self,
        cfg: BorisConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Open an HTTP client that verifies TLS with certifi's roots."""
        self.cfg = cfg
        self.base = cfg.api_base.rstrip("/")
        self._sleep = sleep
        self.requests = 0
        self.http = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=cfg.timeout_s,
            verify=ssl.create_default_context(cafile=certifi.where()),
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> BorisClient:
        """Use the client as a context manager; it closes on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the client."""
        self.close()

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self.http.close()

    def get_json(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET ``url`` and return its JSON body.

        Waits ``request_delay_s`` before every attempt. Retries timeouts,
        connection errors and 5xx replies with a growing pause.

        Raises:
            NotOnVPNError: The reply was not JSON (BORIS's off-network page).
            BorisError: Any other HTTP error, or retries used up.
        """
        attempts = self.cfg.max_retries + 1
        for attempt in range(1, attempts + 1):
            self._sleep(self.cfg.request_delay_s)
            self.requests += 1
            try:
                r = self.http.get(url, params=params)
            except httpx.TransportError as exc:
                problem = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code < 500:
                    return self._json(r)
                problem = f"HTTP {r.status_code}"
            if attempt < attempts:
                pause = 2.0**attempt
                log.warning(
                    "BORIS %s for %s; retry %d/%d in %.0f s",
                    problem,
                    url,
                    attempt,
                    self.cfg.max_retries,
                    pause,
                )
                self._sleep(pause)
        raise BorisError(f"{problem} for {url} after {attempts} attempts")

    @staticmethod
    def _json(r: httpx.Response) -> dict[str, Any]:
        """Check a non-5xx reply and decode it."""
        ctype = r.headers.get("content-type", "")
        is_json = ctype.startswith(("application/json", "application/hal"))
        if r.status_code >= 400:
            hint = "" if is_json else " (not JSON: are you on the UniBe VPN?)"
            raise BorisError(f"HTTP {r.status_code} for {r.url}{hint}: {r.text[:200]}")
        if not is_json or r.text.lstrip().startswith("<"):
            raise NotOnVPNError(VPN_HINT.format(ctype=ctype or "no content type"))
        try:
            return r.json()
        except ValueError as exc:
            raise BorisError(f"invalid JSON from {r.url}: {exc}") from exc

    def preflight(self) -> None:
        """Check BORIS answers with JSON before a long run starts.

        Raises:
            NotOnVPNError: Not on the UniBe network or VPN.
        """
        self.get_json(self.base)

    def search_page(self, page: int) -> dict[str, Any]:
        """One page of the CDE search (``/discover/search/objects``), items only.

        The results embed each item's full metadata, so no per-item request
        is needed. The sort order is fixed by config so pages do not shift
        between runs.
        """
        return self.get_json(
            f"{self.base}/discover/search/objects",
            params={
                "query": self.cfg.cde_query,
                "dsoType": "ITEM",
                "size": self.cfg.page_size,
                "page": page,
                "sort": self.cfg.sort,
            },
        )
