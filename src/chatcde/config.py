"""Load and validate ChatCDE's configuration.

Settings come from YAML files merged in this order (later files win):

1. ``config/default.yaml`` in the project (committed; every key has a default).
2. The user file ``$XDG_CONFIG_HOME/chatcde/config.yaml``
   (``~/.config/chatcde/config.yaml`` if ``XDG_CONFIG_HOME`` is unset), if it exists.
3. One explicit file, from ``chatcde --config PATH`` or the ``CHATCDE_CONFIG``
   environment variable. It must exist.

Mappings merge key by key; any other value (including lists) replaces the earlier
one. The merged result is validated with pydantic, and unknown keys are rejected,
so a typo in an override fails loudly instead of silently using the default.

Relative paths resolve against the project root (the folder that holds
``config/``), not the current directory, so ``data/`` is the same wherever
``chatcde`` runs. ``~`` is expanded. See docs/decisions/0006.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
)

ENV_CONFIG = "CHATCDE_CONFIG"
"""Environment variable naming an explicit override file."""

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class ConfigError(Exception):
    """A config file is missing, unreadable, or fails validation."""


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def project_root() -> Path:
    """Return the project folder that holds ``config/default.yaml``.

    The default config is not packaged into wheels; ChatCDE is installed in
    editable mode from a clone, so the file sits two levels above this package.
    """
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    """Return the path of the committed default config."""
    return project_root() / "config" / "default.yaml"


def user_config_path() -> Path:
    """Return the per-user override path (it may not exist)."""
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "chatcde" / "config.yaml"


def find_config_files(explicit: Path | None = None) -> list[Path]:
    """List the config files that apply, lowest priority first.

    Args:
        explicit: File given with ``--config``; falls back to ``$CHATCDE_CONFIG``.

    Returns:
        The default file, then the user file if it exists, then the explicit file.

    Raises:
        ConfigError: If the default or the explicit file does not exist.
    """
    default = default_config_path()
    if not default.is_file():
        raise ConfigError(
            f"Default config not found at {default}. Install ChatCDE from a clone "
            'with `pip install -e ".[dev]"`.'
        )
    files = [default]
    user = user_config_path()
    if user.is_file():
        files.append(user)
    if explicit is None and os.environ.get(ENV_CONFIG):
        explicit = Path(os.environ[ENV_CONFIG])
    if explicit is not None:
        explicit = explicit.expanduser()
        if not explicit.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        files.append(explicit)
    return files


# ---------------------------------------------------------------------------
# Models (mirror config/default.yaml; keep them in sync)
# ---------------------------------------------------------------------------


def _resolve_path(value: Path | None, info: ValidationInfo) -> Path | None:
    """Expand ``~`` and make a relative path absolute against the project root."""
    if value is None:
        return None
    value = value.expanduser()
    if value.is_absolute():
        return value
    root = (info.context or {}).get("root", project_root())
    return Path(root) / value


ProjectPath = Annotated[Path, AfterValidator(_resolve_path)]
"""A path that is resolved against the project root."""


class _Section(BaseModel):
    """Base for config sections: unknown keys are errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LLMConfig(_Section):
    """Local OpenAI-compatible endpoint (GPUStack, vLLM or Ollama)."""

    base_url: str
    api_key_env: str
    timeout_s: float
    max_concurrency: int

    def api_key(self) -> str | None:
        """Return the API key from the environment variable named in the config."""
        return os.environ.get(self.api_key_env)


class ModelsConfig(_Section):
    """Model name for each role (see docs/decisions/0003-models-by-role.md)."""

    agent: str
    tagging: str
    embedding: str
    reranker: str
    vision: str
    speech: str


class BorisConfig(_Section):
    """BORIS Portal REST API settings."""

    api_base: str
    cde_query: str
    request_delay_s: float


class OpenAlexConfig(_Section):
    """OpenAlex API settings."""

    api_base: str
    mailto: str


class EditionLocation(_Section):
    """Where one data edition is hosted."""

    url: str
    probe_timeout_s: float | None = None


class DataConfig(_Section):
    """Local data folder and bundle locations."""

    dir: ProjectPath
    editions: dict[Literal["full", "open"], EditionLocation]
    default_edition: Literal["auto", "full", "open"]


class VocabConfig(_Section):
    """Controlled vocabulary file and its version."""

    file: ProjectPath
    version: str


class PromptsConfig(_Section):
    """Folder of versioned prompt templates."""

    dir: ProjectPath


class RetrievalConfig(_Section):
    """Chunking and search settings."""

    chunk_tokens: int
    chunk_overlap_tokens: int
    top_k: int
    top_n: int


class EvaluationConfig(_Section):
    """Reference sets and thresholds for ``chatcde evaluate``."""

    reference_set: ProjectPath
    questions: ProjectPath
    min_precision: float


class LoggingConfig(_Section):
    """Console and file logging (see :mod:`chatcde.log`)."""

    level: LogLevel
    file: ProjectPath | None
    file_level: LogLevel
    max_bytes: int
    backup_count: int


class Config(_Section):
    """The whole validated configuration."""

    version: Literal[1]
    llm: LLMConfig
    models: ModelsConfig
    boris: BorisConfig
    openalex: OpenAlexConfig
    data: DataConfig
    vocab: VocabConfig
    prompts: PromptsConfig
    retrieval: RetrievalConfig
    evaluation: EvaluationConfig
    logging: LoggingConfig


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` into a copy of ``base``.

    Nested mappings merge key by key; every other value replaces the base value.
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read one YAML file as a mapping; an empty file gives an empty mapping."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def load_config(explicit: Path | None = None) -> Config:
    """Load, merge and validate the configuration.

    Args:
        explicit: Optional override file (``--config``); otherwise
            ``$CHATCDE_CONFIG`` is used if set.

    Returns:
        The validated configuration, with paths made absolute.

    Raises:
        ConfigError: If a file is missing or unreadable, or the merged settings
            are invalid. The message names the files that were merged.
    """
    files = find_config_files(explicit)
    merged: dict[str, Any] = {}
    for path in files:
        merged = deep_merge(merged, _read_yaml(path))
    try:
        return Config.model_validate(merged, context={"root": project_root()})
    except ValidationError as exc:
        sources = ", ".join(str(p) for p in files)
        raise ConfigError(f"Invalid configuration (from {sources}):\n{exc}") from exc
