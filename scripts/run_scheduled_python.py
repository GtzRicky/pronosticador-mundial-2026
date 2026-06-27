from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import traceback
from typing import Iterator


MAX_ERROR_LOG_BYTES = 1_000_000
DEFAULT_LOCK_TIMEOUT_SECONDS = 55 * 60
LOCK_POLL_SECONDS = 5.0


def _rotate_error_log(path: Path) -> None:
    if path.exists() and path.stat().st_size >= MAX_ERROR_LOG_BYTES:
        rotated = path.with_suffix(f"{path.suffix}.1")
        if rotated.exists():
            rotated.unlink()
        path.replace(rotated)


def _record_failure(path: Path, script_path: Path) -> None:
    candidates = [
        path,
        Path(tempfile.gettempdir()) / "quiniela_notification_watchdog" / path.name,
    ]
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            _rotate_error_log(candidate)
            with candidate.open("a", encoding="utf-8") as handle:
                timestamp = datetime.now(timezone.utc).isoformat()
                handle.write(f"\n[{timestamp}] Scheduled script failed: {script_path}\n")
                traceback.print_exc(file=handle)
            return
        except OSError:
            continue


def _record_skip(path: Path, script_path: Path, lock_file: Path) -> None:
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "script": str(script_path),
        "lock_file": str(lock_file),
        "reason": "lock_busy",
        "pid": os.getpid(),
    }
    candidates = [
        path,
        Path(tempfile.gettempdir()) / "quiniela_notification_watchdog" / path.name,
    ]
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return
        except OSError:
            continue


def _default_lock_file(working_directory: Path) -> Path:
    workspace_key = hashlib.sha256(str(working_directory).lower().encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"quiniela_scheduler_{workspace_key}.lock"


def _lock_file(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def _try_acquire_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.lockf(fd, fcntl.LOCK_UN)


@contextmanager
def scheduled_script_lock(
    path: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = LOCK_POLL_SECONDS,
    write_holder_metadata: bool = True,
) -> Iterator[None]:
    fd = _lock_file(path)
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    try:
        while True:
            acquired = _try_acquire_lock(fd)
            if acquired:
                if write_holder_metadata:
                    os.ftruncate(fd, 0)
                    os.write(
                        fd,
                        (
                            f"pid={os.getpid()}\n"
                            f"acquired_at={datetime.now(timezone.utc).isoformat()}\n"
                        ).encode("utf-8"),
                    )
                    os.fsync(fd)
                yield
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire scheduled script lock within "
                    f"{timeout_seconds:.0f}s: {path}"
                )
            time.sleep(poll_seconds)
    finally:
        try:
            if acquired:
                _release_lock(fd)
        finally:
            os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--error-log", required=True)
    parser.add_argument("--lock-file")
    parser.add_argument("--skip-log")
    parser.add_argument(
        "--skip-if-lock-busy",
        action="store_true",
        help="Exit successfully instead of logging a failure when the scheduler lock is busy.",
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT_SECONDS,
    )
    args, script_args = parser.parse_known_args()

    script_path = Path(args.script).resolve()
    working_directory = Path(args.working_directory).resolve()
    error_log = Path(args.error_log).resolve()
    lock_file = (
        Path(args.lock_file).resolve()
        if args.lock_file
        else _default_lock_file(working_directory)
    )
    skip_log = (
        Path(args.skip_log).resolve()
        if args.skip_log
        else working_directory / "outputs" / "logs" / "scheduler_skips.jsonl"
    )
    if not script_path.is_file():
        raise FileNotFoundError(f"Scheduled script not found: {script_path}")
    if not working_directory.is_dir():
        raise NotADirectoryError(
            f"Scheduled working directory not found: {working_directory}"
        )

    os.chdir(working_directory)
    sys.argv = [str(script_path), *script_args]
    with open(os.devnull, "w", encoding="utf-8") as sink:
        sys.stdout = sink
        sys.stderr = sink
        try:
            with scheduled_script_lock(
                lock_file,
                timeout_seconds=args.lock_timeout_seconds,
            ):
                runpy.run_path(str(script_path), run_name="__main__")
        except TimeoutError:
            if args.skip_if_lock_busy:
                _record_skip(skip_log, script_path, lock_file)
                return 0
            _record_failure(error_log, script_path)
            return 1
        except SystemExit as exc:
            if exc.code in (None, 0):
                return 0
            if isinstance(exc.code, int):
                return exc.code
            _record_failure(error_log, script_path)
            return 1
        except BaseException:
            _record_failure(error_log, script_path)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
