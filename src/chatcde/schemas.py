"""Shared data formats for ChatCDE (DRAFT, to be agreed on day 1).

This module is the contract between the two tracks:

* **Track A (Data)** writes these objects: :class:`Record`, :class:`Tag`,
  :class:`PageText`, :class:`ManifestEntry` and :class:`BundleManifest`.
* **Track B (Assistant)** reads them, writes :class:`Chunk` objects to the
  search index, and returns the tool output models at the bottom of this file.

Changing anything here needs a pull request approved by both developers
(see CONTRIBUTING.md, "Changing shared formats").

Storage: each model maps to one table in ``data/catalogue.duckdb``:

=================  =================  ===========
Model              Table              Written by
=================  =================  ===========
Record             ``records``        Track A
Author             ``authors``        Track A
Tag                ``tags``           Track A
PageText           ``pages``          Track A
ManifestEntry      ``downloads``      Track A
Chunk              ``chunks``         Track B
=================  =================  ===========

List fields are stored as DuckDB ``VARCHAR[]`` columns.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class AccessRights(str, Enum):
    """Access status of a record's full text, normalised from BORIS."""

    OPEN = "open"
    RESTRICTED = "restricted"
    EMBARGOED = "embargoed"
    METADATA_ONLY = "metadata_only"
    UNKNOWN = "unknown"


class Edition(str, Enum):
    """Data bundle edition (see docs/decisions/0004-two-data-editions.md)."""

    FULL = "full"
    OPEN = "open"


class TextSource(str, Enum):
    """Where a piece of text came from."""

    FULLTEXT = "fulltext"
    ABSTRACT_BORIS = "abstract_boris"
    ABSTRACT_OPENALEX = "abstract_openalex"
    METADATA = "metadata"  # title + keywords only


class DownloadStatus(str, Enum):
    """Outcome of trying to fetch a record's file from BORIS."""

    DOWNLOADED = "downloaded"
    RESTRICTED = "restricted"  # file exists but could not be downloaded
    NO_FILE = "no_file"  # record has no file in the ORIGINAL bundle
    ERROR = "error"


class Dimension(str, Enum):
    """The 17 schema dimensions (docs/schema_draft.md)."""

    IMPACT_AREA = "impact_area"
    SUB_AREA = "sub_area"
    OBJECTIVE = "objective"
    PATHWAY = "pathway"
    RESEARCH_MODE = "research_mode"
    TRANSFORMATIVE_ASPIRATION = "transformative_aspiration"
    METHOD = "method"
    DATA_TYPE = "data_type"
    COUNTRY = "country"
    REGION = "region"
    SETTING = "setting"
    SCALE = "scale"
    SYSTEM = "system"
    ACTORS = "actors"
    JUSTICE_LENS = "justice_lens"
    SDG = "sdg"
    OUTPUT_TYPE = "output_type"


class ActorRole(str, Enum):
    """How an actor group appears in the work (dimension ``actors`` only)."""

    STUDIED = "studied"
    CO_PRODUCER = "co_producer"
    AUDIENCE = "audience"


# ---------------------------------------------------------------------------
# Provenance: attached to everything an LLM or pipeline step produced
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """What produced a piece of data, so results can be reproduced."""

    code_version: str = Field(
        description="chatcde package version or git commit")
    created_at: datetime
    model: str | None = Field(
        default=None, description="LLM or embedding model name")
    prompt_version: str | None = None
    vocab_version: str | None = None


# ---------------------------------------------------------------------------
# Track A outputs
# ---------------------------------------------------------------------------


class Author(BaseModel):
    """An author of a record, as listed in BORIS."""

    record_id: str
    position: int = Field(ge=0, description="0-based author order")
    name: str
    orcid: str | None = None
    is_cde: bool = Field(description="Has a CDE affiliation on this record")
    cde_unit: str | None = Field(
        default=None, description="e.g. 'Sustainable Land Systems'"
    )


class Record(BaseModel):
    """One BORIS publication with a CDE affiliation."""

    record_id: str = Field(
        description="BORIS item UUID; the ID used everywhere")
    handle: str | None = Field(
        default=None, description="e.g. '20.500.12422/176981'")
    doi: str | None = None
    title: str
    abstract: str | None = None
    abstract_source: TextSource | None = None
    year: int | None = None
    pub_type: str = Field(
        description="BORIS type, lower-cased, e.g. 'article'")
    languages: list[str] = Field(
        default_factory=list, description="ISO 639-1 codes")
    keywords: list[str] = Field(default_factory=list)
    container: str | None = Field(
        default=None, description="Journal, series or book title"
    )
    cde_units: list[str] = Field(default_factory=list)
    access: AccessRights = AccessRights.UNKNOWN
    has_fulltext: bool = False
    boris_url: str
    harvested_at: datetime


class PageText(BaseModel):
    """Parsed text of one page of a record's main file."""

    record_id: str
    page: int = Field(ge=1)
    text: str
    language: str | None = None
    ocr: bool = Field(
        default=False, description="Text came from OCR or the vision model"
    )
    provenance: Provenance


class Tag(BaseModel):
    """One controlled-vocabulary tag on a record."""

    record_id: str
    dimension: Dimension
    term: str = Field(description="Vocabulary code, e.g. 'SL-d', 'KEN', 'O2'")
    other_label: str | None = Field(
        default=None, description="Free text when term == 'other'"
    )
    role: ActorRole | None = Field(
        default=None, description="Only for dimension 'actors'"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str | None = Field(
        default=None, description="Short supporting quote")
    source: TextSource = Field(description="Text the tag was based on")
    provenance: Provenance


class ManifestEntry(BaseModel):
    """Download status of one record's file."""

    record_id: str
    url: str | None = None
    file_name: str | None = None
    status: DownloadStatus
    sha256: str | None = None
    bytes: int | None = None
    error: str | None = None
    fetched_at: datetime


class BundleManifest(BaseModel):
    """Describes a distributable data bundle (``manifest.json`` inside it)."""

    edition: Edition
    version: str = Field(description="Bundle version, e.g. '2026.11.0'")
    built_at: datetime
    code_version: str
    vocab_version: str
    models: dict[str, str] = Field(
        description="Role -> model name, from config")
    record_count: int
    fulltext_count: int
    files: dict[str, str] = Field(description="File name -> sha256")


# ---------------------------------------------------------------------------
# Track B storage
# ---------------------------------------------------------------------------


class Chunk(BaseModel):
    """A searchable passage, built by Track B from PageText or abstracts."""

    chunk_id: str = Field(description="'{record_id}:{n}'")
    record_id: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    language: str | None = None
    token_count: int
    source: TextSource


# ---------------------------------------------------------------------------
# Tool inputs and outputs (Track B returns these; the agent sees them as JSON)
# ---------------------------------------------------------------------------


class Filters(BaseModel):
    """Filters shared by all tools. Empty fields mean 'no restriction'."""

    year_from: int | None = None
    year_to: int | None = None
    cde_units: list[str] = Field(default_factory=list)
    pub_types: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    countries: list[str] = Field(
        default_factory=list, description="ISO alpha-3")
    regions: list[str] = Field(default_factory=list)
    sub_areas: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    sdgs: list[int] = Field(default_factory=list)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class Citation(BaseModel):
    """How an answer cites a record."""

    record_id: str
    title: str
    year: int | None
    doi: str | None
    boris_url: str
    page: int | None = None


class Passage(BaseModel):
    """A search hit."""

    chunk: Chunk
    score: float
    citation: Citation


class PublicationDetail(BaseModel):
    """Everything known about one record."""

    record: Record
    authors: list[Author]
    tags: list[Tag]
    download: ManifestEntry | None


class VocabTerm(BaseModel):
    """A term in a controlled vocabulary, with its current usage."""

    dimension: Dimension
    code: str
    label: str
    definition: str | None = None
    record_count: int


class CountCell(BaseModel):
    """One cell of a count matrix."""

    row: str
    col: str | None = None
    count: int
    record_ids: list[str]


class CountMatrix(BaseModel):
    """Result of ``count_by``: counts with their denominators."""

    row_dimension: Dimension
    col_dimension: Dimension | None
    filters: Filters
    cells: list[CountCell]
    row_totals: dict[str, int]
    col_totals: dict[str, int]
    total_records: int = Field(description="Records matching the filters")
    untagged_records: int = Field(
        description="Matching records with no tag on these dimensions"
    )


class GapCell(CountCell):
    """A thin cell from ``find_gaps``."""

    expected: float = Field(
        description="Count expected if row and column tags were independent"
    )
    ratio: float = Field(description="count / expected")


class CoverageReport(BaseModel):
    """How complete the data behind an answer is."""

    edition: Edition
    filters: Filters
    total_records: int
    with_fulltext: int
    abstract_only: int
    metadata_only: int
    low_confidence_tags: int
    by_year: dict[str, int]
    by_language: dict[str, int]
