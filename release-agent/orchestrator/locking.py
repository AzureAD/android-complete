"""Portable OS-held file locks for local release transactions."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
import time


@contextmanager
def file_lock(path: str, timeout: float = 30.0):
    """Hold one byte of a stable lock file until the context exits or process dies."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
        else:
            import fcntl
        while not acquired:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if exc.errno not in (
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EDEADLK,
                ):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"File is locked by another process: {path}") from exc
                time.sleep(0.05)
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
