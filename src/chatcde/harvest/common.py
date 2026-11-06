"""Helpers shared by the harvest stages (``metadata``, ``files``, ``openalex``).

Each stage records which code produced its output (CLAUDE.md, "Reproducibility")
and writes its state and summaries atomically, so a crash never leaves a
half-written file behind.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chatcde import __version__
from chatcde.config import project_root


def code_version() -> str:
    """Package version plus the short git commit, if the source is a git checkout."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root(),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return __version__
    return f"{__version__}+g{commit}"


def now() -> datetime:
    """The current UTC time, to the second (for state files and manifests)."""
    return datetime.now(UTC).replace(microsecond=0)


def write_json(path: Path, obj: Any) -> None:
    """Write JSON via a temp file and rename, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
