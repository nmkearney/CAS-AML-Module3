from __future__ import annotations

from pathlib import Path

import pytest

from chatcde.config import (
    ConfigError,
    deep_merge,
    find_config_files,
    load_config,
    project_root,
    user_config_path,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_load_and_paths_are_absolute():
    cfg = load_config()
    assert cfg.version == 1
    assert cfg.boris.request_delay_s == pytest.approx(0.3)
    assert cfg.data.dir == project_root() / "data"
    assert cfg.vocab.file.is_absolute()
    assert cfg.logging.file is None  # turned off by the test override


def test_deep_merge_merges_maps_and_replaces_lists():
    base = {"a": {"x": 1, "y": 2}, "l": [1, 2]}
    assert deep_merge(base, {"a": {"y": 3}, "l": [9]}) == {
        "a": {"x": 1, "y": 3},
        "l": [9],
    }
    assert base == {"a": {"x": 1, "y": 2}, "l": [1, 2]}  # input not mutated


def test_user_file_overrides_default(isolated_config: Path):
    write(user_config_path(), "models:\n  agent: my-model\n")
    cfg = load_config()
    assert cfg.models.agent == "my-model"
    assert cfg.models.tagging  # other keys keep their defaults


def test_explicit_file_beats_user_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CHATCDE_CONFIG")
    write(user_config_path(), "models:\n  agent: from-user\n")
    explicit = write(tmp_path / "x.yaml", "models:\n  agent: from-explicit\n")
    files = find_config_files(explicit)
    assert files[-1] == explicit
    assert load_config(explicit).models.agent == "from-explicit"


def test_env_variable_names_explicit_file(tmp_path: Path, monkeypatch):
    env_file = write(tmp_path / "env.yaml", "retrieval:\n  top_n: 3\n")
    monkeypatch.setenv("CHATCDE_CONFIG", str(env_file))
    assert load_config().retrieval.top_n == 3


def test_unknown_key_is_an_error(tmp_path: Path):
    bad = write(tmp_path / "bad.yaml", "retrieval:\n  topn: 3\n")
    with pytest.raises(ConfigError, match="topn"):
        load_config(bad)


def test_missing_explicit_file_is_an_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_absolute_and_home_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = write(
        tmp_path / "p.yaml", f"data:\n  dir: {tmp_path}/d\nvocab:\n  file: ~/v.yaml\n"
    )
    cfg = load_config(f)
    assert cfg.data.dir == tmp_path / "d"
    assert cfg.vocab.file == tmp_path / "v.yaml"


def test_secret_comes_from_environment(monkeypatch):
    cfg = load_config()
    monkeypatch.setenv(cfg.llm.api_key_env, "s3cret")
    assert cfg.llm.api_key() == "s3cret"
