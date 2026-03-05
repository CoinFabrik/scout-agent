from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Generic, Literal, Protocol, TypeVar, cast

from scout_agent.domain.audit import Finding
from scout_agent.runtime.audit.reporting import (
    AuditProgressReporter,
    PlainAuditProgressReporter,
    format_audit_expert_spawned_line,
    format_audit_file_completed_line,
    format_audit_file_started_line,
    format_audit_finding_verified_line,
    format_audit_started_line,
    format_audit_tool_denied_line,
    format_audit_tool_used_line,
)

AuditUiMode = Literal["tui", "plain"]

_ResultT = TypeVar("_ResultT")
_UNSET = object()


class AuditUiError(RuntimeError):
    """Raised when the audit UI can't be initialized or completed safely."""


class AuditProgressSession(Protocol):
    reporter: AuditProgressReporter

    def run(self, task: Callable[[], _ResultT]) -> _ResultT: ...


@dataclass(frozen=True, slots=True)
class AuditStatusSnapshot:
    current_file: str = ""
    reviewed: int = 0
    total: int = 0
    verified_findings: int = 0


@dataclass(frozen=True, slots=True)
class _AuditUiEvent:
    line: str | None
    status: AuditStatusSnapshot
    close: bool = False


class PlainAuditProgressSession:
    def __init__(self, *, reporter: PlainAuditProgressReporter) -> None:
        self.reporter = reporter
        self._closed = False

    def run(self, task: Callable[[], _ResultT]) -> _ResultT:
        try:
            return task()
        finally:
            self._close_reporter()

    def _close_reporter(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.reporter.close()


class TextualAuditProgressReporter:
    def __init__(
        self,
        *,
        event_queue: Queue[_AuditUiEvent] | None = None,
    ) -> None:
        self._event_queue = event_queue or Queue()
        self._status = AuditStatusSnapshot()
        self._closed = False

    @property
    def event_queue(self) -> Queue[_AuditUiEvent]:
        return self._event_queue

    @property
    def status_snapshot(self) -> AuditStatusSnapshot:
        return self._status

    def started(
        self,
        *,
        project_root: Path,
        total_files: int,
        model_name: str,
        llm_mode: str,
    ) -> None:
        self._status = replace(self._status, total=total_files)
        self._emit(
            line=format_audit_started_line(
                project_root=project_root,
                total_files=total_files,
                model_name=model_name,
                llm_mode=llm_mode,
            )
        )

    def file_started(
        self,
        *,
        index: int,
        total: int,
        current_file: str,
    ) -> None:
        self._status = replace(
            self._status,
            current_file=current_file,
            total=total,
        )
        self._emit(
            line=format_audit_file_started_line(
                index=index,
                total=total,
                current_file=current_file,
            )
        )

    def finding_verified(
        self,
        *,
        total_verified_findings: int,
        finding: Finding,
    ) -> None:
        self._status = replace(
            self._status,
            verified_findings=total_verified_findings,
        )
        self._emit(
            line=format_audit_finding_verified_line(
                total_verified_findings=total_verified_findings,
                finding=finding,
            )
        )

    def file_completed(
        self,
        *,
        reviewed: int,
        total: int,
        current_file: str,
    ) -> None:
        self._status = replace(
            self._status,
            current_file=current_file,
            reviewed=reviewed,
            total=total,
        )
        self._emit(
            line=format_audit_file_completed_line(
                reviewed=reviewed,
                total=total,
                current_file=current_file,
            )
        )

    def expert_spawned(
        self,
        *,
        expert_name: str,
    ) -> None:
        self._emit(
            line=format_audit_expert_spawned_line(
                expert_name=expert_name,
            )
        )

    def tool_used(
        self,
        *,
        tool_name: str,
        target: str,
        expert_name: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> None:
        self._emit(
            line=format_audit_tool_used_line(
                tool_name=tool_name,
                target=target,
                expert_name=expert_name,
                line_start=line_start,
                line_end=line_end,
                offset=offset,
                limit=limit,
            )
        )

    def tool_denied(
        self,
        *,
        tool_name: str,
        target: str,
        current_file: str,
        reason: str,
        expert_name: str | None = None,
    ) -> None:
        self._emit(
            line=format_audit_tool_denied_line(
                tool_name=tool_name,
                target=target,
                current_file=current_file,
                reason=reason,
                expert_name=expert_name,
            )
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._event_queue.put(
            _AuditUiEvent(
                line=None,
                status=self._status,
                close=True,
            )
        )

    def _emit(self, *, line: str) -> None:
        self._event_queue.put(
            _AuditUiEvent(
                line=line,
                status=self._status,
            )
        )


class TextualAuditProgressSession(Generic[_ResultT]):
    def __init__(self) -> None:
        self.reporter = TextualAuditProgressReporter()
        self._result: object = _UNSET
        self._exception: BaseException | None = None

    def run(self, task: Callable[[], _ResultT]) -> _ResultT:
        self._result = _UNSET
        self._exception = None

        app = self._create_app()
        worker = Thread(
            target=self._run_task,
            args=(task,),
            name="audit-progress-ui",
            daemon=True,
        )
        worker.start()

        try:
            app.run()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise AuditUiError(f"Textual UI failed: {exc}") from exc
        finally:
            self.reporter.close()

        if self._exception is not None:
            raise self._exception
        if self._result is _UNSET:
            raise AuditUiError("Audit UI exited before the audit task returned.")
        return cast(_ResultT, self._result)

    def _run_task(self, task: Callable[[], _ResultT]) -> None:
        try:
            self._result = task()
        except BaseException as exc:
            self._exception = exc
        finally:
            self.reporter.close()

    def _create_app(self):
        try:
            from textual.app import App, ComposeResult
            from textual.widgets import Log, Static
        except ImportError as exc:  # pragma: no cover - depends on installed extras
            raise AuditUiError(
                "Textual UI requested but the 'textual' package is not installed."
            ) from exc

        event_queue = self.reporter.event_queue
        initial_status = self.reporter.status_snapshot

        class AuditProgressApp(App[None]):
            CSS = """
            Screen {
                layout: vertical;
                overflow: hidden hidden;
                background: $surface;
            }

            #status {
                width: 100%;
                height: 1;
                padding: 0 1;
                background: $surface;
                color: $text;
            }

            #log {
                width: 100%;
                height: 1fr;
                background: $surface;
                color: $text;
                overflow-x: hidden;
                scrollbar-visibility: hidden;
            }
            """

            def __init__(self) -> None:
                super().__init__()
                self._status = initial_status

            def compose(self) -> ComposeResult:
                yield Static(_render_status_line(self._status), id="status")
                yield Log(id="log")

            def on_mount(self) -> None:
                self.query_one(Log).auto_scroll = True
                self.set_interval(1 / 20, self._drain_events)

            def _drain_events(self) -> None:
                status_widget = self.query_one("#status", Static)
                log_widget = self.query_one(Log)
                should_exit = False
                status_changed = False

                while True:
                    try:
                        event = event_queue.get_nowait()
                    except Empty:
                        break

                    if event.line is not None:
                        log_widget.write_line(event.line)
                    if event.status != self._status:
                        self._status = event.status
                        status_changed = True
                    if event.close:
                        should_exit = True

                if status_changed:
                    status_widget.update(_render_status_line(self._status))
                if should_exit:
                    self.exit()

        return AuditProgressApp()


def _render_status_line(status: AuditStatusSnapshot) -> str:
    current_file = status.current_file or "-"
    return (
        f"File: {current_file} | "
        f"Reviewed: {status.reviewed}/{status.total} | "
        f"Findings: {status.verified_findings}"
    )
