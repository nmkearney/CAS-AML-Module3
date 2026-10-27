#!/usr/bin/env python3
"""
Inventory of CDE publications in BORIS Portal (University of Bern).

Run on a machine that is on the UniBe network or VPN (the REST API is
currently closed to outside traffic). Uses only the Python standard library.

    python3 boris_cde_inventory.py                  # count + summary + CSV
    python3 boris_cde_inventory.py --check-files 60 # also sample items for attached PDFs

Outputs:
    cde_records.csv      one row per matched record: metadata, keywords, abstract
    cde_summary.txt      counts by type, year, language, access rights, CDE unit,
                         plus the most common subject keywords
    cde_fields.txt       which metadata fields BORIS uses on CDE records
"""

import argparse
import collections
import csv
import json
import random
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

try:  # python.org builds on macOS ship without root certificates
    import certifi

    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

BASE = "https://boris-portal.unibe.ch/server/api"
CDE = "Centre for Development and Environment"
UA = {
    "User-Agent": "CDE-RAG-feasibility/0.1 (internal inventory)",
    "Accept": "application/json",
}


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        body = r.read().decode("utf-8", "replace")
    if body.lstrip().startswith("<"):
        sys.exit(
            "Got an HTML page instead of JSON. Are you on the UniBe VPN? "
            "(The API returns a 'Temporary Access Restriction' page otherwise.)"
        )
    return json.loads(body)


def search_page(page, size=100):
    q = urllib.parse.urlencode(
        {
            "query": f'"{CDE}"',
            "dsoType": "ITEM",
            "size": size,
            "page": page,
        }
    )
    return get_json(f"{BASE}/discover/search/objects?{q}")


def values(md, key):
    return [v.get("value", "") for v in md.get(key, []) if v.get("value")]


def first(md, *keys):
    for k in keys:
        v = values(md, k)
        if v:
            return v[0]
    return ""


TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def keys_like(md, *needles):
    out = []
    for k in md:
        kl = k.lower()
        if "date" in kl:  # e.g. dc.date.accessioned matched "access" before
            continue
        if any(n in kl for n in needles):
            out.extend(v for v in values(md, k) if not TIMESTAMP.match(v))
    return out


def keys_prefixed(md, prefix):
    out = []
    for k in md:
        if k.startswith(prefix):
            out.extend(values(md, k))
    return out


def clean(s):
    return " ".join(s.split())


def is_cde(md):
    """True if any affiliation/organisation-like field mentions CDE."""
    for k, vs in md.items():
        kl = k.lower()
        if any(
            n in kl
            for n in (
                "affiliation",
                "orgunit",
                "department",
                "contributor",
                "credit",
                "publisher",
            )
        ) and any(CDE in v.get("value", "") for v in vs):
            return True
    return False


def cde_units(md):
    units = set()
    for vs in md.values():
        for v in vs:
            s = v.get("value", "")
            if s.startswith(CDE + " (CDE) - "):
                units.add(s.split(" - ", 1)[1])
    return units


def has_original_files(item_uuid):
    """Count PDFs/files in the ORIGINAL bundle (what a RAG would ingest)."""
    data = get_json(f"{BASE}/core/items/{item_uuid}/bundles?size=20")
    for b in data.get("_embedded", {}).get("bundles", []):
        if b.get("name") == "ORIGINAL":
            bs = get_json(b["_links"]["bitstreams"]["href"] + "?size=50")
            return len(bs.get("_embedded", {}).get("bitstreams", []))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check-files",
        type=int,
        default=0,
        help="sample N records and check for attached files",
    )
    ap.add_argument(
        "--delay", type=float, default=0.3, help="seconds between requests (be gentle)"
    )
    args = ap.parse_args()

    first_page = search_page(0, size=100)
    sr = first_page["_embedded"]["searchResult"]
    total_hits = sr["page"]["totalElements"]
    total_pages = sr["page"]["totalPages"]
    print(
        f"Search hits mentioning CDE (upper bound): {total_hits} "
        f"across {total_pages} pages"
    )

    rows = []
    field_census = collections.Counter()
    for p in range(total_pages):
        data = first_page if p == 0 else search_page(p)
        objs = data["_embedded"]["searchResult"]["_embedded"]["objects"]
        for o in objs:
            item = o["_embedded"]["indexableObject"]
            md = item.get("metadata", {})
            rows.append(
                {
                    "uuid": item.get("uuid", ""),
                    "entity_type": first(md, "dspace.entity.type"),
                    "is_cde": is_cde(md),
                    "title": first(md, "dc.title"),
                    "year": first(md, "dc.date.issued")[:4],
                    "type": first(md, "dc.type"),
                    "language": "; ".join(values(md, "dc.language.iso")),
                    "access": "; ".join(sorted(set(keys_like(md, "rights", "access")))),
                    "doi": first(md, "dc.identifier.doi"),
                    "cde_units": "; ".join(sorted(cde_units(md))),
                    "has_abstract": bool(values(md, "dc.description.abstract")),
                    "keywords": "; ".join(
                        clean(k) for k in keys_prefixed(md, "dc.subject")
                    ),
                    "container": first(
                        md, "dc.relation.ispartof", "dc.relation.ispartofseries"
                    ),
                    "abstract": clean(first(md, "dc.description.abstract")),
                }
            )
            if rows[-1]["is_cde"]:
                field_census.update(md.keys())
        print(f"  page {p + 1}/{total_pages}", end="\r", flush=True)
        time.sleep(args.delay)
    print()

    cde = [r for r in rows if r["is_cde"]]
    pubs = [r for r in cde if r["entity_type"] in ("Publication", "")]

    with open("cde_records.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(cde)

    def table(title, counter, top=40):
        lines = [f"\n{title}"]
        for k, v in counter.most_common(top):
            lines.append(f"  {v:6d}  {k or '(empty)'}")
        return "\n".join(lines)

    no_abs_doi = sum(1 for r in pubs if not r["has_abstract"] and r["doi"])
    out = [
        f"Search hits mentioning CDE: {total_hits}",
        f"Records with a CDE affiliation/credit: {len(cde)}",
        f"  of which publications (entity type Publication/blank): {len(pubs)}",
        "",
        "All figures below are for publications only.",
        f"  with an abstract in metadata: {sum(r['has_abstract'] for r in pubs)}",
        f"  with a DOI: {sum(bool(r['doi']) for r in pubs)}",
        f"  without abstract but with DOI (OpenAlex can fill): {no_abs_doi}",
        f"  with subject keywords: {sum(bool(r['keywords']) for r in pubs)}",
        table(
            "Entity type (all CDE records)",
            collections.Counter(r["entity_type"] for r in cde),
        ),
        table("Publication type", collections.Counter(r["type"].lower() for r in pubs)),
        table("Access rights", collections.Counter(r["access"] for r in pubs)),
        table("Language", collections.Counter(r["language"] for r in pubs)),
        table(
            "CDE unit",
            collections.Counter(
                u
                for r in pubs
                for u in (
                    r["cde_units"].split("; ") if r["cde_units"] else ["(none listed)"]
                )
            ),
        ),
        table("Year", collections.Counter(r["year"] for r in pubs), top=80),
        table(
            "Top 60 subject keywords",
            collections.Counter(
                k.strip().lower()
                for r in pubs
                for k in r["keywords"].split(";")
                if k.strip()
            ),
            top=60,
        ),
    ]

    with open("cde_fields.txt", "w", encoding="utf-8") as f:
        f.write("Metadata fields on CDE records (count of records using each)\n")
        f.writelines(f"  {v:6d}  {k}\n" for k, v in sorted(field_census.items()))

    if args.check_files and pubs:
        sample = random.sample(pubs, min(args.check_files, len(pubs)))
        with_files = 0
        for i, r in enumerate(sample, 1):
            try:
                if has_original_files(r["uuid"]) > 0:
                    with_files += 1
            except Exception as e:  # noqa: BLE001
                print(f"  file check failed for {r['uuid']}: {e}")
            print(f"  file check {i}/{len(sample)}", end="\r", flush=True)
            time.sleep(args.delay)
        print()
        out.append(
            f"\nFile sample: {with_files}/{len(sample)} sampled records "
            f"have files in the ORIGINAL bundle "
            f"(~{100 * with_files / len(sample):.0f}%)"
        )

    summary = "\n".join(out)
    with open("cde_summary.txt", "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(summary)
    print("\nWrote cde_records.csv, cde_summary.txt and cde_fields.txt")


if __name__ == "__main__":
    main()
