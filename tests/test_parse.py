"""Tests for ``chatcde parse`` (issue #10, decision 0014), all offline.

PDFs are generated with PyMuPDF in each test: a text page, a scanned (image-only)
page, a blank page and a page of symbols that the character check calls garbled.
The vision model is a fake ``/chat/completions`` endpoint served through
``httpx.MockTransport``.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pymupdf
import pytest
from typer.testing import CliRunner

from chatcde import catalogue
from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.parse import pages as parse_pages
from chatcde.parse.language import detect_language
from chatcde.parse.ocr import (
    NO_TEXT,
    OcrCache,
    OcrError,
    load_prompt,
    main_script,
    ocr_page,
)
from chatcde.parse.pdf import (
    PageClass,
    RawPage,
    alnum_ratio,
    classify,
    extract_pages,
    garbage_ratio,
    render_png,
)
from chatcde.schemas import (
    AccessRights,
    DownloadStatus,
    ManifestEntry,
    PageText,
    Record,
)
from chatcde.store.chunking import ChunkSettings
from chatcde.store.endpoint import EndpointClient
from chatcde.store.index import build_chunks

EN = (
    "Sustainable land management in mountain regions depends on secure tenure, "
    "fair access to markets and knowledge shared between farmers and researchers. "
)
DE = (
    "Die nachhaltige Entwicklung von Berggebieten hängt von sicheren Landrechten, "
    "einem fairen Marktzugang und dem Austausch von Wissen zwischen Bauern ab. "
)
OCR_TEXT = "Scanned report page. " * 6
T0 = datetime(2026, 11, 7, tzinfo=UTC)


# --- helpers ------------------------------------------------------------------


def make_pdf(path: Path, kinds: list[str]) -> None:
    """Write a PDF with one page per kind: text, de, scan, blank, symbols."""
    doc = pymupdf.open()
    for kind in kinds:
        page = doc.new_page()
        if kind == "text":
            page.insert_textbox(page.rect + (50, 50, -50, -50), EN * 3)
        elif kind == "de":
            page.insert_textbox(page.rect + (50, 50, -50, -50), DE * 3)
        elif kind == "symbols":  # what a broken font encoding looks like
            page.insert_textbox(page.rect + (50, 50, -50, -50), "§¤¶ ©® ±×÷ " * 40)
        elif kind == "scan":
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
            pix.set_rect(pix.irect, (200, 200, 200))
            page.insert_image(page.rect, pixmap=pix)
        elif kind != "blank":
            raise ValueError(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def record(rid: str) -> Record:
    return Record(
        record_id=rid,
        title=f"Record {rid}",
        pub_type="report",
        languages=["me"],
        access=AccessRights.OPEN,
        has_fulltext=True,
        boris_url=f"https://boris-portal.unibe.ch/entities/publication/{rid}",
        harvested_at=T0,
    )


def manifest(rid: str, sha: str, status: DownloadStatus) -> ManifestEntry:
    return ManifestEntry(record_id=rid, status=status, sha256=sha, fetched_at=T0)


class FakeVision:
    """A fake ``/chat/completions`` endpoint; behaviour is configurable."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply: str | None = OCR_TEXT
        self.finish_reason = "stop"
        self.status = 200
        self.loops_without_penalty = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        self.calls.append(body)
        if self.loops_without_penalty and "repetition_penalty" not in body:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "la la la"}, "finish_reason": "length"}
                    ]
                },
            )
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "down"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": self.reply},
                        "finish_reason": self.finish_reason,
                    }
                ]
            },
        )


def client_for(fake: FakeVision, retries: int = 0) -> EndpointClient:
    return EndpointClient(
        "https://llm.test/v1",
        "key",
        timeout_s=5,
        max_retries=retries,
        transport=httpx.MockTransport(fake),
        sleep=lambda _: None,
    )


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Config with data paths in tmp_path, and a catalogue of four records.

    * ``r-text``: text, German text, blank (no OCR needed)
    * ``r-scan``: text, scan, symbols (two pages pending OCR)
    * ``r-gone``: was downloaded earlier, now restricted (its pages must go)
    * ``r-none``: no file
    """
    override = tmp_path / "parse.yaml"
    override.write_text(
        "logging:\n  file: null\n"
        "llm:\n  max_concurrency: 2\n"
        "parse:\n  max_retries: 0\n  max_consecutive_failures: 3\n"
        f"data:\n  raw_dir: {tmp_path / 'raw'}\n"
        f"  files_dir: {tmp_path / 'raw' / 'files'}\n"
        f"  text_dir: {tmp_path / 'text'}\n"
        f"  catalogue: {tmp_path / 'cat.duckdb'}\n",
        encoding="utf-8",
    )
    cfg = load_config(override)
    files = cfg.data.files_dir
    make_pdf(files / "r-text.pdf", ["text", "de", "blank"])
    make_pdf(files / "r-scan.pdf", ["text", "scan", "symbols"])
    con = catalogue.connect(cfg.data.catalogue)
    catalogue.replace_tables(
        con,
        {
            "records": [record(r) for r in ("r-text", "r-scan", "r-gone", "r-none")],
            "downloads": [
                manifest("r-text", "sha-text", DownloadStatus.DOWNLOADED),
                manifest("r-scan", "sha-scan", DownloadStatus.DOWNLOADED),
                manifest("r-gone", "sha-gone", DownloadStatus.RESTRICTED),
                manifest("r-none", None, DownloadStatus.NO_FILE),
            ],
            "pages": [
                PageText(
                    record_id="r-gone",
                    page=1,
                    text="old",
                    provenance={"code_version": "x", "created_at": T0},
                )
            ],
        },
    )
    con.close()
    return cfg


def rows(cfg: Config, sql: str, params: list[Any] | None = None) -> list[tuple]:
    con = duckdb.connect(str(cfg.data.catalogue), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


# --- page classification ------------------------------------------------------


def test_classify_rules(cfg: Config) -> None:
    s = cfg.parse
    assert classify(RawPage(1, EN, 0.0, 0), s) is PageClass.TEXT
    assert classify(RawPage(1, "Fig. 3", 0.9, 0), s) is PageClass.OCR
    assert classify(RawPage(1, "", 0.0, 500), s) is PageClass.OCR  # outlined text
    assert classify(RawPage(1, "12", 0.05, 3), s) is PageClass.BLANK
    garbled = "� " * 30 + EN
    assert garbage_ratio(garbled) > s.max_garbage_ratio
    assert classify(RawPage(1, garbled, 0.0, 0), s) is PageClass.GARBLED
    assert classify(RawPage(1, "(cid:12)(cid:7) " * 20, 0.0, 0), s) is PageClass.GARBLED
    assert alnum_ratio("Methods: ........ 1 2 3") == 1.0  # dot leaders are fine
    assert alnum_ratio("§¤¶ ©®") == 0.0
    burmese = "တနင်္သာရီတိုင်းဒေသကြီးအတွင်းရှိ ဆီအုန်းစိုက်ခင်းများအား လေ့လာဆန်းစစ်မှု"
    assert alnum_ratio(burmese) == 1.0  # vowel signs are marks, not symbols
    assert alnum_ratio("") == 1.0


def test_extract_pages_from_pdf(cfg: Config) -> None:
    doc = pymupdf.open(cfg.data.files_dir / "r-scan.pdf")
    raw = extract_pages(doc, cfg.parse)
    assert [p.page for p in raw] == [1, 2, 3]
    assert "mountain regions" in raw[0].text
    assert raw[1].text.strip() == "" and raw[1].image_coverage > 0.9
    assert [classify(p, cfg.parse) for p in raw] == [
        PageClass.TEXT,
        PageClass.OCR,
        PageClass.GARBLED,
    ]
    png = render_png(doc[1], dpi=cfg.parse.ocr_dpi, max_px=400)
    assert png.startswith(b"\x89PNG")
    pix = pymupdf.Pixmap(png)
    assert max(pix.width, pix.height) <= 400
    doc.close()


# --- language -----------------------------------------------------------------


def test_detect_language(cfg: Config) -> None:
    s = cfg.parse
    assert detect_language(EN, s) == "en"
    assert detect_language(DE, s) == "de"
    fr = "La politique alimentaire des villes suisses vise une transition durable. "
    assert detect_language(fr * 2, s) == "fr"
    assert detect_language("Abb. 3", s) is None


# --- vision OCR ---------------------------------------------------------------


def test_ocr_page_request_and_answers(cfg: Config) -> None:
    fake = FakeVision()
    prompt = load_prompt(cfg.prompts.dir, cfg.parse.ocr_prompt)
    assert NO_TEXT in prompt
    with client_for(fake) as client:
        text = ocr_page(client, model="vis", prompt=prompt, png=b"png", max_tokens=99)
        assert text == OCR_TEXT.strip()
        sent = fake.calls[0]
        assert sent["model"] == "vis" and sent["max_tokens"] == 99
        image = sent["messages"][0]["content"][1]["image_url"]["url"]
        assert image == "data:image/png;base64," + base64.b64encode(b"png").decode()

        fake.reply = NO_TEXT
        assert ocr_page(client, model="vis", prompt="p", png=b"x", max_tokens=9) == ""
        fake.reply = "```\nfenced\n```"
        assert ocr_page(client, model="vis", prompt="p", png=b"x", max_tokens=9) == (
            "fenced"
        )
        fake.reply, fake.finish_reason = "cut", "length"
        with pytest.raises(OcrError, match="cut off"):
            ocr_page(client, model="vis", prompt="p", png=b"x", max_tokens=9)
        fake.finish_reason, fake.reply = "stop", None
        with pytest.raises(OcrError, match="empty"):
            ocr_page(client, model="vis", prompt="p", png=b"x", max_tokens=9)


def test_ocr_loop_retried_with_repetition_penalty() -> None:
    fake = FakeVision()
    fake.loops_without_penalty = True
    with client_for(fake) as client:
        with pytest.raises(OcrError, match="cut off"):
            ocr_page(client, model="vis", prompt="p", png=b"x", max_tokens=9)
        text = ocr_page(
            client,
            model="vis",
            prompt="p",
            png=b"x",
            max_tokens=9,
            retry_repetition_penalty=1.05,
        )
    assert text == OCR_TEXT.strip()
    assert fake.calls[-1]["repetition_penalty"] == 1.05
    assert "repetition_penalty" not in fake.calls[-2]


def test_ocr_rejects_listed_script() -> None:
    fake = FakeVision()
    fake.reply = "ตัวอักษรย่อ ธนาคารพัฒนาอาเซียน ADB"  # Thai for a Lao page
    with client_for(fake) as client, pytest.raises(OcrError, match="THAI"):
        ocr_page(
            client,
            model="vis",
            prompt="p",
            png=b"x",
            max_tokens=9,
            reject_scripts=["THAI"],
        )
    assert main_script("ລາຍງານ ສະຖິຕິ") == "LAO"
    assert main_script("12 34") is None


def test_rejected_cached_answer_is_asked_again(cfg: Config) -> None:
    fake = FakeVision()
    fake.reply = "ตัวอักษรย่อ ธนาคารพัฒนาอาเซียน"
    with client_for(fake) as client:
        s = parse_pages.parse_pdfs(cfg, client=client)
    assert s.ocr_failed == 2 and s.ocr_pending == 2
    # Put a Thai answer in the cache, as an older run without the check would have.
    cache = cfg.data.text_dir / "ocr" / "r-scan.jsonl"
    fake.reply = OCR_TEXT
    with client_for(fake) as client:
        parse_pages.parse_pdfs(cfg, client=client)
    lines = [json.loads(x) for x in cache.read_text().splitlines()]
    for line in lines:
        line["text"] = "ตัวอักษรย่อ ธนาคารพัฒนาอาเซียน"
    cache.write_text("".join(json.dumps(x) + "\n" for x in lines))
    with client_for(fake) as client:
        s3 = parse_pages.parse_pdfs(cfg, client=client, force=True)
    assert s3.ocr_cached == 0 and s3.ocr_requests == 2 and s3.ocr_pending == 0


def test_ocr_retries_5xx_then_fails() -> None:
    fake = FakeVision()
    fake.status = 503
    with client_for(fake, retries=2) as client, pytest.raises(OcrError):
        ocr_page(client, model="vis", prompt="p", png=b"x", max_tokens=9)
    assert len(fake.calls) == 3


def test_ocr_cache_roundtrip(tmp_path: Path) -> None:
    from chatcde.parse.ocr import CacheKey

    cache = OcrCache(tmp_path, "r1")
    key = CacheKey(2, "abc", "vis", "ocr_page.v1")
    assert cache.get(key) is None
    cache.put(key, "hello")
    cache.put(CacheKey(3, "def", "vis", "ocr_page.v1"), "")
    (tmp_path / "r1.jsonl").open("a").write('{"page": 4, "trunc')  # crash mid-line
    again = OcrCache(tmp_path, "r1")
    assert again.get(key) == "hello"
    assert again.get(CacheKey(3, "def", "vis", "ocr_page.v1")) == ""
    assert again.get(CacheKey(2, "abc", "other-model", "ocr_page.v1")) is None


# --- the stage ----------------------------------------------------------------


def test_parse_end_to_end(cfg: Config) -> None:
    fake = FakeVision()
    with client_for(fake) as client:
        s = parse_pages.parse_pdfs(cfg, client=client)
    assert s.records == 2 and s.parsed == 2 and s.removed == 1 and s.errors == 0
    assert s.pages == 6
    assert s.by_class == {"text": 3, "ocr": 1, "garbled": 1, "blank": 1}
    assert s.ocr_requests == 2 and s.ocr_pages == 2 and s.ocr_pending == 0
    assert s.by_status == {"done": 2}
    # r-text has an English and a German page of similar length; r-scan is English.
    assert set(s.boris_vs_detected) == {"me"}
    assert sum(s.boris_vs_detected["me"].values()) == 2
    assert set(s.boris_vs_detected["me"]) <= {"en", "de"}

    got = {
        (rid, page): (text, lang, ocr, prov)
        for rid, page, text, lang, ocr, prov in rows(
            cfg, "SELECT record_id, page, text, language, ocr, provenance FROM pages"
        )
    }
    assert set(got) == {("r-text", 1), ("r-text", 2), ("r-text", 3)} | {
        ("r-scan", 1),
        ("r-scan", 2),
        ("r-scan", 3),
    }
    assert got[("r-text", 2)][1] == "de"
    assert got[("r-text", 3)][:3] == ("", None, False)
    text, lang, ocr, prov = got[("r-scan", 2)]
    assert text == OCR_TEXT.strip() and ocr and lang == "en"
    assert prov["model"] == cfg.models.vision
    assert prov["prompt_version"] == cfg.parse.ocr_prompt
    assert got[("r-scan", 1)][3]["model"] is None
    assert (cfg.data.text_dir / "parse_summary.json").is_file()
    assert (cfg.data.text_dir / "ocr" / "r-scan.jsonl").is_file()

    # Second run: nothing to do, no requests.
    with client_for(fake) as client:
        s2 = parse_pages.parse_pdfs(cfg, client=client)
    assert s2.parsed == 0 and s2.skipped == 2 and s2.ocr_requests == 0
    assert len(fake.calls) == 2

    # --force re-reads the PDFs, but OCR comes from the cache.
    with client_for(fake) as client:
        s3 = parse_pages.parse_pdfs(cfg, client=client, force=True)
    assert s3.parsed == 2 and s3.ocr_requests == 0 and s3.ocr_cached == 2
    assert s3.ocr_pages == 2


def test_no_ocr_leaves_pages_pending_then_fills_them(cfg: Config) -> None:
    s = parse_pages.parse_pdfs(cfg, ocr=False)
    assert s.ocr_requests == 0 and s.ocr_pending == 2
    assert s.by_status == {"done": 1, "needs_ocr": 1}
    (pending,) = rows(
        cfg, "SELECT ocr_pending FROM parse_state WHERE record_id = 'r-scan'"
    )[0]
    assert pending == [2, 3]

    fake = FakeVision()
    with client_for(fake) as client:
        s2 = parse_pages.parse_pdfs(cfg, client=client)
    assert s2.parsed == 0 and s2.ocr_requests == 2
    assert s2.ocr_pending == 0 and s2.by_status == {"done": 2}


def test_unreadable_pages_do_not_stop_the_pass(cfg: Config) -> None:
    make_pdf(cfg.data.files_dir / "r-scan.pdf", ["scan"] * 6)
    fake = FakeVision()
    fake.reply, fake.finish_reason = "loop", "length"
    with client_for(fake) as client:
        s = parse_pages.parse_pdfs(cfg, client=client)
    assert not s.ocr_stopped
    assert s.ocr_requests == 6 and s.ocr_failed == 6 and s.ocr_pending == 6


def test_ocr_stops_after_repeated_failures(cfg: Config) -> None:
    make_pdf(cfg.data.files_dir / "r-scan.pdf", ["scan"] * 6)
    fake = FakeVision()
    fake.status = 500
    with client_for(fake) as client:
        s = parse_pages.parse_pdfs(cfg, client=client)
    assert s.ocr_stopped
    # At most the failures that tripped the stop plus the requests in flight.
    assert 3 <= s.ocr_requests < 6
    assert s.ocr_pending == 6 and s.by_status["needs_ocr"] == 1
    ((error,),) = rows(cfg, "SELECT error FROM parse_state WHERE record_id='r-scan'")
    assert "HTTP 500" in error


def test_changed_pdf_and_broken_pdf(cfg: Config) -> None:
    parse_pages.parse_pdfs(cfg, ocr=False)
    con = catalogue.connect(cfg.data.catalogue)
    con.execute("UPDATE downloads SET sha256 = 'new' WHERE record_id = 'r-text'")
    con.close()
    (cfg.data.files_dir / "r-text.pdf").write_bytes(b"not a pdf at all")
    s = parse_pages.parse_pdfs(cfg, ocr=False)
    assert s.parsed == 1 and s.errors == 1
    assert rows(cfg, "SELECT count(*) FROM pages WHERE record_id='r-text'") == [(0,)]
    assert rows(cfg, "SELECT status FROM parse_state WHERE record_id='r-text'") == [
        ("error",)
    ]
    # An error is retried on the next run.
    make_pdf(cfg.data.files_dir / "r-text.pdf", ["text"])
    s2 = parse_pages.parse_pdfs(cfg, ocr=False)
    assert s2.parsed == 1 and s2.errors == 0


def test_limit_and_record_filter(cfg: Config) -> None:
    s = parse_pages.parse_pdfs(cfg, ocr=False, record_ids=["r-scan", "r-nope"])
    assert s.parsed == 1
    s2 = parse_pages.parse_pdfs(cfg, ocr=False, limit=1)
    assert s2.parsed == 1 and s2.skipped == 1


def test_chunking_accepts_parsed_pages(cfg: Config) -> None:
    fake = FakeVision()
    with client_for(fake) as client:
        parse_pages.parse_pdfs(cfg, client=client)
    settings = ChunkSettings(chunk_tokens=500, overlap_tokens=50, tokens_per_word=1.4)
    s = build_chunks(cfg.data.catalogue, settings)
    assert s.by_source.get("fulltext", 0) >= 2


def test_no_downloads_is_an_error(tmp_path: Path) -> None:
    override = tmp_path / "empty.yaml"
    override.write_text(
        f"logging:\n  file: null\ndata:\n  catalogue: {tmp_path / 'c.duckdb'}\n"
        f"  text_dir: {tmp_path / 'text'}\n",
        encoding="utf-8",
    )
    with pytest.raises(parse_pages.ParseError, match="harvest files"):
        parse_pages.parse_pdfs(load_config(override))


def test_cli_no_ocr(cfg: Config, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["--config", str(tmp_path / "parse.yaml"), "parse", "--no-ocr"]
    )
    assert result.exit_code == 0, result.output
    assert "Pages waiting for OCR" in result.output


@pytest.mark.network
def test_live_vision_ocr(tmp_path: Path) -> None:
    """One real page through models.vision (UniBe VPN + CHATCDE_LLM_API_KEY)."""
    import os

    cfg = load_config()
    if not os.environ.get(cfg.llm.api_key_env):
        pytest.skip(f"{cfg.llm.api_key_env} not set")
    make_pdf(tmp_path / "p.pdf", ["de"])
    doc = pymupdf.open(tmp_path / "p.pdf")
    png = render_png(doc[0], dpi=cfg.parse.ocr_dpi, max_px=cfg.parse.ocr_max_px)
    doc.close()
    with EndpointClient.from_config(cfg, max_retries=1) as client:
        text = ocr_page(
            client,
            model=cfg.models.vision,
            prompt=load_prompt(cfg.prompts.dir, cfg.parse.ocr_prompt),
            png=png,
            max_tokens=cfg.parse.ocr_max_tokens,
        )
    assert "Berggebieten" in text
    assert detect_language(text, cfg.parse) == "de"
