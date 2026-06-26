from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_launcher_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "run_scheduled_python.py"
    spec = importlib.util.spec_from_file_location("run_scheduled_python", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scheduled_script_lock_times_out_when_lock_stays_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_launcher_module()
    monkeypatch.setattr(launcher, "_try_acquire_lock", lambda _fd: False)

    with pytest.raises(TimeoutError):
        with launcher.scheduled_script_lock(
            tmp_path / "matchday_scheduler_errors.lock",
            timeout_seconds=0,
            poll_seconds=0.001,
        ):
            pass


def test_scheduled_script_lock_writes_holder_metadata(tmp_path: Path) -> None:
    launcher = _load_launcher_module()
    lock_path = tmp_path / "matchday_scheduler_errors.lock"

    with launcher.scheduled_script_lock(lock_path, timeout_seconds=1, poll_seconds=0.001):
        pass
    content = lock_path.read_text(encoding="utf-8")

    assert "pid=" in content
    assert "acquired_at=" in content
