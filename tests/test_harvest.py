"""Tests for ``chatcde harvest metadata`` (issue #7), all offline.

The BORIS replies are built from the real item JSON in ``tests/fixtures/raw/``
and served through ``httpx.MockTransport``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.harvest import mapping
from chatcde.harvest.boris import BorisClient, BorisError, NotOnVPNError
from chatcde.harvest.metadata import (
    HarvestError,
    fetch_pages,
    harvest_metadata,
    load_state,
)

FIX = Path(__file__).parent / "fixtures"
RAW_ITEMS = sorted(FIX.glob("raw/*/item.json"))
JSON_HEADERS = {"content-type": "application/hal+json;charset=UTF-8"}


def load_items() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in RAW_ITEMS]


def jsonl(name: str) -> list[dict[str, Any]]:
    lines = (FIX / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def strip_cde(item: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``item`` with every CDE mention replaced by another institute."""
    item = copy.deepcopy(item)
    item["uuid"] = "00000000-0000-0000-0000-00000000c0de"
    for vs in item["metadata"].values():
        for v in vs:
            if mapping.CDE in (v.get("value") or ""):
                v["value"] = "Institute of Geography"
    return item


def with_uuid(item: dict[str, Any], uuid: str, **changes: Any) -> dict[str, Any]:
    item = copy.deepcopy(item)
    item["uuid"] = uuid
    item.update(changes)
    return item


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    override = tmp_path / "harvest.yaml"
    override.write_text(
        "logging:\n  file: null\n"
        "boris:\n  request_delay_s: 0\n  page_size: 10\n  max_retries: 2\n"
        f"data:\n  raw_dir: {tmp_path / 'raw'}\n"
        f"  catalogue: {tmp_path / 'cat.duckdb'}\n",
        encoding="utf-8",
    )
    return load_config(override)


class FakeBoris:
    """Serves a paged search over ``items``; records the pages requested."""

    def __init__(self, items: list[dict[str, Any]], page_size: int = 10) -> None:
        self.items = items
        self.size = page_size
        self.pages: list[int] = []
        self.fail_on: set[int] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/discover/search/objects"):
            return httpx.Response(200, headers=JSON_HEADERS, json={"_links": {}})
        page = int(request.url.params["page"])
        if page in self.fail_on:
            raise httpx.ConnectError("simulated network drop")
        self.pages.append(page)
        chunk = self.items[page * self.size : (page + 1) * self.size]
        n = len(self.items)
        return httpx.Response(
            200,
            headers=JSON_HEADERS,
            json={
                "_embedded": {
                    "searchResult": {
                        "page": {
                            "totalElements": n,
                            "totalPages": -(-n // self.size),
                            "number": page,
                            "size": self.size,
                        },
                        "_embedded": {
                            "objects": [
                                {"_embedded": {"indexableObject": it}} for it in chunk
                            ]
                        },
                    }
                }
            },
        )


def client_for(
    cfg: Config, handler: Any, sleeps: list[float] | None = None
) -> BorisClient:
    record = sleeps.append if sleeps is not None else (lambda s: None)
    return BorisClient(cfg.boris, transport=httpx.MockTransport(handler), sleep=record)


# --- mapping ----------------------------------------------------------------


def test_mapping_reproduces_fixture_records_and_authors():
    """The shared mapping gives the same rows as the hand-checked fixtures."""
    expected_records = {r["record_id"]: r for r in jsonl("records.jsonl")}
    expected_authors = [a for a in jsonl("authors.jsonl")]
    got_authors = []
    for item in load_items():
        rec = mapping.to_record(item, harvested_at=mapping_time()).model_dump(
            mode="json"
        )
        want = expected_records[item["uuid"]]
        for skip in ("harvested_at", "has_fulltext"):
            rec.pop(skip), want.pop(skip)
        assert rec == want, item["uuid"]
        got_authors += [a.model_dump(mode="json") for a in mapping.to_authors(item)]
    real = {i["uuid"] for i in load_items()}
    assert got_authors == [a for a in expected_authors if a["record_id"] in real]


def mapping_time():
    from datetime import UTC, datetime

    return datetime(2026, 11, 6, tzinfo=UTC)


def test_is_cde_uses_sponsorship_field():
    md = {
        "dc.description.sponsorship": [
            {"value": "Centre for Development and Environment (CDE) - Services"}
        ],
        "oairecerif.author.affiliation": [{"value": "Institute of Geography"}],
    }
    assert mapping.cde_link(md) == "explicit"
    assert mapping.cde_units(md) == ["Services"]
    assert mapping.cde_link(strip_cde(load_items()[0])["metadata"]) is None


def test_placeholders_are_empty():
    md = {"dc.title": [{"value": mapping.PLACEHOLDER}, {"value": " Real "}]}
    assert mapping.vals(md, "dc.title") == [None, "Real"]
    assert mapping.first(md, "dc.title") == "Real"


def test_normalise_doi():
    assert mapping.normalise_doi("https://doi.org/10.1/x") == "10.1/x"
    assert mapping.normalise_doi("doi:10.1/x") == "10.1/x"
    assert mapping.normalise_doi("") is None


# --- client -----------------------------------------------------------------


def test_html_reply_means_not_on_vpn(cfg: Config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>Temporary Access Restriction</html>",
        )

    with client_for(cfg, handler) as c, pytest.raises(NotOnVPNError, match="VPN"):
        c.preflight()


def test_retries_5xx_then_succeeds_and_waits_before_each_request(cfg: Config):
    replies = iter(
        [httpx.Response(503), httpx.Response(200, headers=JSON_HEADERS, json={})]
    )
    sleeps: list[float] = []
    with client_for(cfg, lambda r: next(replies), sleeps) as c:
        assert c.get_json(c.base) == {}
        assert c.requests == 2
    # delay, back-off, delay (delay is 0 in the test config, back-off is 2 s)
    assert sleeps == [0, 2.0, 0]


def test_gives_up_after_max_retries(cfg: Config):
    with client_for(cfg, lambda r: httpx.Response(502)) as c:
        with pytest.raises(BorisError, match="after 3 attempts"):
            c.get_json(c.base)


def test_4xx_is_an_error_not_retried(cfg: Config):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, headers=JSON_HEADERS, json={"message": "nope"})

    with client_for(cfg, handler) as c, pytest.raises(BorisError, match="404"):
        c.get_json(c.base)
    assert len(calls) == 1


# --- fetch / resume -----------------------------------------------------------


def test_fetch_resumes_after_interruption(cfg: Config):
    fake = FakeBoris(load_items())  # 30 items -> 3 pages of 10
    raw = cfg.data.raw_dir
    fake.fail_on = {1}
    with client_for(cfg, fake) as c, pytest.raises(BorisError):
        fetch_pages(c, cfg.boris, raw)
    assert fake.pages == [0]
    assert not load_state(raw).complete

    fake.fail_on = set()
    with client_for(cfg, fake) as c:
        state = fetch_pages(c, cfg.boris, raw)
    assert fake.pages == [0, 1, 2]  # page 0 was not fetched again
    assert state.complete and state.pages_done == [0, 1, 2]

    with client_for(cfg, fake) as c:
        fetch_pages(c, cfg.boris, raw)  # finished: nothing to do
        assert c.requests == 0
    with client_for(cfg, fake) as c:
        fetch_pages(c, cfg.boris, raw, force=True)
    assert fake.pages == [0, 1, 2, 0, 1, 2]


def test_changed_search_settings_need_force(cfg: Config, tmp_path: Path):
    fake = FakeBoris(load_items())
    with client_for(cfg, fake) as c:
        fetch_pages(c, cfg.boris, cfg.data.raw_dir)
    other = cfg.boris.model_copy(update={"page_size": 50})
    with client_for(cfg, fake) as c, pytest.raises(HarvestError, match="--force"):
        fetch_pages(c, other, cfg.data.raw_dir)


# --- build --------------------------------------------------------------------


def corpus() -> list[dict[str, Any]]:
    """The 30 fixture items plus one non-CDE, one dataset, one withdrawn, one dup."""
    items = load_items()
    base = items[0]
    dataset = with_uuid(base, "00000000-0000-0000-0000-0000000da7a5")
    dataset["metadata"]["dspace.entity.type"] = [{"value": "Dataset"}]
    return items + [
        strip_cde(base),
        dataset,
        with_uuid(base, "00000000-0000-0000-0000-00000000dead", withdrawn=True),
        copy.deepcopy(items[5]),  # same item on a later page
    ]


def table(cfg: Config, sql: str) -> list[tuple]:
    con = duckdb.connect(str(cfg.data.catalogue), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_build_filters_and_is_idempotent(cfg: Config):
    with client_for(cfg, FakeBoris(corpus())) as c:
        s = harvest_metadata(cfg, client=c)
    assert s.search_hits == 33  # the duplicate counted once
    assert s.records == 30
    assert s.dropped == {"not_cde": 1, "entity_type:Dataset": 1, "withdrawn": 1}
    assert s.authors == len(
        [
            a
            for a in jsonl("authors.jsonl")
            if a["record_id"] in {i["uuid"] for i in load_items()}
        ]
    )
    before = table(cfg, "SELECT * FROM records ORDER BY record_id")

    s2 = harvest_metadata(cfg, offline=True)
    assert s2.records == 30 and s2.removed == 0
    assert table(cfg, "SELECT * FROM records ORDER BY record_id") == before
    assert table(cfg, "SELECT count(*) FROM authors") == [(s.authors,)]
    summary = json.loads(
        (cfg.data.raw_dir / "boris" / "harvest_summary.json").read_text()
    )
    assert summary["records"] == 30 and summary["code_version"]


def test_build_keeps_work_of_later_stages(cfg: Config):
    items = load_items()
    no_abstract = next(
        i for i in items if not mapping.first(i["metadata"], "dc.description.abstract")
    )
    uid = no_abstract["uuid"]
    with client_for(cfg, FakeBoris(items)) as c:
        harvest_metadata(cfg, client=c)
    con = duckdb.connect(str(cfg.data.catalogue))
    con.execute(
        "UPDATE records SET has_fulltext = true, abstract = 'From OpenAlex.', "
        "abstract_source = 'abstract_openalex' WHERE record_id = ?",
        [uid],
    )
    con.close()

    harvest_metadata(cfg, offline=True)
    assert table(
        cfg,
        "SELECT has_fulltext, abstract, abstract_source FROM records "
        f"WHERE record_id = '{uid}'",
    ) == [(True, "From OpenAlex.", "abstract_openalex")]


def test_offline_without_fetch_fails_clearly(cfg: Config):
    with pytest.raises(HarvestError, match="VPN"):
        harvest_metadata(cfg, offline=True)


# --- CLI ----------------------------------------------------------------------


def test_cli_not_on_vpn_exits_2(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import chatcde.harvest.metadata as md

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html/>"
        )

    real = md.BorisClient
    monkeypatch.setattr(
        md,
        "BorisClient",
        lambda bcfg: real(
            bcfg, transport=httpx.MockTransport(html), sleep=lambda s: None
        ),
    )
    result = CliRunner().invoke(
        app, ["--config", str(tmp_path / "harvest.yaml"), "harvest", "metadata"]
    )
    assert result.exit_code == 2
    assert "VPN" in result.output
    assert not cfg.data.catalogue.exists()


# --- CDE rule and ORCIDs (decision 0008) ---------------------------------------


def org_linked(item: dict[str, Any], uuid: str, sponsor: str, authors: list[str]):
    """A legacy-style record: CDE only in dc.relation.organization."""
    item = strip_cde(item)
    item["uuid"] = uuid
    md = item["metadata"]
    md[mapping.ORG_LINK_KEY] = [
        {"value": "Centre for Development and Environment (CDE)"}
    ]
    md["dc.description.sponsorship"] = [{"value": sponsor}]
    md["dc.contributor.author"] = [{"value": a, "authority": None} for a in authors]
    for k in mapping.AFFILIATION_KEYS:
        md[k] = [{"value": mapping.PLACEHOLDER} for _ in authors]
    return item


def test_org_link_kept_only_when_confirmed(cfg: Config):
    items = load_items()
    known = next(a.name for i in items for a in mapping.to_authors(i) if a.is_cde)
    base = items[0]
    extra = [
        org_linked(base, "00000000-0000-0000-0000-0000000000a1",
                   mapping.NCCR_SPONSOR, ["Nobody, Known"]),
        org_linked(base, "00000000-0000-0000-0000-0000000000a2",
                   "Institute of Plant Sciences (IPS)", [known.upper()]),
        org_linked(base, "00000000-0000-0000-0000-0000000000a3",
                   "Institute of Plant Sciences (IPS)", ["Nobody, Known"]),
    ]  # fmt: skip
    assert mapping.cde_link(extra[0]["metadata"]) == "org_link"
    with client_for(cfg, FakeBoris(items + extra)) as c:
        s = harvest_metadata(cfg, client=c)
    assert s.by_cde_link == {"explicit": 30, "org_link_nccr": 1, "org_link_person": 1}
    assert s.dropped["org_link_unconfirmed"] == 1
    ids = {r[0] for r in table(cfg, "SELECT record_id FROM records")}
    assert "00000000-0000-0000-0000-0000000000a3" not in ids


def test_orcid_follows_person_authority_not_position():
    item = json.loads(
        (FIX / "raw/0d741fd9-b15d-4057-b6f9-22395f89b413/item.json").read_text()
    )
    by_name = {a.name: a.orcid for a in mapping.to_authors(item)}
    # cris.virtual.orcid lists this ORCID at place 3, but its source person is
    # Trechsel (place 2 among the authors); da Silva-Trolliet has none.
    assert by_name["Trechsel, Lilian Julia"] == "0000-0001-8734-6394"
    assert by_name["da Silva-Trolliet, Tamara Rebecca"] is None


def test_fixture_orcids_are_consistent_per_person():
    """One ORCID never belongs to two different surnames in the fixtures.

    Umlauts are folded, since BORIS spells some names both ways (Zähringer and
    Zaehringer share one ORCID).
    """
    fold = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
    seen: dict[str, set[str]] = {}
    for a in jsonl("authors.jsonl"):
        if a["orcid"]:
            surname = a["name"].split(",")[0].lower().translate(fold)
            seen.setdefault(a["orcid"], set()).add(surname)
    assert {o: s for o, s in seen.items() if len(s) > 1} == {}
