"""Tests for the cleanup_virtualenv helper."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "cleanup_virtualenv.py"

spec = importlib.util.spec_from_file_location("cleanup_virtualenv", MODULE_PATH)
cleanup_virtualenv = importlib.util.module_from_spec(spec)
assert spec and spec.loader  # Defensive: ensure the module can be loaded.
spec.loader.exec_module(cleanup_virtualenv)  # type: ignore[assignment]


def test_run_dry_run_preserves_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("dummy")

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    cleanup_virtualenv.run(str(venv_dir), dry_run=True, force=True)

    assert venv_dir.exists()


def test_run_removes_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv_dir = tmp_path / "custom_venv"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("dummy")

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    cleanup_virtualenv.run(str(venv_dir), force=True)

    assert not venv_dir.exists()


def test_run_errors_when_active(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    venv_dir = tmp_path / "active"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("dummy")

    monkeypatch.setenv("VIRTUAL_ENV", str(venv_dir))

    with pytest.raises(SystemExit) as exc:
        cleanup_virtualenv.run(str(venv_dir))

    assert exc.value.code == 2
    assert venv_dir.exists()

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


def test_purge_pip_cache_invokes_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}

    def fake_run(cmd: list[str], check: bool, stdout: Any, stderr: Any) -> None:  # type: ignore[override]
        calls["cmd"] = cmd
        calls["check"] = check

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(cleanup_virtualenv.subprocess, "run", fake_run)

    cleanup_virtualenv.run(str(tmp_path / "missing"), force=True, purge_pip_cache=True)

    assert calls["cmd"][0:3] == [cleanup_virtualenv.sys.executable, "-m", "pip"]
    assert calls["cmd"][3:] == ["cache", "purge"]
