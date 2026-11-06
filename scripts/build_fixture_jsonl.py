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
docs/decisions/0007-test-fixtures.md.

    python scripts/build_fixture_jsonl.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from chatcde.schemas import (
    AccessRights,
    Author,
    DownloadStatus,
    ManifestEntry,
    PageText,
    Provenance,
    Record,
    TextSource,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
RAW = FIX / "raw"

CDE = "Centre for Development and Environment"
UNIT_PREFIX = CDE + " (CDE) - "
# DSpace-CRIS fills unused slots of position-aligned lists with this marker.
PLACEHOLDER = "#PLACEHOLDER_PARENT_METADATA_VALUE#"
AFFILIATION_KEYS = [f"oairecerif.author.affiliation{s}" for s in ("", "2", "3", "4")]
CONTAINER_KEYS = (
    "dc.relation.ispartof",
    "dc.relation.ispartofbook",
    "dc.relation.ispartofseries",
    "dc.relation.magazine",
)
# dc.subject.ddc (Dewey classes) is a classification, not keywords; left out.
KEYWORD_KEYS = ("dc.subject", "dc.subject.keywords")
ACCESS = {
    "open.access": AccessRights.OPEN,
    "restricted": AccessRights.RESTRICTED,
    "embargoed.access": AccessRights.EMBARGOED,
    "metadata.only": AccessRights.METADATA_ONLY,
}


def vals(md: dict[str, Any], key: str) -> list[str | None]:
    """Values of one metadata key, keeping positions; placeholders become None."""
    out: list[str | None] = []
    for v in md.get(key, []):
        s = (v.get("value") or "").strip()
        out.append(None if not s or s == PLACEHOLDER else s)
    return out


def first(md: dict[str, Any], *keys: str) -> str | None:
    """First real value among ``keys``."""
    for k in keys:
        for v in vals(md, k):
            if v:
                return v
    return None


def clean(s: str) -> str:
    """Collapse whitespace."""
    return " ".join(s.split())


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
    """Map a BORIS item to a :class:`Record`."""
    md = item["metadata"]
    abstract = first(md, "dc.description.abstract")
    doi = first(md, "dc.identifier.doi")
    if doi:
        doi = doi.removeprefix("https://doi.org/").removeprefix("doi:")
    year = first(md, "dc.date.issued")
    units = sorted(
        {
            v.removeprefix(UNIT_PREFIX)
            for vs in md.values()
            for v in (x.get("value", "") for x in vs)
            if v.startswith(UNIT_PREFIX)
        }
    )
    handle = item.get("handle")
    return Record(
        record_id=item["uuid"],
        handle=handle,
        doi=doi,
        title=clean(first(md, "dc.title") or item.get("name", "")),
        abstract=clean(abstract) if abstract else None,
        abstract_source=TextSource.ABSTRACT_BORIS if abstract else None,
        year=int(year[:4]) if year and year[:4].isdigit() else None,
        pub_type=(first(md, "dc.type") or "other").lower(),
        languages=[v for v in vals(md, "dc.language.iso") if v],
        keywords=[clean(v) for k in KEYWORD_KEYS for v in vals(md, k) if v],
        container=first(md, *CONTAINER_KEYS),
        cde_units=units,
        access=ACCESS.get(first(md, "datacite.rights") or "", AccessRights.UNKNOWN),
        has_fulltext=True,
        boris_url=first(md, "dc.identifier.uri")
        or f"https://boris-portal.unibe.ch/handle/{handle}",
        harvested_at=fetch["fetched_at"],
    )


def to_authors(item: dict[str, Any]) -> list[Author]:
    """Authors in order, with ORCID and CDE affiliation from aligned lists."""
    md = item["metadata"]
    names = vals(md, "dc.contributor.author")
    orcids = vals(md, "cris.virtual.orcid")
    affs = [vals(md, k) for k in AFFILIATION_KEYS]
    out = []
    for i, name in enumerate(names):
        mine = [a[i] for a in affs if i < len(a) and a[i]]
        unit = next(
            (a.removeprefix(UNIT_PREFIX) for a in mine if a.startswith(UNIT_PREFIX)),
            None,
        )
        out.append(
            Author(
                record_id=item["uuid"],
                position=i,
                name=name or "(unknown)",
                orcid=orcids[i] if i < len(orcids) else None,
                is_cde=any(CDE in a for a in mine),
                cde_unit=unit,
            )
        )
    return out


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
