"""Cross-process advisory serialization for one acquisition dataset base."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os


class DatasetBusyError(RuntimeError):
    """Another process currently owns the dataset's exclusive operation lock."""


@contextmanager
def _operation_lock(path: str, resource: str, purpose: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                owner = os.read(descriptor, 4096).decode("utf-8", "replace").strip()
            except OSError:
                owner = ""
            detail = f"; owner={owner}" if owner else ""
            raise DatasetBusyError(
                f"{resource} is busy{detail}") from exc
        owner = json.dumps({
            "pid": os.getpid(),
            "purpose": str(purpose),
            "started_utc": datetime.now(timezone.utc).isoformat(),
        }, sort_keys=True).encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, owner)
        os.fsync(descriptor)
        yield path
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def dataset_lock(base: str, purpose: str):
    """Serialize all operations that read or mutate one dataset base."""
    absolute = os.path.abspath(base)
    return _operation_lock(absolute + ".lock", f"dataset {absolute}", purpose)


def bench_lock(purpose: str):
    """Serialize live use of the one shared PROACT board and its instruments."""
    path = os.path.join("/tmp", f"proact-acquisition-bench-{os.getuid()}.lock")
    return _operation_lock(path, "PROACT acquisition bench", purpose)
