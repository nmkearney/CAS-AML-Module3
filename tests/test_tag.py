"""Tests for ``chatcde tag`` (issue #15). No network: a fake endpoint.

The fake answers each batch's JSON schema (sent as ``response_format``) with the
first allowed code of every dimension and a quote from the record's title, so
the quotes are found in the metadata. Records whose title is in ``bad_titles``
get an answer that never validates, and ``invented`` titles get quotes that are
not in the text.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.schemas import Dimension, Provenance, Tag, TextSource
from chatcde.store.endpoint import EndpointClient
from chatcde.tag import tables
from chatcde.tag.answer import OTHER, batch_model, codes_for
from chatcde.tag.derive import derive, publish
from chatcde.tag.extract import RecordText
from chatcde.tag.llm import load_prompt
from chatcde.tag.prompt import split_template, system_prompt
from chatcde.tag.regions import GLOBAL_FLAG, PARTNER_COUNTRY, RegionMap, load_countries
from chatcde.tag.run import locate, scope_ids, tag_records, to_tags
from chatcde.tag.sample import draw_pilot
from chatcde.tag.vocabulary import load_vocabulary

# --- fakes ----------------------------------------------------------------------


def _first_code(schema: dict[str, Any], ref: str) -> tuple[str, bool]:
    """First enum value of an item definition, and whether it has a role field."""
    item = schema["$defs"][ref.split("/")[-1]]
    return item["properties"]["term"]["enum"][0], "role" in item["properties"]


class FakeLLM:
    """``httpx.MockTransport`` handler answering tagging requests."""

    def __init__(self) -> None:
        self.chat: list[dict[str, Any]] = []
        self.bad_titles: set[str] = set()
        self.invented: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.chat.append(body)
        user = body["messages"][1]["content"]
        title = user.splitlines()[0].removeprefix("Title: ")
        schema = body["response_format"]["json_schema"]["schema"]
        if title in self.bad_titles:
            content = "not json"
        else:
            quote = (
                "words that are nowhere in the input"
                if title in self.invented
                else " ".join(title.split()[:6])
            )
            answer: dict[str, list[dict[str, Any]]] = {}
            for dim, prop in schema["properties"].items():
                code, has_role = _first_code(schema, prop["items"]["$ref"])
                item: dict[str, Any] = {
                    "term": code,
                    "evidence": quote,
                    "strength": "explicit",
                }
                defs = schema["$defs"][prop["items"]["$ref"].split("/")[-1]]
                if "other_label" in defs["properties"]:
                    item["other_label"] = None
                if has_role:
                    item["role"] = "studied"
                answer[dim] = [item, dict(item)]  # a duplicate, dropped by code
            content = json.dumps(answer)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}]
            },
        )


def make_client(fake: FakeLLM) -> EndpointClient:
    return EndpointClient(
        "https://llm.test/v1",
        "secret",
        timeout_s=5,
        max_retries=0,
        transport=httpx.MockTransport(fake),
        sleep=lambda _: None,
    )


@pytest.fixture
def db(fixture_db: Path, tmp_path: Path) -> Path:
    """A writable copy of the fixture catalogue."""
    path = tmp_path / "catalogue.duckdb"
    shutil.copy(fixture_db, path)
    return path


def make_cfg(tmp_path: Path, db: Path, **tagging: Any) -> tuple[Config, Path]:
    override = tmp_path / "override.yaml"
    override.write_text(
        yaml.safe_dump(
            {
                "data": {"catalogue": str(db)},
                "logging": {"file": None},
                "tagging": {"pilot": {"size": 6, "seed": 1, "floor": 0}, **tagging},
            }
        ),
        encoding="utf-8",
    )
    return load_config(override), override


@pytest.fixture(scope="module")
def cfg0() -> Config:
    return load_config()


@pytest.fixture(scope="module")
def vocab(cfg0: Config) -> dict[str, Any]:
    return load_vocabulary(cfg0.vocab.file)


@pytest.fixture(scope="module")
def countries(cfg0: Config) -> dict[str, Any]:
    return load_countries(cfg0.tagging.m49_file)


# --- regions --------------------------------------------------------------------


def test_region_terms(vocab: dict[str, Any], countries: dict[str, Any]):
    rm = RegionMap(countries, vocab)
    assert rm.terms("MDG") == ["Eastern Africa", "CDE-EAF", PARTNER_COUNTRY]
    assert rm.terms("ECU") == ["South America", "CDE-SAM"]
    assert rm.terms("NPL") == ["Southern Asia", PARTNER_COUNTRY]  # in no partner region
    assert rm.terms("CHE") == ["Western Europe", "CDE-EUR"]
    assert rm.terms("VNM") == ["South-eastern Asia", "CDE-MSEA"]  # by country list
    assert rm.terms("CAN") == ["Northern America"]
    assert rm.terms(GLOBAL_FLAG) == [] and rm.terms("XXX") == []
    assert len(countries) > 240


# --- schema ---------------------------------------------------------------------


def test_batch_model_offers_only_live_codes(
    cfg0: Config, vocab: dict[str, Any], countries: dict[str, Any]
):
    method = codes_for("method", vocab, countries)
    live = [t["code"] for t in vocab["method"]["terms"]]
    assert method == [*live, OTHER]
    assert "MET-futures-methods" not in method  # retired
    assert not any(c.startswith("MET-G-") for c in method)  # groups are not terms
    assert OTHER not in codes_for("sub_area", vocab, countries)
    assert {"CHE", GLOBAL_FLAG} <= set(codes_for("country", vocab, countries))
    assert codes_for("sdg", vocab, countries) == [str(n) for n in range(1, 18)]

    s = cfg0.tagging
    model = batch_model("who_what", s.batches["who_what"], vocab, countries, s)
    ok = {
        "system": [],
        "actors": [
            {
                "term": "ACT-women",
                "evidence": "q",
                "strength": "weak",
                "other_label": None,
                "role": "studied",
            }
        ],
        "justice_lens": [],
        "sdg": [{"term": "15", "evidence": "q", "strength": "explicit"}],
        "output_type": [],
    }
    model.model_validate(ok)
    for bad in (
        {**ok, "sdg": [{"term": "18", "evidence": "q", "strength": "explicit"}]},
        {**ok, "actors": [{**ok["actors"][0], "role": None}]},
        {
            **ok,
            "sdg": [
                {"term": str(n), "evidence": "q", "strength": "weak"}
                for n in (1, 2, 3, 4)
            ],
        },  # more than 3
        {**ok, "extra": []},
    ):
        with pytest.raises(ValidationError):
            model.model_validate(bad)


# --- prompt ---------------------------------------------------------------------


def test_system_prompt_lists_the_batch(cfg0: Config, vocab: dict[str, Any]):
    s = cfg0.tagging
    template = load_prompt(cfg0.prompts.dir, s.prompt)
    head, rules = split_template(template)
    assert "{{dimensions}}" in head
    assert set(rules) == {d for dims in s.batches.values() for d in dims}

    text = system_prompt(template, s.batches["how_where"], vocab, s)
    assert "## method (at most 5 tags)" in text and "## actors" not in text
    assert "#### Participatory and transdisciplinary" in text  # group heading
    assert "- MET-case-study — Case study" in text
    assert "- other — none of the above fits" in text
    assert "{{" not in text

    strategy = system_prompt(template, s.batches["strategy"], vocab, s)
    assert "#### SL: Sustainable land systems" in strategy
    assert "#### Cross-cutting" in strategy  # additional sub-areas by impact area
    with pytest.raises(ValueError, match="no '### region' rules"):
        system_prompt(template, ["region"], vocab, s)


# --- answers to tags ------------------------------------------------------------


def _record() -> RecordText:
    return RecordText(
        "r1",
        "Title: Soil erosion in Nepal\n\nAbstract:\nWe interviewed farmers.",
        "abstract,fulltext",
        metadata="Title: Soil erosion in Nepal",
        abstract="We interviewed farmers about terrace maintenance.",
        abstract_source="abstract_openalex",
        fulltext="A household survey of 120 farms in the hills.",
    )


def test_locate_finds_the_part_a_quote_comes_from():
    rec = _record()
    assert (
        locate("interviewed farmers about terrace", rec) is TextSource.ABSTRACT_OPENALEX
    )
    assert locate("household survey of 120 farms", rec) is TextSource.FULLTEXT
    assert locate("Soil erosion in Nepal", rec) is TextSource.METADATA
    assert locate("something else entirely here", rec) is None


def test_to_tags_maps_strength_checks_quotes_and_drops_repeats(
    cfg0: Config, vocab: dict[str, Any], countries: dict[str, Any]
):
    s = cfg0.tagging
    model = batch_model("how_where", s.batches["how_where"], vocab, countries, s)
    item = {"other_label": None}
    answer = model.model_validate(
        {
            "method": [
                {
                    **item,
                    "term": "MET-household-and-other-surveys",
                    "evidence": "household survey of 120 farms",
                    "strength": "explicit",
                },
                {
                    **item,
                    "term": "MET-household-and-other-surveys",
                    "evidence": "household survey of 120 farms",
                    "strength": "weak",
                },
                {
                    **item,
                    "term": "MET-case-study",
                    "evidence": "not in the input at all",
                    "strength": "explicit",
                },
                {
                    **item,
                    "term": OTHER,
                    "evidence": "interviewed farmers about terrace",
                    "strength": "inferred",
                },  # other without a label: dropped
            ],
            "data_type": [],
            "setting": [],
            "scale": [],
            "country": [
                {
                    "term": "NPL",
                    "evidence": "Soil erosion in Nepal",
                    "strength": "explicit",
                }
            ],
        }
    )
    prov = Provenance(code_version="x", created_at="2026-11-09T00:00:00Z", model="m")
    rows, unverified = to_tags(
        answer,
        _record(),
        batch="how_where",
        dimensions=s.batches["how_where"],
        cfg=cfg0,
        provenance=prov,
        run="main",
    )
    got = {(r.dimension.value, r.term): r for r in rows}
    assert set(got) == {
        ("method", "MET-household-and-other-surveys"),
        ("method", "MET-case-study"),
        ("country", "NPL"),
    }
    assert got[("method", "MET-household-and-other-surveys")].confidence == 0.9
    assert (
        got[("method", "MET-household-and-other-surveys")].source is TextSource.FULLTEXT
    )
    capped = got[("method", "MET-case-study")]
    assert unverified == 1 and capped.confidence == s.unverified_cap
    assert capped.source is TextSource.FULLTEXT  # fallback: richest part
    assert got[("country", "NPL")].source is TextSource.METADATA


# --- runs -----------------------------------------------------------------------


def _ids(con: duckdb.DuckDBPyConnection, n: int) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT record_id FROM records ORDER BY record_id LIMIT ?", [n]
        ).fetchall()
    ]


def test_tag_records_resumes_and_retries(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    ids = _ids(con, 3)
    title = con.execute(
        "SELECT title FROM records WHERE record_id = ?", [ids[0]]
    ).fetchone()[0]
    fake = FakeLLM()
    fake.bad_titles.add(title)
    client = make_client(fake)

    s = tag_records(con, cfg, client, scope="ids", ids=ids)
    n_batches = len(cfg.tagging.batches)
    assert s.jobs == 3 * n_batches and s.failed == n_batches and s.done == 2 * n_batches
    assert len(fake.chat) == 2 * n_batches + 2 * n_batches  # bad answers: 2 attempts
    rows = con.execute("SELECT * FROM tag_runs").fetchall()
    assert rows and s.tags_by_dimension["method"] == 2  # duplicate dropped, 2 records
    cols = [c[0] for c in con.description]
    for row in rows:
        Tag.model_validate(
            {k: v for k, v in zip(cols, row, strict=True) if k in Tag.model_fields}
        )
    assert s.unverified_share == 0  # quotes from the title are found
    assert (
        s.unused_terms["method"]
        == len(
            codes_for(
                "method",
                load_vocabulary(cfg.vocab.file),
                load_countries(cfg.tagging.m49_file),
            )
        )
        - 2
    )  # 'other' is not counted; one code used

    fake.bad_titles.clear()
    again = tag_records(con, cfg, client, scope="ids", ids=ids)
    assert again.skipped == 2 * n_batches and again.done == n_batches  # error retried
    calls = len(fake.chat)
    third = tag_records(con, cfg, client, scope="ids", ids=ids)
    assert third.skipped == 3 * n_batches and len(fake.chat) == calls
    forced = tag_records(
        con, cfg, client, scope="ids", ids=ids[:1], batches=["strategy"], force=True
    )
    assert forced.done == 1 and forced.batches == ["strategy"]
    states = con.execute(
        "SELECT count(*), count(DISTINCT record_id) FROM tag_state WHERE status = 'ok'"
    ).fetchone()
    assert states == (3 * n_batches, 3)
    con.close()


def test_unverified_quotes_are_capped(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    ids = _ids(con, 1)
    title = con.execute(
        "SELECT title FROM records WHERE record_id = ?", [ids[0]]
    ).fetchone()[0]
    fake = FakeLLM()
    fake.invented.add(title)
    s = tag_records(
        con, cfg, make_client(fake), scope="ids", ids=ids, batches=["strategy"]
    )
    assert s.unverified_share == 1
    top = con.execute("SELECT max(confidence) FROM tag_runs").fetchone()[0]
    assert top == cfg.tagging.unverified_cap
    con.close()


def test_scope_and_bad_batch(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    assert (
        len(scope_ids(con, "all"))
        == con.execute("SELECT count(*) FROM records").fetchone()[0]
    )
    with pytest.raises(ValueError, match="not in the catalogue"):
        scope_ids(con, "ids", ["nope"])
    with pytest.raises(RuntimeError, match="no pilot"):
        scope_ids(con, "pilot")
    with pytest.raises(ValueError, match="unknown batch"):
        tag_records(con, cfg, make_client(FakeLLM()), scope="all", batches=["nope"])
    con.close()


def test_pilot_excludes_the_vocabulary_sample(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    tables.ensure(con, "vocab_sample")
    excluded = _ids(con, 10)
    con.executemany(
        "INSERT INTO vocab_sample VALUES "
        "(?, 'u|d|l', 'u', 'd', 'l', false, 13, 250, 3, now(), 'x')",
        [[i] for i in excluded],
    )
    s = draw_pilot(con, cfg)
    picked = {r[0] for r in con.execute("SELECT record_id FROM tag_pilot").fetchall()}
    assert s.sampled == len(picked) == 6 and not picked & set(excluded)
    total = con.execute("SELECT count(*) FROM records").fetchone()[0]
    assert s.eligible == total - 10  # metadata-only records are eligible too
    assert draw_pilot(con, cfg).reused
    assert set(scope_ids(con, "pilot")) == picked
    con.close()


# --- publish --------------------------------------------------------------------


def _tag(rid: str, dim: str, term: str, conf: float) -> Tag:
    return Tag(
        record_id=rid,
        dimension=Dimension(dim),
        term=term,
        confidence=conf,
        evidence="q",
        source=TextSource.METADATA,
        provenance=Provenance(code_version="x", created_at="2026-11-09T00:00:00Z"),
    )


def test_derive_region_and_impact_area(
    vocab: dict[str, Any], countries: dict[str, Any]
):
    asa = {t["impact_area"]: t["code"] for t in vocab["additional_sub_area"]["terms"]}
    tags = [
        _tag("r1", "country", "MDG", 0.9),
        _tag("r1", "country", "KEN", 0.6),
        _tag("r1", "sub_area", "SL-a", 0.6),
        _tag("r1", "sub_area", "SL-d", 0.9),
        _tag("r1", "additional_sub_area", asa["SG"], 0.6),
        _tag("r1", "additional_sub_area", asa["cross_cutting"], 0.9),
        _tag("r2", "country", GLOBAL_FLAG, 0.9),
    ]
    got = {
        (t.record_id, t.dimension.value, t.term): t
        for t in derive(tags, vocab, RegionMap(countries, vocab))
    }
    assert set(got) == {
        ("r1", "region", "Eastern Africa"),
        ("r1", "region", "CDE-EAF"),
        ("r1", "region", PARTNER_COUNTRY),
        ("r1", "impact_area", "SL"),
        ("r1", "impact_area", "SG"),
    }
    assert got[("r1", "region", "CDE-EAF")].confidence == 0.9  # highest origin
    assert got[("r1", "impact_area", "SL")].confidence == 0.9
    assert all(
        t.evidence is None and t.provenance.model == "derived" for t in got.values()
    )


def test_publish_replaces_tags(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    with pytest.raises(RuntimeError, match="no tags"):
        publish(con, cfg)
    ids = _ids(con, 2)
    tag_records(con, cfg, make_client(FakeLLM()), scope="ids", ids=ids)
    s = publish(con, cfg)
    assert s.records_tagged == 2 and s.llm_tags > 0
    rows = con.execute(
        "SELECT count(*), count(DISTINCT record_id) FROM tags"
    ).fetchone()
    assert rows == (s.llm_tags + s.region_tags + s.impact_area_tags, 2)  # fixtures gone
    assert s.impact_area_tags == 2  # first sub_area code (JE-a) -> JE, per record
    con.close()


# --- CLI ------------------------------------------------------------------------


def test_cli_tag(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from chatcde.store import endpoint

    fake = FakeLLM()
    real_init = endpoint.EndpointClient.__init__

    def init_with_fake(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = httpx.MockTransport(fake)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(endpoint.EndpointClient, "__init__", init_with_fake)
    _, override = make_cfg(tmp_path, db)
    runner = CliRunner()
    base = ["--config", str(override), "tag"]
    assert runner.invoke(app, base).exit_code == 2  # no scope chosen
    result = runner.invoke(app, [*base, "--pilot", "--batch", "strategy"])
    assert result.exit_code == 0, result.output
    for title in ("tag: pilot draw", "tag: run main (pilot)", "published run main"):
        assert title in result.output
    alt = runner.invoke(app, [*base, "--pilot", "--alternative", "--batch", "strategy"])
    assert alt.exit_code == 0, alt.output
    assert "tag: run alt (pilot)" in alt.output and "published" not in alt.output
    con = duckdb.connect(str(db), read_only=True)
    runs = dict(
        con.execute("SELECT run, count(*) FROM tag_runs GROUP BY run").fetchall()
    )
    assert set(runs) == {"main", "alt"}
    con.close()
