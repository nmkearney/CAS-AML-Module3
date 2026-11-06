#!/usr/bin/env python3
"""Build the fixture JSONL files from ``tests/fixtures/raw/`` and ``synthetic.yaml``.

A pure transform, no network. Reads the BORIS JSON written by
``fetch_fixture_records.py`` and writes one validated schema object per line:

=================  ==================  =================================
File               Model               Source
=================  ==================  =================================
records.jsonl      Record              item.json (+ fetch.json)
authors.jsonl      Author              item.json, aligned by position
pages.jsonl        PageText            pages.json (PyMuPDF text)
downloads.jsonl    ManifestEntry       fetch.json
=================  ==================  =================================

``tags.jsonl`` is hand-made and not touched here. Synthetic records in
``synthetic.yaml`` are validated and appended to each file. Field mapping notes
(BORIS field names as checked on 2026-11-06) are in
docs/decisions/0007-test-fixtures.md; the mapping itself is
:mod:`chatcde.harvest.mapping`, shared with ``chatcde harvest metadata``.

    python scripts/build_fixture_jsonl.py
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from chatcde.harvest import mapping
from chatcde.harvest.mapping import to_authors
from chatcde.schemas import (
    Author,
    DownloadStatus,
    ManifestEntry,
    PageText,
    Provenance,
    Record,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
RAW = FIX / "raw"


def code_version() -> str:
    """Short git commit of the repo, or 'unknown' outside git."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def to_record(item: dict[str, Any], fetch: dict[str, Any]) -> Record:
    """Map a BORIS item to a :class:`Record`; fixture records all have full text."""
    rec = mapping.to_record(item, datetime.fromisoformat(fetch["fetched_at"]))
    return rec.model_copy(update={"has_fulltext": True})


def to_pages(
    uuid: str, pages: list[dict[str, Any]], prov: Provenance
) -> list[PageText]:
    """PyMuPDF page texts as :class:`PageText` (language left to the parse stage)."""
    return [
        PageText(record_id=uuid, page=p["page"], text=p["text"], provenance=prov)
        for p in pages
    ]


def to_download(fetch: dict[str, Any]) -> ManifestEntry:
    """The fetch summary as a :class:`ManifestEntry`."""
    return ManifestEntry(
        record_id=fetch["uuid"],
        url=fetch["file_url"],
        file_name=fetch["file_name"],
        status=DownloadStatus.DOWNLOADED,
        sha256=fetch["sha256"],
        bytes=fetch["bytes"],
        fetched_at=fetch["fetched_at"],
    )


def load(path: Path) -> Any:
    """Read a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[BaseModel]) -> None:
    """One ``model_dump_json`` per line, UTF-8, trailing newline."""
    path.write_text(
        "".join(r.model_dump_json(exclude_none=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def main() -> None:
    """Rebuild records/authors/pages/downloads JSONL from raw/ and synthetic.yaml."""
    cv = code_version()
    records: list[BaseModel] = []
    authors: list[BaseModel] = []
    pages: list[BaseModel] = []
    downloads: list[BaseModel] = []
    for d in sorted(p for p in RAW.iterdir() if (p / "fetch.json").exists()):
        item, fetch = load(d / "item.json"), load(d / "fetch.json")
        prov = Provenance(code_version=cv, created_at=fetch["fetched_at"])
        records.append(to_record(item, fetch))
        authors += to_authors(item)
        pages += to_pages(item["uuid"], load(d / "pages.json"), prov)
        downloads.append(to_download(fetch))

    syn = yaml.safe_load((FIX / "synthetic.yaml").read_text(encoding="utf-8"))
    n_real = len(records)
    records += [Record.model_validate(r) for r in syn["records"]]
    authors += [Author.model_validate(a) for a in syn["authors"]]
    pages += [PageText.model_validate(p) for p in syn["pages"]]
    downloads += [ManifestEntry.model_validate(m) for m in syn["downloads"]]

    for name, rows in (
        ("records", records),
        ("authors", authors),
        ("pages", pages),
        ("downloads", downloads),
    ):
        write_jsonl(FIX / f"{name}.jsonl", rows)
        print(f"  {name + '.jsonl':16} {len(rows):4d} rows")
    print(f"{n_real} real + {len(records) - n_real} synthetic records")


if __name__ == "__main__":
    main()
