"""Tests for ``chatcde harvest files`` (issue #8, decision 0009), all offline.

A small catalogue is built from ``tests/fixtures/records.jsonl``; BORIS's bundle
replies use the real bitstream JSON in ``tests/fixtures/raw/<uuid>/`` and are
served through ``httpx.MockTransport``. File contents are fake bytes whose MD5
is patched into the bitstream JSON, so the checksum check is exercised.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from chatcde import catalogue
from chatcde.cli import app
from chatcde.config import Config, load_config
from chatcde.harvest.boris import BorisClient, NotOnVPNError
from chatcde.harvest.files import download_files, file_path, is_pdf_name, main_pdf
from chatcde.harvest.metadata import HarvestError
from chatcde.schemas import Record

FIX = Path(__file__).parent / "fixtures"
JSON = {"content-type": "application/hal+json;charset=UTF-8"}
N = 6  # records used from the fixtures


def fixture_records() -> list[Record]:
    lines = (FIX / "records.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [Record.model_validate_json(x) for x in lines if x.strip()]
    return [r for r in recs if (FIX / "raw" / r.record_id).is_dir()][:N]


def bitstreams(uuid: str) -> list[dict[str, Any]]:
    path = FIX / "raw" / uuid / "bitstreams.json"
    return json.loads(path.read_text(encoding="utf-8"))


def body(uuid: str) -> bytes:
    return f"%PDF-1.7 fake file of {uuid}".encode()


class FakeBoris:
    """Serves bundles and file contents; behaviour per record is configurable."""

    def __init__(self, records: list[Record]) -> None:
        self.files: dict[str, list[dict[str, Any]]] = {}
        self.content: dict[str, tuple[str, bytes]] = {}  # bitstream uuid -> rec, data
        for r in records:
            bs = copy.deepcopy(bitstreams(r.record_id))
            for b in bs:
                data = body(r.record_id)
                b["checkSum"]["value"] = hashlib.md5(data).hexdigest()
                self.content[b["uuid"]] = (r.record_id, data)
            self.files[r.record_id] = bs
        self.restricted: set[str] = set()
        self.html: set[str] = set()
        self.flaky: dict[str, int] = {}  # record -> 5xx replies before success
        self.corrupt: set[str] = set()
        self.downloads: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        if parts[-1] == "bundles":
            uuid = parts[-2]
            assert request.url.params["embed"] == "bitstreams"
            bs = self.files.get(uuid, [])
            bundles = (
                [
                    {
                        "name": "ORIGINAL",
                        "_links": {"bitstreams": {"href": "unused"}},
                        "_embedded": {
                            "bitstreams": {
                                "_embedded": {"bitstreams": bs},
                                "page": {"totalElements": len(bs)},
                            }
                        },
                    }
                ]
                if bs
                else []
            )
            return httpx.Response(
                200, headers=JSON, json={"_embedded": {"bundles": bundles}}
            )
        if parts[-1] == "content":
            rec, data = self.content[parts[-2]]
            self.downloads.append(rec)
            if rec in self.restricted:
                return httpx.Response(401, headers=JSON, json={"status": 401})
            if rec in self.html:
                return httpx.Response(
                    200, headers={"content-type": "text/html"}, text="<html/>"
                )
            if self.flaky.get(rec, 0) > 0:
                self.flaky[rec] -= 1
                return httpx.Response(503)
            if rec in self.corrupt:
                data = data + b"truncated?"
            return httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=data
            )
        return httpx.Response(200, headers=JSON, json={"_links": {}})  # preflight


@pytest.fixture
def records() -> list[Record]:
    return fixture_records()


@pytest.fixture
def cfg(tmp_path: Path, records: list[Record]) -> Config:
    override = tmp_path / "files.yaml"
    override.write_text(
        "logging:\n  file: null\n"
        "boris:\n  request_delay_s: 0\n  max_retries: 2\n"
        f"data:\n  raw_dir: {tmp_path / 'raw'}\n"
        f"  files_dir: {tmp_path / 'raw' / 'files'}\n"
        f"  catalogue: {tmp_path / 'cat.duckdb'}\n",
        encoding="utf-8",
    )
    cfg = load_config(override)
    con = catalogue.connect(cfg.data.catalogue)
    catalogue.replace_tables(con, {"records": records})
    con.close()
    return cfg


def client(cfg: Config, fake: FakeBoris) -> BorisClient:
    return BorisClient(
        cfg.boris, transport=httpx.MockTransport(fake), sleep=lambda s: None
    )


def rows(cfg: Config, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(cfg.data.catalogue), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def statuses(cfg: Config) -> dict[str, str]:
    return dict(rows(cfg, "SELECT record_id, status FROM downloads"))


# --- choosing the file -------------------------------------------------------


def test_main_pdf_is_largest_pdf():
    bs = [
        {"name": "data.xlsx", "sizeBytes": 10_000_000},
        {"name": "small.pdf", "sizeBytes": 10},
        {"name": "Main.PDF", "sizeBytes": 500},
    ]
    assert main_pdf(bs)["name"] == "Main.PDF"
    assert main_pdf([{"name": "data.xlsx", "sizeBytes": 1}]) is None


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("paper.pdf", True),
        ("PAPER.PDF", True),
        ("pdf", True),
        ("1-s2.0-S0921800913001614-main.pdf__tid=437f&acdnat=1398", True),
        ("slides.pptx", False),
        ("manual.zip", False),
        ("pdf_guide.docx", False),
        (None, False),
    ],
)
def test_pdf_names_seen_on_boris(name: str | None, ok: bool):
    assert is_pdf_name(name) is ok


# --- the stage -----------------------------------------------------------------


def test_downloads_files_and_writes_manifest(cfg: Config, records: list[Record]):
    fake = FakeBoris(records)
    with client(cfg, fake) as c:
        s = download_files(cfg, client=c)
    assert s.by_status == {"downloaded": N} and s.attempted == N
    for r in records:
        path = file_path(cfg.data.files_dir, r.record_id)
        assert path.read_bytes() == body(r.record_id)
    got = rows(cfg, "SELECT record_id, sha256, bytes, file_name, url FROM downloads")
    for rid, sha, size, name, url in got:
        assert sha == hashlib.sha256(body(rid)).hexdigest()
        assert size == len(body(rid))
        assert name.lower().endswith(".pdf") and url.endswith("/content")
    assert rows(cfg, "SELECT bool_and(has_fulltext) FROM records") == [(True,)]
    assert not list(cfg.data.files_dir.glob("*.part"))
    saved = json.loads(
        (cfg.data.raw_dir / "boris" / "files_summary.json").read_text("utf-8")
    )
    assert saved["by_status"] == {"downloaded": N} and saved["code_version"]


def test_statuses_and_access_table(cfg: Config, records: list[Record]):
    fake = FakeBoris(records)
    a, b, c_, d = (r.record_id for r in records[:4])
    fake.restricted.add(a)
    fake.files[b] = []  # no ORIGINAL bundle
    fake.files[c_] = [dict(fake.files[c_][0], name="table.xlsx")]  # no PDF
    fake.corrupt.add(d)
    with client(cfg, fake) as c:
        s = download_files(cfg, client=c)
    st = statuses(cfg)
    assert (st[a], st[b], st[c_], st[d]) == (
        "restricted",
        "no_file",
        "no_file",
        "error",
    )
    err = dict(rows(cfg, "SELECT record_id, error FROM downloads"))
    assert "table.xlsx" in err[c_] and "MD5" in err[d] and err[a] == "HTTP 401"
    for rid in (a, d):
        assert not file_path(cfg.data.files_dir, rid).exists()
    access = {r.record_id: r.access.value for r in records}
    assert s.by_access[access[a]]["restricted"] >= 1
    assert sum(n for row in s.by_access.values() for n in row.values()) == N
    assert rows(cfg, "SELECT count(*) FROM records WHERE has_fulltext") == [(N - 4,)]


def test_5xx_is_retried(cfg: Config, records: list[Record]):
    fake = FakeBoris(records)
    fake.flaky[records[0].record_id] = 2  # max_retries is 2: third try succeeds
    with client(cfg, fake) as c:
        download_files(cfg, client=c)
    assert statuses(cfg)[records[0].record_id] == "downloaded"


def test_rerun_skips_finished_and_retries_errors(cfg: Config, records: list[Record]):
    fake = FakeBoris(records)
    bad, restricted = records[0].record_id, records[1].record_id
    fake.corrupt.add(bad)
    fake.restricted.add(restricted)
    with client(cfg, fake) as c:
        download_files(cfg, client=c)
    fake.corrupt.clear()
    fake.downloads.clear()
    with client(cfg, fake) as c:
        s = download_files(cfg, client=c)
    assert fake.downloads == [bad]
    assert s.skipped == N - 1 and statuses(cfg)[bad] == "downloaded"

    # A downloaded file that went missing is fetched again.
    gone = records[2].record_id
    file_path(cfg.data.files_dir, gone).unlink()
    fake.downloads.clear()
    with client(cfg, fake) as c:
        download_files(cfg, client=c)
    assert fake.downloads == [gone]

    # --force tries everything again.
    fake.downloads.clear()
    with client(cfg, fake) as c:
        s = download_files(cfg, client=c, force=True)
    assert sorted(fake.downloads) == sorted(r.record_id for r in records)
    assert s.skipped == 0


def test_limit(cfg: Config, records: list[Record]):
    fake = FakeBoris(records)
    with client(cfg, fake) as c:
        s = download_files(cfg, client=c, limit=2)
    assert s.attempted == 2 and len(statuses(cfg)) == 2
    assert s.by_access  # records not yet tried are reported as not_tried
    assert sum(row.get("not_tried", 0) for row in s.by_access.values()) == N - 2


def test_html_reply_stops_the_run_keeping_progress(cfg: Config, records: list[Record]):
    fake = FakeBoris(records)
    stop_at = sorted(r.record_id for r in records)[2]
    fake.html.add(stop_at)
    with client(cfg, fake) as c, pytest.raises(NotOnVPNError):
        download_files(cfg, client=c)
    assert len(statuses(cfg)) == 2
    assert not file_path(cfg.data.files_dir, stop_at).exists()


def test_records_left_out_are_dropped_from_manifest(cfg: Config, records: list[Record]):
    fake = FakeBoris(records)
    with client(cfg, fake) as c:
        download_files(cfg, client=c)
    con = catalogue.connect(cfg.data.catalogue)
    catalogue.replace_tables(con, {"records": records[1:]})
    con.close()
    with client(cfg, fake) as c:
        s = download_files(cfg, client=c)
    assert s.removed == 1 and records[0].record_id not in statuses(cfg)


def test_empty_catalogue_fails_clearly(cfg: Config):
    con = catalogue.connect(cfg.data.catalogue)
    catalogue.replace_tables(con, {"records": []})
    con.close()
    with pytest.raises(HarvestError, match="harvest metadata"):
        download_files(cfg, client=client(cfg, FakeBoris([])))


# --- CLI ------------------------------------------------------------------------


def test_cli_prints_tables(
    cfg: Config, records: list[Record], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import chatcde.harvest.files as files

    fake = FakeBoris(records)
    real = files.BorisClient
    monkeypatch.setattr(
        files,
        "BorisClient",
        lambda bcfg: real(
            bcfg, transport=httpx.MockTransport(fake), sleep=lambda s: None
        ),
    )
    result = CliRunner().invoke(
        app, ["--config", str(tmp_path / "files.yaml"), "harvest", "files"]
    )
    assert result.exit_code == 0, result.output
    assert "downloaded" in result.output and "access" in result.output


def test_cli_not_on_vpn_exits_2(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import chatcde.harvest.files as files

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html/>"
        )

    real = files.BorisClient
    monkeypatch.setattr(
        files,
        "BorisClient",
        lambda bcfg: real(
            bcfg, transport=httpx.MockTransport(html), sleep=lambda s: None
        ),
    )
    result = CliRunner().invoke(
        app, ["--config", str(tmp_path / "files.yaml"), "harvest", "files"]
    )
    assert result.exit_code == 2
    assert "VPN" in result.output
    assert rows(cfg, "SELECT count(*) FROM downloads") == [(0,)]
