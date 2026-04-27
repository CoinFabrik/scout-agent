from __future__ import annotations

from threading import Lock
from typing import Protocol, TextIO


class LineProgressSink(Protocol):
    def emit(self, line: str) -> None: ...

    def close(self) -> None: ...


class PlainLineProgressSink:
    def __init__(self, stdout: TextIO) -> None:
        self._stdout = stdout
        self._lock = Lock()

    def emit(self, line: str) -> None:
        with self._lock:
            print(line, file=self._stdout, flush=True)

    def close(self) -> None:
        return None
