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
    Field,
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
    rerank_path: str

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


class ModelAlternativesConfig(_Section):
    """Second-choice model for each role, or ``None`` if there is none.

    ``chatcde evaluate`` compares these with :class:`ModelsConfig` (decision 0003).
    """

    agent: str | None = None
    tagging: str | None = None
    embedding: str | None = None
    reranker: str | None = None
    vision: str | None = None
    speech: str | None = None


class BorisConfig(_Section):
    """BORIS Portal REST API settings."""

    api_base: str
    cde_query: str
    request_delay_s: float
    page_size: int
    sort: str
    timeout_s: float
    download_timeout_s: float
    max_retries: int


class OpenAlexConfig(_Section):
    """OpenAlex API settings (decision 0010)."""

    api_base: str
    api_key_env: str
    mailto: str = ""
    request_delay_s: float
    batch_size: int = Field(ge=1, le=100)
    timeout_s: float
    max_retries: int
    min_abstract_words: int = Field(ge=0)
    boris_hosts: list[str]
    boris_doi_prefixes: list[str]

    def api_key(self) -> str | None:
        """Return the API key from the environment variable named in the config."""
        return os.environ.get(self.api_key_env) or None


class EditionLocation(_Section):
    """Where one data edition is hosted."""

    url: str
    probe_timeout_s: float | None = None


class DataConfig(_Section):
    """Local data folder and bundle locations."""

    dir: ProjectPath
    raw_dir: ProjectPath
    files_dir: ProjectPath
    text_dir: ProjectPath
    catalogue: ProjectPath
    editions: dict[Literal["full", "open"], EditionLocation]
    default_edition: Literal["auto", "full", "open"]


class ParseConfig(_Section):
    """How PDFs become page text (docs/decisions/0014-parsing.md)."""

    min_chars: int = Field(ge=0)
    min_image_coverage: float = Field(ge=0.0, le=1.0)
    min_drawings: int = Field(ge=1)
    max_garbage_ratio: float = Field(ge=0.0, le=1.0)
    min_alnum_ratio: float = Field(ge=0.0, le=1.0)
    ocr_prompt: str
    ocr_dpi: int = Field(ge=36)
    ocr_max_px: int = Field(ge=256)
    ocr_max_tokens: int = Field(ge=1)
    ocr_retry_repetition_penalty: float | None = Field(default=None, gt=1.0)
    ocr_reject_scripts: list[str] = Field(default_factory=list)
    max_retries: int = Field(ge=0)
    max_consecutive_failures: int = Field(ge=1)
    languages: list[str] = Field(min_length=2)
    min_chars_language: int = Field(ge=0)
    min_language_confidence: float = Field(ge=0.0, le=1.0)
    language_sample_chars: int = Field(ge=1)


class VocabConfig(_Section):
    """Controlled vocabulary file and its version."""

    file: ProjectPath
    version: str


class PromptsConfig(_Section):
    """Folder of versioned prompt templates."""

    dir: ProjectPath


class RetrievalConfig(_Section):
    """Chunking and search settings (decisions 0011 and 0013)."""

    chunk_tokens: int
    chunk_overlap_tokens: int
    tokens_per_word: float = Field(gt=0)
    top_k: int = Field(ge=1)
    top_n: int = Field(ge=1)
    rrf_k: int = Field(ge=1)
    rerank: bool
    rerank_max_chars: int = Field(ge=100)
    bm25_k1: float = Field(ge=0)
    bm25_b: float = Field(ge=0, le=1)


class EmbeddingConfig(_Section):
    """How chunks and queries are embedded (docs/decisions/0012-embeddings.md)."""

    batch_size: int = Field(ge=1)
    dimensions: int | None = Field(default=None, ge=1)
    include_title: bool
    max_retries: int = Field(ge=0)
    query_instructions: dict[str, str] = Field(default_factory=dict)


class ToolsConfig(_Section):
    """Limits on what one tool call returns (decision 0020)."""

    max_k: int = Field(ge=1)
    max_section_pages: int = Field(ge=1)


class GapsConfig(_Section):
    """Gap analysis settings (decisions 0018 and 0021)."""

    exclude_types: list[str] = Field(default_factory=list)
    min_expected: float = Field(ge=0)


class AgentConfig(_Section):
    """The tool-calling agent (decision 0022)."""

    system_prompt: str
    tools: list[str]
    max_tool_calls: int = Field(ge=0)
    temperature: float = Field(ge=0)
    max_tokens: int = Field(ge=64)
    max_tool_result_chars: int = Field(ge=500)
    max_ids_per_list: int = Field(ge=1)
    omit_fields: list[str] = Field(default_factory=list)
    extra_body: dict[str, Any] = Field(default_factory=dict)


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
    model_alternatives: ModelAlternativesConfig
    boris: BorisConfig
    openalex: OpenAlexConfig
    data: DataConfig
    parse: ParseConfig
    vocab: VocabConfig
    prompts: PromptsConfig
    retrieval: RetrievalConfig
    embedding: EmbeddingConfig
    tools: ToolsConfig
    gaps: GapsConfig
    agent: AgentConfig
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
