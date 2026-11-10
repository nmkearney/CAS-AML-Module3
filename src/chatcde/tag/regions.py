"""Countries and the regions derived from them (decision 0017).

The LLM tags countries only, as ISO 3166-1 alpha-3 codes. Region is derived in
code from those tags, at two levels:

* **UN M49 world region:** the finest M49 grouping a country has: its
  intermediate region where M49 defines one (Madagascar → "Eastern Africa",
  Ecuador → "South America"), otherwise its sub-region (Nepal → "Southern Asia",
  Switzerland → "Western Europe").
* **CDE partner region:** ``CDE-EAF``, ``CDE-MSEA``, ``CDE-SAM`` or ``CDE-EUR``,
  from ``region.cde_partner_region`` in the vocabulary. A region is defined
  either by a list of countries or by an M49 code (region, sub-region or
  intermediate region). Countries outside the four get none.

The seven countries with a main partner institution (``cde_partner_countries``)
also get the region term :data:`PARTNER_COUNTRY`.

The country list comes from ``vocab/m49.csv`` (``scripts/fetch_m49.py``, UN
Statistics Division), so tagging needs no network.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Country flag for work with no single study country (``country.extra_flag``).
GLOBAL_FLAG = "global_or_multi_country"
#: Region term for the seven CDE partner countries (decision 0017).
PARTNER_COUNTRY = "cde_partner_country"


@dataclass(frozen=True)
class Country:
    """One row of ``vocab/m49.csv``."""

    alpha3: str
    name: str
    region_code: str
    region: str
    sub_region_code: str
    sub_region: str
    intermediate_region_code: str
    intermediate_region: str

    @property
    def world_region(self) -> str | None:
        """The finest M49 grouping: intermediate region, else sub-region."""
        return self.intermediate_region or self.sub_region or None

    @property
    def m49_codes(self) -> set[str]:
        """Every M49 grouping code the country belongs to."""
        return {
            c
            for c in (
                self.region_code,
                self.sub_region_code,
                self.intermediate_region_code,
            )
            if c
        }


def load_countries(path: Path) -> dict[str, Country]:
    """Countries by alpha-3 code from ``vocab/m49.csv``.

    Raises:
        FileNotFoundError: The file is missing (run ``scripts/fetch_m49.py``).
    """
    with path.open(encoding="utf-8", newline="") as f:
        return {
            r["alpha3"]: Country(
                alpha3=r["alpha3"],
                name=r["name"],
                region_code=r["region_code"],
                region=r["region"],
                sub_region_code=r["sub_region_code"],
                sub_region=r["sub_region"],
                intermediate_region_code=r["intermediate_region_code"],
                intermediate_region=r["intermediate_region"],
            )
            for r in csv.DictReader(f)
        }


class RegionMap:
    """Derives region terms from country codes, using the vocabulary and M49."""

    def __init__(self, countries: dict[str, Country], vocab: dict[str, Any]) -> None:
        """Keep the country table and read the partner regions from ``vocab``."""
        self.countries = countries
        node = vocab.get("region") or {}
        self.partner_regions: dict[str, dict[str, Any]] = dict(
            node.get("cde_partner_region") or {}
        )
        self.partner_countries: set[str] = set(node.get("cde_partner_countries") or [])

    def partner_region(self, alpha3: str) -> str | None:
        """The CDE partner region of a country, or None.

        A region listing ``countries`` matches those; one with an ``m49`` code
        matches every country in that M49 grouping.
        """
        country = self.countries.get(alpha3)
        for code, spec in self.partner_regions.items():
            if alpha3 in (spec.get("countries") or []):
                return code
            m49 = str(spec.get("m49") or "")
            if m49 and country is not None and m49 in country.m49_codes:
                return code
        return None

    def terms(self, alpha3: str) -> list[str]:
        """Region terms for one country tag: M49 region, partner region, flag.

        The global/multi-country flag and unknown codes give no region.
        """
        country = self.countries.get(alpha3)
        if country is None:
            return []
        out = [country.world_region] if country.world_region else []
        if partner := self.partner_region(alpha3):
            out.append(partner)
        if alpha3 in self.partner_countries:
            out.append(PARTNER_COUNTRY)
        return out
