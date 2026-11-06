#!/usr/bin/env python3
"""Fetch the selected fixture records from BORIS: item JSON, licence, first pages.

Needs the UniBe network or VPN (the REST API answers with an HTML page otherwise,
with HTTP 200, so every response is checked for JSON). Reads
``tests/fixtures/selection.csv`` from ``select_fixture_records.py`` and works
through the picks in rank order, swapping in reserves until ``--n`` records are kept.

A record is **kept** only if its main file in the ``ORIGINAL`` bundle carries an
accepted open licence (see :data:`ACCEPTED` and docs/decisions/0007-test-fixtures.md)
the PDF has a text layer on page 1, and the fetched pages hold at least
:data:`MIN_TEXT_CHARS` characters (covers or a story map alone are too thin to tag).
Records listed in ``tests/fixtures/excluded.csv`` (uuid, reason; decided in review)
are dropped too, and their ``raw/`` folder is removed. Otherwise the next reserve
in the queue is tried; ``--n`` caps the number kept.

For each kept record it writes ``tests/fixtures/raw/<uuid>/``:

* ``item.json``: the full BORIS item
* ``bitstreams.json``: the ``ORIGINAL`` bitstreams (metadata only)
* ``fetch.json``: licence, chosen file, sha256, size and fetch time
* ``pages.json``: text of the first ``--pages`` pages plus the first ``--body-pages``
  pages of the body (PyMuPDF), with their real page numbers

The body start is the first page with at least :data:`MIN_BODY_CHARS` characters
and at most one front-matter marker (:data:`FRONT_MATTER`: contents dot leaders,
licence and publisher notices, ISBN, citation boxes). The heuristic is recorded in
``tests/fixtures/body_pages.csv`` with ``source=auto``; a row with ``source=manual``
overrides it. ``--reextract`` rebuilds every ``pages.json`` from the local PDFs
without contacting BORIS.

PDFs go to ``data/raw/fixtures/`` (gitignored) and are never committed. The run is
resumable: a uuid whose ``raw/<uuid>/fetch.json`` exists, or that an earlier run
dropped (``tests/fixtures/raw/fetch_log.csv``), is skipped unless ``--force``.
That log records the decision for every uuid tried and is rewritten each run.
Requests are sequential, with ``--delay`` seconds (default 0.3) between them.

    python scripts/fetch_fixture_records.py --probe <uuid>   # dump metadata only
    python scripts/fetch_fixture_records.py [--n 30] [--force]
    python scripts/fetch_fixture_records.py --reextract      # pages only, no network
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import ssl
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import certifi
import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
RAW = FIX / "raw"
PDF_DIR = ROOT / "data" / "raw" / "fixtures"
BASE = "https://boris-portal.unibe.ch/server/api"
HEADERS = {
    "User-Agent": "ChatCDE-fixtures/0.1 (CDE, University of Bern; read-only)",
    "Accept": "application/json",
}

# Licence field: BORIS stores it on the bitstream, not the item (probed 2026-11-06).
LICENCE_FIELDS = ("dc.rights.uri", "dc.rights")
# Accepted Creative Commons variants: redistributable with attribution and allow
# storing extracts. ND is rejected (we store derived text); see decision 0007.
ACCEPTED = {"by", "by-sa", "by-nc", "by-nc-sa", "zero"}
CC_RE = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/?([\d.]+)?")

# Below this, tags would rest on the title alone (review of 2026-11-06).
MIN_TEXT_CHARS = 1500

# Body-page detection (see module docstring).
MIN_BODY_CHARS = 1500
BODY_SCAN_PAGES = 15
FRONT_MATTER = re.compile(
    r"\.{8,}|BioOne|Terms of Use|licen[cs]ed under|Lizenz|Licence|Zitierweise"
    r"|Citation|Impressum|Inhaltsverzeichnis|Table des matières|Contents|ISBN"
    r"|You may also like",
    re.IGNORECASE,
)
BODY_FIELDS = ["uuid", "body_start", "source", "note"]

LOG_FIELDS = ["uuid", "role", "rank", "status", "reason", "licence", "checked_at"]


class Boris:
    """Minimal, polite BORIS REST client (sequential, fixed delay, JSON check)."""

    def __init__(self, delay: float) -> None:
        """Open an HTTP client that verifies TLS with certifi's roots."""
        ctx = ssl.create_default_context(cafile=certifi.where())
        self.http = httpx.Client(
            headers=HEADERS, timeout=60, verify=ctx, follow_redirects=True
        )
        self.delay = delay

    def get_json(self, url: str) -> dict[str, Any]:
        """GET ``url`` and return JSON; exit if BORIS sent its HTML block page."""
        time.sleep(self.delay)
        r = self.http.get(url)
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith(
            ("application/json", "application/hal")
        ):
            sys.exit(
                f"Got {r.headers.get('content-type')} instead of JSON from {url}. "
                "Are you on the UniBe VPN?"
            )
        return r.json()

    def get_bytes(self, url: str) -> bytes:
        """Download a file (bitstream content)."""
        time.sleep(self.delay)
        r = self.http.get(url, headers={"Accept": "*/*"})
        r.raise_for_status()
        return r.content

    def item(self, uuid: str) -> dict[str, Any]:
        """Full item JSON."""
        return self.get_json(f"{BASE}/core/items/{uuid}")

    def original_bitstreams(self, uuid: str) -> list[dict[str, Any]]:
        """Bitstreams in the item's ``ORIGINAL`` bundle (empty list if none)."""
        data = self.get_json(f"{BASE}/core/items/{uuid}/bundles?size=20")
        for b in data.get("_embedded", {}).get("bundles", []):
            if b.get("name") == "ORIGINAL":
                bs = self.get_json(b["_links"]["bitstreams"]["href"] + "?size=50")
                return bs.get("_embedded", {}).get("bitstreams", [])
        return []


def md_values(md: dict[str, Any], key: str) -> list[str]:
    """Non-empty values of one metadata key."""
    return [v["value"] for v in md.get(key, []) if v.get("value")]


def licence_of(bitstream: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (normalised id such as 'CC-BY-4.0', source URL) for a bitstream."""
    md = bitstream.get("metadata", {})
    for key in LICENCE_FIELDS:
        for v in md_values(md, key):
            m = CC_RE.search(v.lower())
            if m:
                kind, ver = m.group(2), m.group(3) or ""
                norm = "CC0-1.0" if kind == "zero" else f"CC-{kind.upper()}"
                if kind != "zero" and ver:
                    norm += f"-{ver}"
                return norm, v
            return v, v  # a non-CC value, kept verbatim and rejected below
    return None, None


def accepted(norm: str | None) -> bool:
    """True if a normalised licence is in :data:`ACCEPTED`."""
    if not norm or not norm.startswith("CC"):
        return False
    if norm.startswith("CC0"):
        return "zero" in ACCEPTED
    kind = re.sub(r"-[\d.]+$", "", norm[3:]).lower()
    return kind in ACCEPTED


def main_pdf(bitstreams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the main file: the largest PDF in ORIGINAL."""
    pdfs = [b for b in bitstreams if b.get("name", "").lower().endswith(".pdf")]
    return max(pdfs, key=lambda b: b.get("sizeBytes", 0)) if pdfs else None


def extract_pages(pdf: Path, n: int) -> list[dict[str, Any]]:
    """Text of the first ``n`` pages, one dict per page (1-based page numbers)."""
    with pymupdf.open(pdf) as doc:
        return [
            {"page": i + 1, "text": doc[i].get_text("text")}
            for i in range(min(n, doc.page_count))
        ]


def detect_body_start(pdf: Path) -> int | None:
    """First page (1-based) that looks like body text, or None within the scan."""
    with pymupdf.open(pdf) as doc:
        for i in range(min(BODY_SCAN_PAGES, doc.page_count)):
            text = doc[i].get_text("text")
            if (
                len(text.strip()) >= MIN_BODY_CHARS
                and len(FRONT_MATTER.findall(text)) <= 1
            ):
                return i + 1
    return None


def select_pages(
    pdf: Path, n_first: int, n_body: int, body_start: int | None
) -> list[dict[str, Any]]:
    """Pages 1..n_first plus body_start..body_start+n_body-1, deduplicated, in order.

    Keeping the first pages preserves realistic front matter for parsing tests;
    the body pages give taggable content when the front matter is long.
    """
    with pymupdf.open(pdf) as doc:
        wanted = set(range(1, n_first + 1))
        if body_start:
            wanted |= set(range(body_start, body_start + n_body))
        return [
            {"page": p, "text": doc[p - 1].get_text("text")}
            for p in sorted(wanted)
            if p <= doc.page_count
        ]


def read_body_overrides(path: Path) -> dict[str, dict[str, str]]:
    """Rows of ``body_pages.csv`` by uuid (empty if the file does not exist)."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {r["uuid"]: r for r in csv.DictReader(f)}


def reextract(n_first: int, n_body: int, body_csv: Path) -> None:
    """Rewrite pages.json (and body_start in fetch.json) for every kept record.

    Uses the PDFs in ``data/raw/fixtures/``; no network. Manual rows in
    ``body_csv`` are kept, auto rows are recomputed.
    """
    rows = read_body_overrides(body_csv)
    out_rows = []
    for d in sorted(p for p in RAW.iterdir() if (p / "fetch.json").exists()):
        pdf = PDF_DIR / f"{d.name}.pdf"
        if not pdf.exists():
            sys.exit(f"Missing {pdf}; run a normal fetch with --force for {d.name}.")
        row = rows.get(d.name)
        if row and row["source"] == "manual":
            start = int(row["body_start"]) if row["body_start"] else None
        else:
            start = detect_body_start(pdf)
            row = {"uuid": d.name, "source": "auto", "note": ""}
        row["body_start"] = str(start or "")
        out_rows.append(row)
        pages = select_pages(pdf, n_first, n_body, start)
        write_json(d / "pages.json", pages)
        fetch = json.loads((d / "fetch.json").read_text(encoding="utf-8"))
        fetch["body_start"] = start
        write_json(d / "fetch.json", fetch)
        print(
            f"  {d.name[:8]} body_start={start!s:5} {row['source']:6} pages "
            f"{[p['page'] for p in pages]}"
        )
    with body_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BODY_FIELDS)
        w.writeheader()
        w.writerows(out_rows)


def write_json(path: Path, obj: Any) -> None:
    """Write pretty, stable JSON (UTF-8, sorted keys) so diffs stay readable."""
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def fetch_one(
    boris: Boris, uuid: str, pages: int, n_body: int
) -> tuple[str, str, str | None]:
    """Fetch one record; return (status 'kept'|'dropped', reason, licence)."""
    item = boris.item(uuid)
    bitstreams = boris.original_bitstreams(uuid)
    pdf_bs = main_pdf(bitstreams)
    if pdf_bs is None:
        return "dropped", "no PDF in ORIGINAL", None
    norm, lic_url = licence_of(pdf_bs)
    if not accepted(norm):
        return "dropped", f"licence not accepted: {norm or 'none'}", norm

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = PDF_DIR / f"{uuid}.pdf"
    content = boris.get_bytes(pdf_bs["_links"]["content"]["href"])
    pdf_path.write_bytes(content)
    try:
        page_texts = extract_pages(pdf_path, pages)
    except Exception as e:  # noqa: BLE001 - a broken PDF just drops the record
        return "dropped", f"PDF unreadable: {e}", norm
    if not page_texts or len(page_texts[0]["text"].strip()) < 50:
        return "dropped", "no text layer on page 1 (scan; no OCR for fixtures)", norm
    n_chars = sum(len(p["text"].strip()) for p in page_texts)
    if n_chars < MIN_TEXT_CHARS:
        return "dropped", f"too little text in first pages ({n_chars} chars)", norm

    out = RAW / uuid
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "item.json", item)
    write_json(out / "bitstreams.json", bitstreams)
    override = read_body_overrides(FIX / "body_pages.csv").get(uuid)
    if override and override["source"] == "manual":
        body_start = int(override["body_start"]) if override["body_start"] else None
    else:
        body_start = detect_body_start(pdf_path)
    write_json(out / "pages.json", select_pages(pdf_path, pages, n_body, body_start))
    write_json(
        out / "fetch.json",
        {
            "uuid": uuid,
            "licence": norm,
            "licence_url": lic_url,
            "file_name": pdf_bs.get("name"),
            "file_url": pdf_bs["_links"]["content"]["href"],
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "body_start": body_start,
        },
    )
    return "kept", "ok", norm


def probe(boris: Boris, uuid: str) -> None:
    """Print item, bundle and bitstream metadata so field names can be checked."""
    item = boris.item(uuid)
    print(json.dumps(item["metadata"], ensure_ascii=False, indent=1))
    for b in boris.original_bitstreams(uuid):
        print("\nBITSTREAM", b.get("name"), b.get("sizeBytes"))
        print(json.dumps(b.get("metadata", {}), ensure_ascii=False, indent=1))
        print("licence ->", licence_of(b))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selection", type=Path, default=FIX / "selection.csv")
    ap.add_argument("--excluded", type=Path, default=FIX / "excluded.csv")
    ap.add_argument("--n", type=int, default=30, help="records to keep")
    ap.add_argument("--pages", type=int, default=3, help="first pages to keep")
    ap.add_argument("--body-pages", type=int, default=3, help="body pages to add")
    ap.add_argument(
        "--reextract",
        action="store_true",
        help="rebuild pages.json from local PDFs; no network",
    )
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--force", action="store_true", help="refetch finished records")
    ap.add_argument("--probe", metavar="UUID", help="dump metadata and exit")
    args = ap.parse_args()

    if args.reextract:
        reextract(args.pages, args.body_pages, FIX / "body_pages.csv")
        return
    boris = Boris(args.delay)
    if args.probe:
        probe(boris, args.probe)
        return

    with args.selection.open(encoding="utf-8", newline="") as f:
        queue = [r for r in csv.DictReader(f)]
    queue.sort(key=lambda r: (r["role"] != "pick", int(r["rank"])))

    excluded: dict[str, str] = {}
    if args.excluded.exists():
        with args.excluded.open(encoding="utf-8", newline="") as f:
            excluded = {x["uuid"]: x["reason"] for x in csv.DictReader(f)}

    RAW.mkdir(parents=True, exist_ok=True)
    log_path = RAW / "fetch_log.csv"
    earlier: dict[str, dict[str, str]] = {}
    if log_path.exists() and not args.force:
        with log_path.open(encoding="utf-8", newline="") as f:
            earlier = {x["uuid"]: x for x in csv.DictReader(f)}
    log_rows: list[dict[str, str]] = []
    kept = 0
    for r in queue:
        if kept >= args.n:
            break
        uuid = r["uuid"]
        done = (RAW / uuid / "fetch.json").exists()
        prev = earlier.get(uuid)
        if uuid in excluded:
            lic = None
            if done:
                lic = json.loads((RAW / uuid / "fetch.json").read_text())["licence"]
                shutil.rmtree(RAW / uuid)
            status, reason = "dropped", f"excluded in review: {excluded[uuid]}"
        elif done and not args.force:
            status, reason = "kept", "already fetched"
            lic = json.loads((RAW / uuid / "fetch.json").read_text())["licence"]
        elif prev and prev["status"] == "dropped":
            status, reason, lic = "dropped", prev["reason"], prev["licence"] or None
        else:
            try:
                status, reason, lic = fetch_one(
                    boris, uuid, args.pages, args.body_pages
                )
            except httpx.HTTPStatusError as e:
                status, reason, lic = "dropped", f"HTTP {e.response.status_code}", None
        kept += status == "kept"
        print(f"  {status:7} {r['role']:7} {uuid[:8]} {lic or '-':16} {reason}")
        log_rows.append(
            {
                "uuid": uuid,
                "role": r["role"],
                "rank": r["rank"],
                "status": status,
                "reason": reason,
                "licence": lic or "",
                "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )

    with log_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        w.writerows(log_rows)
    dropped = sum(1 for x in log_rows if x["status"] == "dropped")
    print(f"\nKept {kept}/{args.n}; dropped {dropped}; log -> {log_path}")
    if kept < args.n:
        print("Not enough records: raise --reserves in select_fixture_records.py.")


if __name__ == "__main__":
    main()
