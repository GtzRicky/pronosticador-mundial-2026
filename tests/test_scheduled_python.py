from __future__ import annotations

import importlib.util
from pathlib import Path
import runpy

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


def test_launcher_can_skip_busy_lock_without_logging_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_launcher_module()
    script = tmp_path / "noop.py"
    script.write_text("raise AssertionError('should not run')\n", encoding="utf-8")
    error_log = tmp_path / "errors.log"
    lock_path = tmp_path / "scheduler.lock"
    monkeypatch.setattr(launcher, "_try_acquire_lock", lambda _fd: False)
    monkeypatch.setattr(
        launcher.sys,
        "argv",
        [
            "run_scheduled_python.py",
            "--script",
            str(script),
            "--working-directory",
            str(tmp_path),
            "--error-log",
            str(error_log),
            "--lock-file",
            str(lock_path),
            "--lock-timeout-seconds",
            "0",
            "--skip-if-lock-busy",
        ],
    )

    assert launcher.main() == 0
    assert not error_log.exists()
    skip_log = tmp_path / "outputs" / "logs" / "scheduler_skips.jsonl"
    assert skip_log.exists()
    assert "lock_busy" in skip_log.read_text(encoding="utf-8")


def test_launcher_logs_busy_lock_without_skip_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load_launcher_module()
    script = tmp_path / "noop.py"
    script.write_text("raise AssertionError('should not run')\n", encoding="utf-8")
    error_log = tmp_path / "errors.log"
    lock_path = tmp_path / "scheduler.lock"
    monkeypatch.setattr(launcher, "_try_acquire_lock", lambda _fd: False)
    monkeypatch.setattr(
        launcher.sys,
        "argv",
        [
            "run_scheduled_python.py",
            "--script",
            str(script),
            "--working-directory",
            str(tmp_path),
            "--error-log",
            str(error_log),
            "--lock-file",
            str(lock_path),
            "--lock-timeout-seconds",
            "0",
        ],
    )
    monkeypatch.setattr(runpy, "run_path", lambda *_args, **_kwargs: None)

    assert launcher.main() == 1
    assert "TimeoutError" in error_log.read_text(encoding="utf-8")
