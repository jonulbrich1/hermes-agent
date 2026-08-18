from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class RuntimeAlreadyRunningError(RuntimeError):
    pass


class RuntimeDataLock:
    """Hold an OS-level exclusive lock for one writable Organic data directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeAlreadyRunningError(
                "Another Organic runtime already owns this Living Memory directory. "
                "Use the running Hermes Organic tab or its MCP tools instead of starting "
                f"a second engine. Lock: {self.path}"
            ) from exc
        self._file = handle

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        self._file = None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "RuntimeDataLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
