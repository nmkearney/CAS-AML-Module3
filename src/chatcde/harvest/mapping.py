"""Map BORIS (DSpace-CRIS) item JSON to :class:`Record` and :class:`Author`.

Pure functions, no network. Field names were checked against live BORIS items on
2026-11-06 (``data/cde_fields.txt``, decision 0007) and the CDE filter is
explained in decision 0008:

* ``#PLACEHOLDER_PARENT_METADATA_VALUE#`` marks an empty slot in a
  position-aligned list and is read as "no value".
* ``dc.contributor.author`` and ``oairecerif.author.affiliation{,2,3,4}`` are
  aligned by position. ``cris.virtual.orcid`` is **not**: its j-th value belongs
  to the person entity whose UUID is the j-th ``cris.virtualsource.orcid`` value,
  matched against each author's ``authority`` (corrected 2026-11-06; decision 0008).
* Org units (including CDE units such as
  ``Centre for Development and Environment (CDE) - Sustainable Governance``)
  are in ``dc.description.sponsorship``.
* ``dc.relation.organization`` holds the organisations a record was credited to
  in the old EPrints BORIS (only migrated records, those with
  ``unibe.eprints.legacyId``, have it). It can name CDE when no author does.
* Access is in ``datacite.rights`` (``open.access``, ``restricted``,
  ``metadata.only``, ``embargoed.access``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from chatcde.schemas import AccessRights, Author, Record, TextSource

BORIS_HANDLE_URL = "https://boris-portal.unibe.ch/handle/{handle}"

CDE = "Centre for Development and Environment"
UNIT_PREFIX = CDE + " (CDE) - "
PLACEHOLDER = "#PLACEHOLDER_PARENT_METADATA_VALUE#"

AFFILIATION_KEYS = tuple(
    f"oairecerif.author.affiliation{s}" for s in ("", "2", "3", "4")
)
EDITOR_AFFILIATION_KEYS = tuple(
    f"oairecerif.editor.affiliation{s}" for s in ("", "2", "3", "4")
)
#: Fields that credit a record to CDE directly (decision 0008).
EXPLICIT_CDE_KEYS = (
    *AFFILIATION_KEYS,
    *EDITOR_AFFILIATION_KEYS,
    "dc.description.sponsorship",
    "dc.publisher",
)
#: Legacy EPrints credit; kept only when confirmed (see :func:`cde_link`).
ORG_LINK_KEY = "dc.relation.organization"
#: Sponsorship that confirms an org-linked record on its own: CDE ran the NCCR.
NCCR_SPONSOR = "NCCR North-South Management Centre"
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

Metadata = dict[str, list[dict[str, Any]]]


def vals(md: Metadata, key: str) -> list[str | None]:
    """Values of one metadata key, keeping positions; placeholders become None."""
    out: list[str | None] = []
    for v in md.get(key, []):
        s = (v.get("value") or "").strip()
        out.append(None if not s or s == PLACEHOLDER else s)
    return out


def first(md: Metadata, *keys: str) -> str | None:
    """First real value among ``keys``, in the order given."""
    for k in keys:
        for v in vals(md, k):
            if v:
                return v
    return None


def clean(s: str) -> str:
    """Collapse runs of whitespace (BORIS titles and abstracts contain newlines)."""
    return " ".join(s.split())


def entity_type(item: dict[str, Any]) -> str:
    """DSpace-CRIS entity type, e.g. ``Publication``, ``Dataset``, ``Project``."""
    return (
        first(item.get("metadata", {}), "dspace.entity.type")
        or item.get("entityType", "")
        or ""
    )


def _names_cde(md: Metadata, keys: tuple[str, ...]) -> bool:
    return any(v and CDE in v for k in keys for v in vals(md, k))


def cde_link(md: Metadata) -> str | None:
    """How a record names CDE: ``"explicit"``, ``"org_link"`` or None.

    ``explicit``: an author or editor affiliation, the sponsorship (org unit) or
    the publisher names CDE. ``org_link``: only the legacy
    ``dc.relation.organization`` does; such records need confirming by
    :func:`confirms_org_link` before they enter the catalogue (decision 0008).
    """
    if _names_cde(md, EXPLICIT_CDE_KEYS):
        return "explicit"
    if _names_cde(md, (ORG_LINK_KEY,)):
        return "org_link"
    return None


def _people(
    md: Metadata, name_key: str, aff_keys: tuple[str, ...]
) -> list[tuple[str, bool]]:
    """(normalised name, has a CDE affiliation here) for each listed person."""
    affs = [vals(md, k) for k in aff_keys]
    out = []
    for i, name in enumerate(vals(md, name_key)):
        if name:
            here = any(i < len(a) and a[i] and CDE in a[i] for a in affs)
            out.append((person_key(name), here))
    return out


def person_key(name: str) -> str:
    """Name as compared across records: whitespace collapsed, case folded."""
    return clean(name).casefold()


def people(md: Metadata) -> dict[str, bool]:
    """Authors and editors of a record: name key -> CDE-affiliated on this record."""
    out: dict[str, bool] = {}
    for key, aff in (
        ("dc.contributor.author", AFFILIATION_KEYS),
        ("dc.contributor.editor", EDITOR_AFFILIATION_KEYS),
    ):
        for name, here in _people(md, key, aff):
            out[name] = out.get(name, False) or here
    return out


def confirms_org_link(md: Metadata, cde_people: set[str]) -> str | None:
    """Why an org-linked record counts as CDE's, or None if nothing confirms it.

    ``"nccr"``: sponsored by the NCCR North-South Management Centre.
    ``"person"``: an author or editor has a CDE affiliation on another record
    (``cde_people``, from the explicit records of the same harvest).
    """
    if any(v and NCCR_SPONSOR in v for v in vals(md, "dc.description.sponsorship")):
        return "nccr"
    if set(people(md)) & cde_people:
        return "person"
    return None


def cde_units(md: Metadata) -> list[str]:
    """Sorted CDE unit names found anywhere in the metadata, without the prefix."""
    return sorted(
        {
            clean(v.removeprefix(UNIT_PREFIX))
            for k in md
            for v in vals(md, k)
            if v and v.startswith(UNIT_PREFIX)
        }
    )


def normalise_doi(doi: str | None) -> str | None:
    """Strip resolver prefixes so DOIs compare as bare ``10.x/y`` strings."""
    if not doi:
        return None
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        doi = doi.removeprefix(prefix)
    return doi.strip() or None


def doi_key(doi: str | None) -> str | None:
    """A DOI in the form used for matching: bare ``10.x/y``, lower case.

    DOIs are case-insensitive, so lower-casing makes the same DOI from BORIS
    and OpenAlex compare equal.
    """
    bare = normalise_doi(doi.strip()) if doi else None
    if bare:
        bare = bare.removeprefix("https://dx.doi.org/")
    return bare.lower() if bare else None


def to_record(item: dict[str, Any], harvested_at: datetime) -> Record:
    """Map one BORIS item to a :class:`Record`.

    ``has_fulltext`` starts False; the file download stage (issue #8) sets it.
    ``harvested_at`` is when the item's JSON was fetched from BORIS.
    """
    md = item["metadata"]
    abstract = first(md, "dc.description.abstract")
    year = first(md, "dc.date.issued")
    handle = item.get("handle")
    return Record(
        record_id=item["uuid"],
        handle=handle,
        doi=normalise_doi(first(md, "dc.identifier.doi")),
        publisher_doi=doi_key(first(md, "dc.identifier.publisherDOI")),
        title=clean(first(md, "dc.title") or item.get("name") or ""),
        abstract=clean(abstract) if abstract else None,
        abstract_source=TextSource.ABSTRACT_BORIS if abstract else None,
        year=int(year[:4]) if year and year[:4].isdigit() else None,
        pub_type=(first(md, "dc.type") or "other").lower(),
        languages=[v for v in vals(md, "dc.language.iso") if v],
        keywords=[clean(v) for k in KEYWORD_KEYS for v in vals(md, k) if v],
        container=first(md, *CONTAINER_KEYS),
        cde_units=cde_units(md),
        access=ACCESS.get(first(md, "datacite.rights") or "", AccessRights.UNKNOWN),
        has_fulltext=False,
        boris_url=first(md, "dc.identifier.uri")
        or BORIS_HANDLE_URL.format(handle=handle),
        harvested_at=harvested_at,
    )


def orcids_by_person(md: Metadata) -> dict[str, str]:
    """Person entity UUID -> ORCID, from the two parallel ``cris.virtual*`` lists."""
    sources = vals(md, "cris.virtualsource.orcid")
    return {
        src: orcid
        for src, orcid in zip(sources, vals(md, "cris.virtual.orcid"), strict=False)
        if src and orcid
    }


def to_authors(item: dict[str, Any]) -> list[Author]:
    """Authors in order, with CDE affiliation (by position) and ORCID (by person)."""
    md = item["metadata"]
    names = vals(md, "dc.contributor.author")
    persons = [v.get("authority") for v in md.get("dc.contributor.author", [])]
    orcid_of = orcids_by_person(md)
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
                orcid=orcid_of.get(persons[i] or "") if i < len(persons) else None,
                is_cde=any(CDE in a for a in mine),
                cde_unit=unit,
            )
        )
    return out
