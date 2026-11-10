"""Write ``vocab/m49.csv``: UN M49 country and region codes, for deriving regions.

Tagging (#15) needs two things for every country: the list of valid ISO 3166-1
alpha-3 codes, and the UN M49 region each country belongs to (decision 0017:
region is derived from country in code, never tagged by the LLM). The UN
Statistics Division publishes both on its M49 overview page, as an HTML table
(``downloadTableEN``). This one-off script reads that table and writes a small
CSV that is committed with the vocabulary, so tagging needs no network.

Run from the repository root::

    python scripts/fetch_m49.py                  # fetch the page (public, no VPN)
    python scripts/fetch_m49.py --html page.html # or parse a saved copy

Source: United Nations Statistics Division, "Standard country or area codes for
statistical use (M49)", https://unstats.un.org/unsd/methodology/m49/overview/.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from pathlib import Path

import certifi
import httpx

URL = "https://unstats.un.org/unsd/methodology/m49/overview/"
OUT = Path("vocab/m49.csv")

#: Columns of the UNSD table (in order) -> CSV column, for the ones we keep.
KEEP = {
    "ISO-alpha3 Code": "alpha3",
    "ISO-alpha2 Code": "alpha2",
    "M49 Code": "m49",
    "Country or Area": "name",
    "Region Code": "region_code",
    "Region Name": "region",
    "Sub-region Code": "sub_region_code",
    "Sub-region Name": "sub_region",
    "Intermediate Region Code": "intermediate_region_code",
    "Intermediate Region Name": "intermediate_region",
}


def _text(cell: str) -> str:
    """Cell content without tags, entities decoded, whitespace trimmed."""
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def parse(page: str) -> list[dict[str, str]]:
    """Rows of the English M49 table as dicts with the :data:`KEEP` columns.

    The first row of the table holds the column names (the page uses ``td`` for
    them, not ``th``). Rows without an alpha-3 code (e.g. Antarctica's
    sub-region rows, if any) are skipped.

    Raises:
        ValueError: The English table or an expected column is missing.
    """
    m = re.search(r'<table id\s*=\s*"downloadTableEN".*?</table>', page, re.S)
    if not m:
        raise ValueError("no downloadTableEN table on the page")
    rows = [
        [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S)
    ]
    rows = [r for r in rows if r]
    header, body = rows[0], rows[1:]
    missing = [k for k in KEEP if k not in header]
    if missing:
        raise ValueError(f"columns missing from the M49 table: {missing}")
    idx = {name: header.index(name) for name in KEEP}
    out = [
        {col: r[idx[name]] for name, col in KEEP.items()}
        for r in body
        if len(r) == len(header) and r[idx["ISO-alpha3 Code"]]
    ]
    return sorted(out, key=lambda r: r["alpha3"])


def main() -> int:
    """Fetch or read the page, write :data:`OUT`; return a process exit code."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--html", type=Path, help="parse a saved copy of the page")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if args.html:
        page = args.html.read_text(encoding="utf-8")
    else:
        resp = httpx.get(
            URL,
            headers={"User-Agent": "chatcde (research prototype)"},
            verify=certifi.where(),
            timeout=60,
            follow_redirects=True,
        )
        resp.raise_for_status()
        page = resp.text
    rows = parse(page)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(KEEP.values()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} countries or areas written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
