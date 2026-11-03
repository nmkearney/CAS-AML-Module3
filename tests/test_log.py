from __future__ import annotations

import logging
from pathlib import Path

from chatcde.log import setup_logging


def _ours() -> list[logging.Handler]:
    return [
        h for h in logging.getLogger().handlers if getattr(h, "_chatcde_handler", False)
    ]


def test_file_log_written_with_timestamp_format(tmp_path: Path):
    log_file = tmp_path / "logs" / "chatcde.log"
    setup_logging("WARNING", file=log_file, file_level="DEBUG")
    logging.getLogger("chatcde.test").debug("hello file")
    for h in _ours():
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "DEBUG   chatcde.test: hello file" in text


def test_repeated_setup_does_not_stack_handlers(tmp_path: Path):
    setup_logging("INFO", file=tmp_path / "a.log")
    setup_logging("INFO", file=None)
    assert len(_ours()) == 1


def test_console_goes_to_stderr(capsys):
    setup_logging("INFO")
    logging.getLogger("chatcde.test").info("to stderr")
    out, err = capsys.readouterr()
    assert "to stderr" not in out
    assert "to stderr" in err


def test_noisy_loggers_held_at_warning():
    setup_logging("DEBUG")
    assert logging.getLogger("httpx").level == logging.WARNING
