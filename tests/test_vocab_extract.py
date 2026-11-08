"""Tests for `chatcde vocab extract` (issue #13). No network: a fake endpoint.

The fake endpoint answers ``/chat/completions`` by the schema name in
``response_format`` (``ExtractedLabels`` or ``Proposal``) and ``/embeddings``
with bag-of-words vectors, so labels that share words cluster together.
"""

from __future__ import annotations

import json
import re
import shutil
import zlib
from pathlib import Path
from typing import Any

import duckdb
import httpx
import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.store.endpoint import EndpointClient
from chatcde.tag import tables
from chatcde.tag.extract import (
    ExtractedLabels,
    build_texts,
    evidence_in,
    label_sample,
    normalise,
)
from chatcde.tag.llm import LLMReplyError, chat_json, fill_prompt
from chatcde.tag.propose import average_linkage, propose_terms, resolve_term
from chatcde.tag.sample import (
    Candidate,
    allocate,
    build_sample,
    decade_of,
    draw,
    language_of,
    unit_of,
)
from chatcde.tag.vocabulary import (
    flatten_terms,
    load_vocabulary,
    seed_terms,
    unit_codes,
)

DIMS = 32
THEMES = ["soil erosion", "Soil Erosion", "soil erosion control", "urban gardening"]


# --- fakes ----------------------------------------------------------------


def fake_vector(text: str) -> list[float]:
    """Bag-of-words vector: each lower-cased word adds 1 to a hashed dimension."""
    v = [0.0] * DIMS
    for word in re.findall(r"\w+", text.lower()):
        v[zlib.crc32(word.encode()) % DIMS] += 1.0
    v[0] += 0.01
    return v


def title_of(user: str) -> str:
    """The title line of a record message."""
    return user.splitlines()[0].removeprefix("Title: ")


class FakeLLM:
    """``httpx.MockTransport`` handler for chat completions and embeddings."""

    def __init__(self) -> None:
        self.chat: list[dict[str, Any]] = []
        self.embed: list[dict[str, Any]] = []
        self.bad_titles: set[str] = set()  # records whose answer never validates

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            self.embed.append(body)
            data = [
                {"index": i, "embedding": fake_vector(t)}
                for i, t in enumerate(body["input"])
            ]
            return httpx.Response(200, json={"data": data})
        self.chat.append(body)
        name = body["response_format"]["json_schema"]["name"]
        user = body["messages"][1]["content"]
        if name == "ExtractedLabels":
            content = self.labels(user)
        else:
            content = self.proposal(user)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}]
            },
        )

    def labels(self, user: str) -> str:
        title = title_of(user)
        if title in self.bad_titles:
            return "not json"
        quote = " ".join(title.split()[:6])
        theme = THEMES[zlib.crc32(title.encode()) % len(THEMES)]
        item = {"label": theme, "evidence": quote}
        return json.dumps(
            {
                "methods": [
                    {"label": "household survey", "evidence": quote},
                    {"label": "Household  survey", "evidence": quote},  # duplicate
                ],
                "themes": [item],
                "data_types": [
                    {"label": "interview data", "evidence": "made up words here"}
                ],
                "settings": [],
                "systems": [],
                "actors": [
                    {"label": "farmers (studied)", "evidence": quote, "role": "studied"}
                ],
            }
        )

    def proposal(self, user: str) -> str:
        first = user.splitlines()[1].removeprefix("- ").rsplit(" (", 1)[0]
        existing = "Interviews and focus groups" if "survey" in first else "Not a term"
        return json.dumps(
            {
                "label": first.title(),
                "definition": f"Work about {first}.",
                "action": "merge_into_existing",
                "existing_term": existing,
                "suggested_dimension": "sub_area",
                "note": None,
                "n_records": 9999,  # extra keys (counts) are ignored
            }
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


def make_cfg(tmp_path: Path, db: Path, **extract: Any) -> tuple[Config, Path]:
    """Config using ``db``, outputs in ``tmp_path``; returns (cfg, override file)."""
    settings = {
        "sample_size": 12,
        "stratum_floor": 0,
        "output": str(tmp_path / "candidates.yaml"),
        "sample_csv": str(tmp_path / "sample.csv"),
        "min_records": 2,
        **extract,
    }
    override = tmp_path / "override.yaml"
    override.write_text(
        yaml.safe_dump(
            {
                "data": {"catalogue": str(db)},
                "logging": {"file": None},
                "embedding": {"dimensions": None},
                "vocab_extract": settings,
            }
        ),
        encoding="utf-8",
    )
    return load_config(override), override


# --- vocabulary ---------------------------------------------------------------


def test_flatten_terms_handles_every_shape():
    assert flatten_terms(["A", "B"]) == ["A", "B"]
    assert flatten_terms([{"code": "O1", "label": "One"}, {"label": "Two"}]) == [
        "O1: One",
        "Two",
    ]
    assert flatten_terms({"g1": ["A"], "g2": ["B", "C"]}) == ["A", "B", "C"]
    assert flatten_terms({"roles": ["x"], "groups": {"g": ["Pastoralists"]}}) == [
        "Pastoralists"
    ]
    assert flatten_terms({"standard": "ISO"}) == []


def test_seed_terms_and_unit_codes_from_the_real_vocabulary():
    cfg = load_config()
    vocab = load_vocabulary(cfg.vocab.file)
    assert "Case study" in seed_terms(vocab, "method")
    assert "Pastoralists" in seed_terms(vocab, "actors")
    assert any(t.startswith("SL-d:") for t in seed_terms(vocab, "sub_area"))
    assert unit_codes(vocab)["Sustainable Land Systems"] == "SL"


# --- sampling -------------------------------------------------------------


def test_strata_helpers():
    assert decade_of(2014, 2000) == "2010s"
    assert decade_of(1987, 2000) == "<2000"
    assert decade_of(None, 2000) == "unknown"
    codes = {"Sustainable Land Systems": "SL"}
    assert unit_of(["Sustainable Land Systems", "Services"], codes) == "SL"
    assert unit_of(["Services"], codes) == "services"
    assert unit_of([], codes) == "none"
    assert language_of(["de"], ["en", "de"]) == "de"
    assert language_of(["me"], ["en", "de"]) == "other"
    assert language_of(None, ["en", "de"]) == "other"


def test_allocate_floors_then_proportional():
    sizes = {"a": 100, "b": 10, "c": 2}
    alloc = allocate(sizes, 20, 3)
    assert sum(alloc.values()) == 20
    assert alloc["c"] == 2 and alloc["b"] >= 3 and alloc["a"] > alloc["b"]


def test_allocate_edge_cases():
    assert allocate({"a": 3, "b": 2}, 100, 1) == {"a": 3, "b": 2}  # fewer than target
    assert allocate({"a": 5, "b": 5, "c": 5}, 2, 1) == {"a": 1, "b": 1, "c": 1}
    assert allocate({}, 10, 3) == {}


def _candidates(n: int) -> list[Candidate]:
    return [
        Candidate(f"r{i:03d}", ["SL", "none"][i % 2], "2010s", "en", i % 3 == 0)
        for i in range(n)
    ]


def test_draw_is_seeded_and_prefers_page_text():
    cands = _candidates(60)
    a = draw(cands, 10, 2, seed=1)
    assert [c.record_id for c in a] == [c.record_id for c in draw(cands, 10, 2, seed=1)]
    assert [c.record_id for c in a] != [c.record_id for c in draw(cands, 10, 2, seed=2)]
    assert len(a) == 10 and all(c.has_pages for c in a)  # 20 with pages, 10 needed


def test_build_sample_on_fixture(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    s = build_sample(con, cfg)
    assert s.publications == 34 and not s.reused
    assert s.sampled == 12 and s.eligible < s.publications
    ids = {r[0] for r in con.execute("SELECT record_id FROM vocab_sample").fetchall()}
    assert "fixture-synthetic-02" not in ids  # no abstract, no pages: not eligible
    assert (tmp_path / "sample.csv").read_text().count("\n") == 13

    again = build_sample(con, cfg)
    assert again.reused and again.sampled == 12
    cfg2, _ = make_cfg(tmp_path, db, seed=99)
    assert not build_sample(con, cfg2).reused
    assert build_sample(con, cfg2, force=True).sampled == 12
    con.close()


# --- input text and evidence ---------------------------------------------------


def test_build_texts_with_and_without_pages(db: Path):
    con = duckdb.connect(str(db))
    rid = con.execute(
        "SELECT record_id FROM records WHERE abstract IS NOT NULL AND has_fulltext "
        "AND record_id IN (SELECT record_id FROM pages) LIMIT 1"
    ).fetchone()[0]
    t = build_texts(con, [rid], max_fulltext_chars=500, max_input_chars=5000)[rid]
    assert t.text.startswith("Title: ") and t.sources == "abstract,fulltext"
    assert "Full text (first pages):" in t.text
    short = build_texts(con, [rid], max_fulltext_chars=500, max_input_chars=600)[rid]
    assert len(short.text) == 600
    con.execute("DROP TABLE pages")
    t2 = build_texts(con, [rid], max_fulltext_chars=500, max_input_chars=5000)[rid]
    assert t2.sources == "abstract" and "Full text" not in t2.text
    con.close()


def test_evidence_in_ignores_line_break_hyphens_and_case():
    text = "Soil reflectance spec- trometry for rapid and cheap laborato ry analysis."
    assert evidence_in("soil reflectance spectrometry for rapid", text)
    assert evidence_in("Soil reflectance ... cheap laboratory analysis", text)
    assert not evidence_in("soil reflectance by satellites", text)
    assert not evidence_in("", text)
    assert evidence_in("cheap", text)  # short: must occur as a whole
    assert not evidence_in("dear", text)


def test_normalise():
    assert normalise("  Household­  Survey ") == "household survey"


# --- structured output ---------------------------------------------------------


def _chat_client(*contents: str, finish: str = "stop") -> tuple[EndpointClient, list]:
    seen: list[dict] = []
    answers = list(contents)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = answers.pop(0) if answers else ""
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": finish}]
            },
        )

    client = EndpointClient(
        "https://llm.test/v1",
        None,
        timeout_s=5,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    return client, seen


EMPTY = json.dumps(
    {
        k: []
        for k in ("methods", "themes", "data_types", "settings", "systems", "actors")
    }
)


def test_chat_json_sends_schema_and_extra_body():
    client, seen = _chat_client("```json\n" + EMPTY + "\n```")
    out = chat_json(
        client,
        model="m",
        system="s",
        user="u",
        schema=ExtractedLabels,
        max_tokens=100,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    assert out.methods == []
    body = seen[0]
    assert body["response_format"]["json_schema"]["name"] == "ExtractedLabels"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.0 and body["max_tokens"] == 100


def test_chat_json_retries_invalid_then_fails():
    client, seen = _chat_client("nope", EMPTY)
    chat_json(
        client, model="m", system="s", user="u", schema=ExtractedLabels, max_tokens=10
    )
    assert len(seen) == 2
    client, _ = _chat_client("nope", "{}")
    with pytest.raises(LLMReplyError, match="does not fit"):
        chat_json(
            client,
            model="m",
            system="s",
            user="u",
            schema=ExtractedLabels,
            max_tokens=10,
        )
    client, _ = _chat_client(EMPTY, EMPTY, finish="length")
    with pytest.raises(LLMReplyError, match="cut off"):
        chat_json(
            client,
            model="m",
            system="s",
            user="u",
            schema=ExtractedLabels,
            max_tokens=10,
        )


def test_fill_prompt():
    assert fill_prompt("a {{x}} {json}", {"x": 3}) == "a 3 {json}"
    with pytest.raises(KeyError, match="y"):
        fill_prompt("{{y}}", {})


# --- label step -------------------------------------------------------------


def test_label_sample_resumes_and_records_errors(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    with pytest.raises(RuntimeError, match="no sample"):
        label_sample(con, cfg, make_client(FakeLLM()))
    build_sample(con, cfg)
    fake = FakeLLM()
    bad_id = con.execute("SELECT min(record_id) FROM vocab_sample").fetchone()[0]
    bad_title = con.execute(
        "SELECT title FROM records WHERE record_id = ?", [bad_id]
    ).fetchone()[0]
    fake.bad_titles.add(bad_title)
    client = make_client(fake)

    first = label_sample(con, cfg, client, limit=5)
    assert first.labelled + first.failed == 5 and first.remaining == 7
    second = label_sample(con, cfg, client)
    assert second.skipped == first.labelled and second.failed == 1
    assert second.ok_total == 11 and second.remaining == 0
    state = dict(
        con.execute(
            "SELECT status, count(*) FROM vocab_label_state GROUP BY 1"
        ).fetchall()
    )
    assert state == {"ok": 11, "error": 1}

    rows = con.execute(
        "SELECT kind, label, label_norm, evidence_found, role FROM vocab_labels "
        "WHERE record_id = (SELECT min(record_id) FROM vocab_labels)"
    ).fetchall()
    by_kind = {r[0]: r for r in rows}
    assert sum(r[0] == "method" for r in rows) == 1  # duplicate spelling dropped
    assert by_kind["method"][3] is True  # quote is the title
    assert by_kind["data_type"][3] is False  # invented quote
    assert by_kind["actors"][1] == "farmers" and by_kind["actors"][4] == "studied"
    assert second.labels_by_kind["setting"] == 0

    calls = len(fake.chat)
    third = label_sample(con, cfg, client)  # only the failed record is retried
    assert len(fake.chat) - calls == 2  # one record, two attempts
    assert third.skipped == 11
    forced = label_sample(con, cfg, client, force=True, limit=2)
    assert forced.labelled + forced.failed == 2 and forced.skipped == 0
    assert con.execute("SELECT count(*) FROM vocab_label_state").fetchone()[0] == 12
    con.close()


# --- clustering and proposals ---------------------------------------------------


def test_average_linkage_merges_close_vectors_only():
    v = np.array([[1.0, 0.0], [0.99, 0.1], [0.0, 1.0], [0.1, 0.99], [-1.0, 0.0]])
    assert average_linkage(v, 0.9) == [[0, 1], [2, 3], [4]]
    assert average_linkage(v, 1.01) == [[0], [1], [2], [3], [4]]
    assert len(average_linkage(v, -1.0)) == 1
    assert average_linkage(np.zeros((0, 2)), 0.5) == []


def test_average_linkage_avoids_chaining():
    # a-b and b-c are close, a-c is not: average linkage stops before joining all.
    ang = np.radians([0, 30, 60])
    v = np.stack([np.cos(ang), np.sin(ang)], axis=1)
    groups = average_linkage(v, 0.85)  # cos 30 = 0.87, mean link to c ~ 0.71
    assert len(groups) == 2


def test_resolve_term_tolerates_case_and_codes():
    terms = ["SL-a: Sustainable land use", "Case study", "O2: Food systems"]
    assert resolve_term("Case study", terms) == "Case study"
    assert resolve_term("case  STUDY", terms) == "Case study"
    assert resolve_term("Sustainable land use", terms) == "SL-a: Sustainable land use"
    assert resolve_term("SL-a", terms) == "SL-a: Sustainable land use"
    assert resolve_term("sl-a: sustainable land use", terms) == terms[0]
    assert resolve_term("Land use", terms) is None


def test_propose_terms_writes_candidates(db: Path, tmp_path: Path):
    cfg, _ = make_cfg(tmp_path, db)
    con = duckdb.connect(str(db))
    fake = FakeLLM()
    client = make_client(fake)
    with pytest.raises(RuntimeError, match="no labels"):
        propose_terms(con, cfg, client)
    build_sample(con, cfg)
    label_sample(con, cfg, client)
    s = propose_terms(con, cfg, client)
    assert s.records == 12 and s.failed == 0 and s.cached == 0
    doc = yaml.safe_load((tmp_path / "candidates.yaml").read_text())
    assert doc["generated"]["sample"]["records_labelled"] == 12
    assert doc["generated"]["models"]["tagging"] == cfg.models.tagging

    method = doc["method"]["candidates"]
    assert len(method) == 1 and method[0]["n_records"] == 12  # counts from code
    assert method[0]["existing_term"] == "Interviews and focus groups"
    assert method[0]["suggested_dimension"] == "method"  # fixed for non-themes
    themes = doc["theme"]["candidates"]
    erosion = [c for c in themes if "Erosion" in c["label"]]
    assert erosion and erosion[0]["existing_term"] is None  # unknown term dropped
    assert erosion[0]["action"] == "new" and "unknown term" in erosion[0]["note"]
    assert all(c["n_records"] < 9999 for c in themes)
    n_theme = con.execute(
        "SELECT count(DISTINCT record_id) FROM vocab_labels WHERE kind = 'theme'"
    ).fetchone()[0]
    listed = sum(c["n_records"] for c in themes) + sum(
        int(u.split(":", 1)[0]) for u in doc["theme"]["unclustered"]
    )
    assert listed == n_theme  # each record carries one theme here
    assert doc["actors"]["candidates"][0]["roles"] == {"studied": 12}

    ids = [c["id"] for k in doc if k != "generated" for c in doc[k]["candidates"]]
    assert len(ids) == len(set(ids)) and all(len(i) == 10 for i in ids)

    chats, embeds = len(fake.chat), len(fake.embed)
    again = propose_terms(con, cfg, client)  # everything cached
    doc2 = yaml.safe_load((tmp_path / "candidates.yaml").read_text())
    assert [
        c["id"] for k in doc2 if k != "generated" for c in doc2[k]["candidates"]
    ] == ids
    assert again.cached == s.proposed and again.proposed == 0
    assert len(fake.chat) == chats and len(fake.embed) == embeds
    assert propose_terms(con, cfg, client, force=True).proposed == s.proposed
    con.close()


def test_cli_vocab_extract_all_steps(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from chatcde.store import endpoint

    fake = FakeLLM()
    real_init = endpoint.EndpointClient.__init__

    def init_with_fake(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = httpx.MockTransport(fake)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(endpoint.EndpointClient, "__init__", init_with_fake)
    _, override = make_cfg(tmp_path, db)
    result = CliRunner().invoke(app, ["--config", str(override), "vocab", "extract"])
    assert result.exit_code == 0, result.output
    for title in (
        "vocab extract: sample",
        "vocab extract: label",
        "vocab extract: propose",
    ):
        assert title in result.output
    assert (tmp_path / "candidates.yaml").is_file()
    con = duckdb.connect(str(db), read_only=True)
    for t in tables.TABLES:
        assert con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] > 0
    con.close()


def test_cli_label_without_sample_fails(db: Path, tmp_path: Path):
    _, override = make_cfg(tmp_path, db)
    result = CliRunner().invoke(
        app, ["--config", str(override), "vocab", "extract", "--step", "label"]
    )
    assert result.exit_code == 1 and "no sample" in result.output
