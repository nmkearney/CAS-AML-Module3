"""Tests for ``chatcde harvest openalex`` (issue #9, decision 0010), all offline.

A small catalogue is built from hand-made :class:`Record` objects; publisher
DOIs come from a saved BORIS search page written into the temp raw cache, and
OpenAlex is served through ``httpx.MockTransport``.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from chatcde import catalogue
from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.harvest.metadata import HarvestError
from chatcde.harvest.openalex import (
    OpenAlexClient,
    OpenAlexError,
    abstract_from_index,
    clean_abstract,
    doi_key,
    fetch_works,
    harvest_openalex,
    oa_link,
    openalex_dir,
)
from chatcde.schemas import DownloadStatus, ManifestEntry, Record, TextSource

T0 = datetime(2026, 11, 7, tzinfo=UTC)
LONG = " ".join(f"word{i}" for i in range(40))
HOSTS = ["boris.unibe.ch", "boris-portal.unibe.ch"]
PREFIXES = ["10.7892/", "10.48350/", "10.48620/"]


def inverted(text: str) -> dict[str, list[int]]:
    inv: dict[str, list[int]] = {}
    for i, w in enumerate(text.split()):
        inv.setdefault(w, []).append(i)
    return inv


def work(doi: str, abstract: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/W{abs(hash(doi)) % 10**8}",
        "doi": f"https://doi.org/{doi}",
        "language": "en",
        "abstract_inverted_index": inverted(abstract) if abstract else None,
        "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
        "best_oa_location": None,
        **extra,
    }


def rec(
    rid: str,
    doi: str | None,
    abstract: str | None = None,
    publisher_doi: str | None = None,
) -> Record:
    return Record(
        record_id=rid,
        doi=doi,
        publisher_doi=publisher_doi,
        title=f"Title {rid}",
        abstract=abstract,
        abstract_source=TextSource.ABSTRACT_BORIS if abstract else None,
        pub_type="article",
        boris_url=f"https://boris-portal.unibe.ch/entities/publication/{rid}",
        harvested_at=T0,
    )


class FakeOpenAlex:
    """Answers ``/works?filter=doi:a|b`` from ``works``; can fail on demand."""

    def __init__(self, works: list[dict[str, Any]]) -> None:
        self.works = {doi_key(w["doi"]): w for w in works}
        self.calls: list[httpx.Request] = []
        self.fail: dict[int, int] = {}  # call number (1-based) -> status code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        status = self.fail.get(len(self.calls))
        if status:
            return httpx.Response(status, json={"error": "nope"})
        flt = request.url.params["filter"]
        assert flt.startswith("doi:")
        dois = flt.removeprefix("doi:").split("|")
        assert len(dois) <= 100
        results = [self.works[d.lower()] for d in dois if d.lower() in self.works]
        return httpx.Response(200, json={"meta": {}, "results": results})


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.delenv("CHATCDE_OPENALEX_API_KEY", raising=False)
    override = tmp_path / "openalex.yaml"
    override.write_text(
        "logging:\n  file: null\n"
        "openalex:\n  request_delay_s: 0\n  max_retries: 2\n  batch_size: 2\n"
        "  min_abstract_words: 30\n"
        f"data:\n  raw_dir: {tmp_path / 'raw'}\n"
        f"  catalogue: {tmp_path / 'cat.duckdb'}\n",
        encoding="utf-8",
    )
    cfg = load_config(override)
    records = [
        # publisher DOI has the abstract (mixed case: matching is case-blind)
        rec("r1", "10.7892/boris.1", publisher_doi="10.1000/Pub.1"),
        rec(
            "r2",
            "10.7892/boris.2",
            abstract="BORIS abstract wins.",
            publisher_doi="10.1000/pub.2",
        ),
        rec("r3", "10.48350/3"),  # only the BORIS DOI has an abstract
        rec("r4", "10.7892/boris.4"),  # abstract too short
        rec("r5", "10.7892/boris.5"),  # OpenAlex has no abstract
        rec("r6", None),  # no DOI at all
        rec("r7", "10.7892/boris.7"),  # unknown to OpenAlex
    ]
    con = catalogue.connect(cfg.data.catalogue)
    catalogue.replace_tables(
        con,
        {
            "records": records,
            "downloads": [
                ManifestEntry(
                    record_id="r1", status=DownloadStatus.RESTRICTED, fetched_at=T0
                ),
                ManifestEntry(
                    record_id="r3", status=DownloadStatus.DOWNLOADED, fetched_at=T0
                ),
            ],
        },
    )
    con.close()
    return cfg


def fake_works() -> list[dict[str, Any]]:
    oa = {"is_oa": True, "oa_status": "gold", "oa_url": "https://pub.example/1"}
    best = {
        "pdf_url": "https://pub.example/1.pdf",
        "landing_page_url": "https://pub.example/1",
        "license": "cc-by",
        "version": "publishedVersion",
    }
    boris_best = {"pdf_url": None, "landing_page_url": "https://boris.unibe.ch/3/"}
    return [
        work(
            "10.1000/pub.1", "Abstract: " + LONG, open_access=oa, best_oa_location=best
        ),
        work("10.7892/boris.1", "Other text " + LONG),
        work("10.1000/pub.2", "Ignored " + LONG),
        work(
            "10.48350/3",
            LONG,
            open_access={"is_oa": True, "oa_status": "green"},
            best_oa_location=boris_best,
        ),
        work("10.7892/boris.4", "Too short to be an abstract."),
        work("10.7892/boris.5"),
    ]


def client_for(cfg: Config, fake: FakeOpenAlex) -> OpenAlexClient:
    return OpenAlexClient(
        cfg.openalex, transport=httpx.MockTransport(fake), sleep=lambda s: None
    )


def abstracts(cfg: Config) -> dict[str, tuple[str | None, str | None]]:
    con = duckdb.connect(str(cfg.data.catalogue), read_only=True)
    try:
        rows = con.execute(
            "SELECT record_id, abstract, abstract_source FROM records"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def links(cfg: Config) -> list[dict[str, str]]:
    path = openalex_dir(cfg.data.raw_dir) / "oa_links.csv"
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- pure helpers ------------------------------------------------------------


def test_abstract_from_index_restores_word_order_and_repeats():
    inv = {"the": [0, 3], "cat": [1], "saw": [2], "dog": [4]}
    assert abstract_from_index(inv) == "the cat saw the dog"
    assert abstract_from_index(None) is None
    assert abstract_from_index({}) is None


@pytest.mark.parametrize(
    ("text", "out"),
    [
        ("Abstract: The study", "The study"),
        ("ABSTRACTTerrestrial ecosystems", "Terrestrial ecosystems"),
        ("Zusammenfassung  Die  Studie", "Die Studie"),
        ("Abstracts of papers", "Abstracts of papers"),
    ],
)
def test_clean_abstract_strips_label(text: str, out: str):
    assert clean_abstract(text, 0) == out


def test_clean_abstract_rejects_short_text():
    assert clean_abstract("one two three", 4) is None
    assert clean_abstract("one two three four", 4) == "one two three four"


def test_doi_key_normalises_prefix_and_case():
    assert doi_key("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert doi_key("https://dx.doi.org/10.1000/x") == "10.1000/x"
    assert doi_key("  ") is None
    assert doi_key(None) is None


def test_oa_link_prefers_pdf_and_marks_boris():
    w = work(
        "10.1/x",
        open_access={"is_oa": True, "oa_status": "green", "oa_url": "https://a/x"},
        best_oa_location={"pdf_url": "https://a/x.pdf", "landing_page_url": None},
    )
    link = oa_link(w, HOSTS, PREFIXES)
    assert link and link.pdf_url == "https://a/x.pdf" and not link.host_is_boris

    w = work(
        "10.1/y",
        open_access={"is_oa": True, "oa_url": "https://doi.org/10.7892/boris.9"},
    )
    link = oa_link(w, HOSTS, PREFIXES)
    assert link and link.landing_url.endswith("boris.9") and link.host_is_boris
    assert oa_link(work("10.1/z"), HOSTS, PREFIXES) is None


# --- client ------------------------------------------------------------------


def test_retries_429_and_5xx_then_4xx_is_an_error(cfg: Config):
    fake = FakeOpenAlex(fake_works())
    fake.fail = {1: 429, 2: 503}
    pauses: list[float] = []
    client = OpenAlexClient(
        cfg.openalex, transport=httpx.MockTransport(fake), sleep=pauses.append
    )
    assert client.works_by_doi(["10.1000/pub.1"])["results"]
    assert len(fake.calls) == 3 and pauses == [0, 2.0, 0, 4.0, 0]

    fake.fail = {4: 400}
    with pytest.raises(OpenAlexError, match="HTTP 400"):
        client.works_by_doi(["10.1000/pub.1"])
    assert len(fake.calls) == 4


def test_api_key_header_only_when_set(cfg: Config, monkeypatch: pytest.MonkeyPatch):
    fake = FakeOpenAlex([])
    client_for(cfg, fake).works_by_doi(["10.1/x"])
    assert "authorization" not in fake.calls[-1].headers
    monkeypatch.setenv("CHATCDE_OPENALEX_API_KEY", "secret")
    client_for(cfg, fake).works_by_doi(["10.1/x"])
    assert fake.calls[-1].headers["authorization"] == "Bearer secret"


# --- fetch -------------------------------------------------------------------


def test_fetch_batches_and_resumes(cfg: Config):
    raw = cfg.data.raw_dir
    dois = [f"10.1/{i}" for i in range(5)]
    fake = FakeOpenAlex([])
    fake.fail = {2: 400}  # second batch fails
    with pytest.raises(OpenAlexError):
        fetch_works(client_for(cfg, fake), raw, dois, batch_size=2)
    state, n = fetch_works(client_for(cfg, fake), raw, dois, batch_size=2)
    assert n == 3 and len(state.dois_done) == 5 and state.batches == 3
    sizes = [len(c.url.params["filter"].split("|")) for c in fake.calls]
    assert sizes == [2, 2, 2, 1]
    _, n = fetch_works(client_for(cfg, fake), raw, dois, batch_size=2)
    assert n == 0 and len(fake.calls) == 4


# --- the whole stage ---------------------------------------------------------


def test_fills_only_missing_abstracts_and_lists_links(cfg: Config):
    fake = FakeOpenAlex(fake_works())
    s = harvest_openalex(cfg, client=client_for(cfg, fake))
    a = abstracts(cfg)
    assert a["r1"] == (LONG, "abstract_openalex")  # publisher DOI, label stripped
    assert a["r2"] == ("BORIS abstract wins.", "abstract_boris")
    assert a["r3"] == (LONG, "abstract_openalex")
    assert a["r4"] == a["r5"] == a["r6"] == a["r7"] == (None, None)

    assert s.records == 7 and s.records_with_doi == 6 and s.with_publisher_doi == 2
    assert s.dois_known == 8 and s.dois_looked_up == 8 and s.dois_found == 6
    assert s.requests == 4  # 8 DOIs, batch size 2
    assert s.missing_abstract_before == 6 and s.abstract_filled == 2
    assert s.abstract_filled_by_doi == {"publisher": 1, "boris": 1}
    assert s.abstract_rejected_short == 1 and s.abstract_none_in_openalex == 1
    assert s.missing_abstract_no_doi == 1 and s.missing_abstract_not_found == 1
    assert s.missing_abstract_after == 4
    assert (openalex_dir(cfg.data.raw_dir) / "openalex_summary.json").exists()

    by_id = {x["record_id"]: x for x in links(cfg)}
    assert set(by_id) == {"r1", "r3"}
    assert by_id["r1"]["pdf_url"] == "https://pub.example/1.pdf"
    assert by_id["r1"]["host_is_boris"] == "False"
    assert by_id["r1"]["boris_download_status"] == "restricted"
    assert by_id["r3"]["host_is_boris"] == "True"
    assert s.oa_links == 2 and s.oa_links_outside_boris == 1
    assert s.oa_links_outside_boris_without_boris_file == 1


def test_rerun_is_idempotent_and_makes_no_requests(cfg: Config):
    fake = FakeOpenAlex(fake_works())
    harvest_openalex(cfg, client=client_for(cfg, fake))
    before = abstracts(cfg)
    s = harvest_openalex(cfg, client=client_for(cfg, fake))
    assert s.requests == 0 and s.dois_looked_up_this_run == 0
    assert abstracts(cfg) == before
    assert len(fake.calls) == 4


def test_offline_with_higher_threshold_removes_earlier_fills(cfg: Config):
    harvest_openalex(cfg, client=client_for(cfg, FakeOpenAlex(fake_works())))
    stricter = cfg.model_copy(
        update={"openalex": cfg.openalex.model_copy(update={"min_abstract_words": 100})}
    )
    s = harvest_openalex(stricter, offline=True)
    assert s.abstract_filled == 0 and s.requests == 0
    a = abstracts(cfg)
    assert a["r1"] == (None, None)
    assert a["r2"] == ("BORIS abstract wins.", "abstract_boris")


def test_catalogue_from_before_publisher_doi_needs_rebuild(cfg: Config):
    con = catalogue.connect(cfg.data.catalogue)
    con.execute("ALTER TABLE records DROP COLUMN publisher_doi")
    con.close()
    with pytest.raises(HarvestError, match="harvest metadata --offline"):
        harvest_openalex(cfg, client=client_for(cfg, FakeOpenAlex(fake_works())))


def test_offline_without_cache_and_offline_force_fail(cfg: Config):
    with pytest.raises(HarvestError, match="without --offline"):
        harvest_openalex(cfg, offline=True)
    with pytest.raises(HarvestError, match="cannot be combined"):
        harvest_openalex(cfg, offline=True, force=True)


def test_cli_offline_without_cache_exits_1(cfg: Config, tmp_path: Path):
    result = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "openalex.yaml"),
            "harvest",
            "openalex",
            "--offline",
        ],
    )
    assert result.exit_code == 1
    assert "without --offline" in result.output
