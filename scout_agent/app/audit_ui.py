from __future__ import annotations

from collections.abc import Callable
from queue import Empty, Queue
from threading import Thread
from typing import Generic, Literal, Protocol, TypeVar, cast

from scout_agent.runtime.audit.reporting import (
    AuditProgressReporter,
    AuditStatusSnapshot,
    QueueAuditProgressSink,
)

AuditUiMode = Literal["tui", "plain"]

_ResultT = TypeVar("_ResultT")
_UNSET = object()


class AuditUiError(RuntimeError):
    """Raised when the audit UI can't be initialized or completed safely."""


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


class TextualAuditProgressSession(Generic[_ResultT]):
    def __init__(self) -> None:
        self._sink = QueueAuditProgressSink()
        self.reporter = AuditProgressReporter(self._sink)
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

        event_queue: Queue = self._sink.event_queue
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
