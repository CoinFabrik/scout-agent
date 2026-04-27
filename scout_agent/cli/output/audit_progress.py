from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from scout_agent.audit.io.reporting import (
    AuditProgressReporter,
)

_ResultT = TypeVar("_ResultT")


class AuditProgressSession(Protocol):
    reporter: AuditProgressReporter

    def run(self, task: Callable[[], _ResultT]) -> _ResultT: ...


class PlainAuditProgressSession:
    def __init__(self, *, reporter: AuditProgressReporter) -> None:
        self.reporter = reporter

    def run(self, task: Callable[[], _ResultT]) -> _ResultT:
        try:
            return task()
        finally:
            self.reporter.close()
