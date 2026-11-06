"""Logging setup for the ``chatcde`` command.

Two handlers on the root logger:

* **Console:** Rich, on **stderr**, so stdout stays clean for results that are
  piped or redirected. The level comes from the config; ``-v`` / ``-q`` override it.
* **File (optional):** plain text with timestamps, rotated, usually
  ``data/logs/chatcde.log``. Pipeline stages log what they did here, so a run can
  be reviewed later. Provenance of outputs is recorded in the data itself, not here.

Modules log with ``logging.getLogger(__name__)`` and never configure handlers.
The module is named ``log`` rather than ``logging`` to avoid shadowing the stdlib.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
"""Format of lines in the log file."""

NOISY_LOGGERS = ("httpx", "httpcore", "openai", "urllib3")
"""Third-party loggers held at WARNING so DEBUG output stays readable."""

_HANDLER_FLAG = "_chatcde_handler"


def setup_logging(
    level: str = "INFO",
    file: Path | None = None,
    file_level: str = "DEBUG",
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
) -> None:
    """Configure the root logger with a console handler and an optional file.

    Safe to call more than once (for example in tests): handlers added by an
    earlier call are removed first.

    Args:
        level: Console level name, e.g. ``"INFO"``.
        file: Log file path; its folder is created. ``None`` turns off file logging.
        file_level: Level for the log file.
        max_bytes: Rotate the file when it reaches this size.
        backup_count: Number of rotated files to keep.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            root.removeHandler(handler)
            handler.close()

    console = RichHandler(
        console=Console(stderr=True),
        level=level,
        show_path=False,
        rich_tracebacks=True,
    )
    setattr(console, _HANDLER_FLAG, True)
    root.addHandler(console)
    levels = [logging.getLevelNamesMapping()[level]]

    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
        setattr(file_handler, _HANDLER_FLAG, True)
        root.addHandler(file_handler)
        levels.append(logging.getLevelNamesMapping()[file_level])

    root.setLevel(min(levels))
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
