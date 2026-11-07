"""``chatcde harvest files``: each record's main PDF from BORIS, with a manifest.

For every record in the catalogue (decision 0009):

1. List the files in the item's ``ORIGINAL`` bundle (one request, bitstreams
   embedded).
2. Pick the **main PDF**: the largest file with a PDF name (:func:`is_pdf_name`).
3. Download it to ``<files_dir>/<record_id>.pdf`` (via a ``.part`` file), checking
   the MD5 that BORIS lists for it.
4. Write one :class:`~chatcde.schemas.ManifestEntry` to the ``downloads`` table:
   ``downloaded``, ``restricted`` (HTTP 401/403), ``no_file`` (no PDF in
   ``ORIGINAL``) or ``error``.

Every record is tried whatever its ``access`` value, so the run answers whether
files marked *restricted* in BORIS can be downloaded on the UniBe VPN (open
question in CLAUDE.md). Each entry is committed as soon as it is known, so an
interrupted run resumes where it stopped. A rerun skips records that are
``downloaded`` (file still on disk with the recorded size), ``restricted`` or
``no_file``, and retries ``error``; ``force`` redoes everything.

At the end ``records.has_fulltext`` is set from the manifest, and a summary with
counts by status and by ``access`` × status goes to the log and to
``<raw_dir>/boris/files_summary.json``.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from chatcde import catalogue
from chatcde.config import Config
from chatcde.harvest.boris import (
    BorisClient,
    BorisError,
    FileRestrictedError,
    NotOnVPNError,
)
from chatcde.harvest.common import code_version, now, write_json
from chatcde.harvest.metadata import HarvestError, boris_dir
from chatcde.schemas import DownloadStatus, ManifestEntry

log = logging.getLogger(__name__)

#: Statuses a rerun does not try again (``downloaded`` also needs its file).
FINAL = {DownloadStatus.DOWNLOADED, DownloadStatus.RESTRICTED, DownloadStatus.NO_FILE}

#: Called after each record with (records done, records to do, latest entry).
ProgressFn = Callable[[int, int, ManifestEntry], None]


class FilesSummary(BaseModel):
    """State of the download manifest after a run, with its denominators."""

    code_version: str
    run_at: datetime
    records: int = Field(description="Records in the catalogue")
    attempted: int = Field(description="Records tried in this run")
    skipped: int = Field(description="Records already finished, not tried again")
    requests: int = Field(description="HTTP requests sent to BORIS in this run")
    by_status: dict[str, int] = Field(description="All manifest entries by status")
    by_access: dict[str, dict[str, int]] = Field(
        description="records.access -> download status -> records"
    )
    run_by_status: dict[str, int] = Field(description="Entries written in this run")
    downloaded_bytes: int = Field(description="Total size of downloaded files")
    removed: int = Field(description="Manifest rows dropped: record left the catalogue")


def file_path(files_dir: Path, record_id: str) -> Path:
    """Where a record's main PDF is stored."""
    return files_dir / f"{record_id}.pdf"


#: A PDF by its name: ``x.pdf``, a bare ``pdf``, or a publisher download name
#: with the query glued on (``…-main.pdf__tid=…&acdnat=…``, seen on BORIS).
PDF_NAME = re.compile(r"(^pdf$)|\.pdf($|[_?&#])", re.IGNORECASE)


def is_pdf_name(name: str | None) -> bool:
    """True if a bitstream name marks a PDF (see :data:`PDF_NAME`)."""
    return bool(PDF_NAME.search((name or "").strip()))


def main_pdf(bitstreams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The main file: the largest bitstream with a PDF name, or None."""
    pdfs = [b for b in bitstreams if is_pdf_name(b.get("name"))]
    return max(pdfs, key=lambda b: b.get("sizeBytes") or 0) if pdfs else None


def _md5(bitstream: dict[str, Any]) -> str | None:
    """The MD5 checksum BORIS lists for a bitstream, if any."""
    cs = bitstream.get("checkSum") or {}
    if (cs.get("checkSumAlgorithm") or "").upper() == "MD5":
        return cs.get("value")
    return None


def fetch_one(client: BorisClient, record_id: str, files_dir: Path) -> ManifestEntry:
    """Find, download and classify one record's main PDF.

    Errors that concern this record only become an ``error`` entry, so one bad
    file does not stop the run.

    Raises:
        NotOnVPNError: BORIS sent its off-network page; the whole run must stop.
    """
    entry: dict[str, Any] = {"record_id": record_id, "fetched_at": now()}
    try:
        bitstreams = client.original_bitstreams(record_id)
    except NotOnVPNError:
        raise
    except BorisError as exc:
        return ManifestEntry(status=DownloadStatus.ERROR, error=str(exc), **entry)
    pdf = main_pdf(bitstreams)
    if pdf is None:
        names = ", ".join(b.get("name") or "?" for b in bitstreams)
        return ManifestEntry(
            status=DownloadStatus.NO_FILE,
            error=f"no PDF in ORIGINAL: {names}" if bitstreams else None,
            **entry,
        )
    entry |= {"url": pdf["_links"]["content"]["href"], "file_name": pdf.get("name")}
    dest = file_path(files_dir, record_id)
    try:
        got = client.download(entry["url"], dest, expected_md5=_md5(pdf))
    except NotOnVPNError:
        raise
    except FileRestrictedError as exc:
        dest.unlink(missing_ok=True)
        return ManifestEntry(status=DownloadStatus.RESTRICTED, error=str(exc), **entry)
    except BorisError as exc:
        dest.unlink(missing_ok=True)
        return ManifestEntry(status=DownloadStatus.ERROR, error=str(exc), **entry)
    return ManifestEntry(
        status=DownloadStatus.DOWNLOADED, sha256=got.sha256, bytes=got.bytes, **entry
    )


def _is_finished(row: tuple[str, int | None], files_dir: Path, record_id: str) -> bool:
    """True if a manifest row (status, bytes) needs no new attempt."""
    status, size = DownloadStatus(row[0]), row[1]
    if status not in FINAL:
        return False
    if status is DownloadStatus.DOWNLOADED:
        path = file_path(files_dir, record_id)
        return path.exists() and path.stat().st_size == size
    return True


def _upsert(con: duckdb.DuckDBPyConnection, entry: ManifestEntry) -> None:
    """Replace one record's manifest row, in one transaction."""
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM downloads WHERE record_id = ?", [entry.record_id])
        catalogue.insert(con, "downloads", [entry])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _summarise(con: duckdb.DuckDBPyConnection, **run: Any) -> FilesSummary:
    """Counts over the whole manifest, joined to ``records.access``."""
    by_status = dict(
        con.execute(
            "SELECT status, count(*) FROM downloads GROUP BY status ORDER BY 2 DESC"
        ).fetchall()
    )
    by_access: dict[str, dict[str, int]] = defaultdict(dict)
    for access, status, n in con.execute(
        "SELECT r.access, coalesce(d.status, 'not_tried'), count(*) "
        "FROM records r LEFT JOIN downloads d USING (record_id) "
        "GROUP BY ALL ORDER BY 1, 3 DESC"
    ).fetchall():
        by_access[access][status] = n
    (records,) = con.execute("SELECT count(*) FROM records").fetchone()
    (size,) = con.execute(
        "SELECT coalesce(sum(bytes), 0) FROM downloads WHERE status = 'downloaded'"
    ).fetchone()
    return FilesSummary(
        code_version=code_version(),
        run_at=now(),
        records=records,
        by_status=by_status,
        by_access=dict(by_access),
        downloaded_bytes=size,
        **run,
    )


def download_files(
    cfg: Config,
    *,
    force: bool = False,
    limit: int | None = None,
    client: BorisClient | None = None,
    progress: ProgressFn | None = None,
) -> FilesSummary:
    """Download every record's main PDF and keep the ``downloads`` manifest.

    Args:
        cfg: The full config (``boris`` and ``data`` sections are used).
        force: Try every record again, even finished ones.
        limit: Try at most this many records (for trial runs).
        client: A client to use instead of a new one (for tests).
        progress: Called after each record, e.g. to drive a progress bar.

    Returns:
        The summary, also written to ``<raw_dir>/boris/files_summary.json``.

    Raises:
        HarvestError: The catalogue has no records yet.
        NotOnVPNError: Not on the UniBe network/VPN (entries so far are kept).
    """
    db, files_dir = cfg.data.catalogue, cfg.data.files_dir
    con = catalogue.connect(db)
    try:
        catalogue.ensure_tables(con, ["records", "downloads"])
        ids = [
            r[0]
            for r in con.execute(
                "SELECT record_id FROM records ORDER BY record_id"
            ).fetchall()
        ]
        if not ids:
            raise HarvestError(
                f"no records in {db}; run `chatcde harvest metadata` first"
            )
        (removed,) = con.execute(
            "SELECT count(*) FROM downloads "
            "WHERE record_id NOT IN (SELECT record_id FROM records)"
        ).fetchone()
        if removed:
            log.info("Dropping %d manifest rows of records no longer listed", removed)
            con.execute(
                "DELETE FROM downloads "
                "WHERE record_id NOT IN (SELECT record_id FROM records)"
            )
        done = {
            r[0]: (r[1], r[2])
            for r in con.execute(
                "SELECT record_id, status, bytes FROM downloads"
            ).fetchall()
        }
        todo = [
            rid
            for rid in ids
            if force or rid not in done or not _is_finished(done[rid], files_dir, rid)
        ]
        skipped = len(ids) - len(todo)
        if limit is not None:
            todo = todo[:limit]
        log.info(
            "%d records: %d finished earlier, trying %d%s",
            len(ids),
            skipped,
            len(todo),
            " (--force)" if force else "",
        )

        run: Counter[str] = Counter()
        own = client is None
        client = client or BorisClient(cfg.boris)
        try:
            if todo:
                client.preflight()
            for i, rid in enumerate(todo, 1):
                entry = fetch_one(client, rid, files_dir)
                _upsert(con, entry)
                run[entry.status.value] += 1
                if entry.status is DownloadStatus.ERROR:
                    log.warning("%s: %s", rid, entry.error)
                else:
                    log.debug("%s: %s %s", rid, entry.status.value, entry.file_name)
                if progress is not None:
                    progress(i, len(todo), entry)
                if i % 100 == 0:
                    log.info("%d/%d tried: %s", i, len(todo), dict(run))
            requests = client.requests
        finally:
            if own:
                client.close()

        con.execute(
            "UPDATE records SET has_fulltext = record_id IN "
            "(SELECT record_id FROM downloads WHERE status = 'downloaded')"
        )
        summary = _summarise(
            con,
            attempted=len(todo),
            skipped=skipped,
            requests=requests,
            run_by_status=dict(run.most_common()),
            removed=removed,
        )
    finally:
        con.close()

    write_json(
        boris_dir(cfg.data.raw_dir) / "files_summary.json",
        summary.model_dump(mode="json"),
    )
    log.info(
        "Manifest: %s over %d records (%.1f GB) -> %s",
        summary.by_status,
        summary.records,
        summary.downloaded_bytes / 1e9,
        files_dir,
    )
    return summary
