from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import uuid4

from scout_agent.runtime.audit.dump_rendering import write_actor_timeline_markdown
from scout_agent.runtime.time_utils import utc_now_iso

_CODE_PREVIEW_CHAR_LIMIT = 600
_CODE_PREVIEW_LINE_LIMIT = 12


def generate_audit_dump_run_id() -> str:
    timestamp = (
        datetime.now(UTC)
        .replace(microsecond=0)
        .strftime("%Y%m%dT%H%M%SZ")
    )
    return f"{timestamp}-{uuid4().hex[:8]}"


def build_preview_text(
    value: object,
    *,
    char_limit: int = _CODE_PREVIEW_CHAR_LIMIT,
    line_limit: int = _CODE_PREVIEW_LINE_LIMIT,
) -> tuple[str | None, bool | None]:
    text = _coerce_text(value)
    if text is None:
        return None, None

    lines = text.splitlines()
    selected_lines = lines[:line_limit]
    preview = "\n".join(selected_lines)
    truncated = len(lines) > line_limit

    if len(preview) > char_limit:
        preview = preview[:char_limit].rstrip()
        truncated = True

    return preview or None, truncated


def extract_message_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            part = extract_message_text(item)
            if part:
                parts.append(part)
        if not parts:
            return None
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content", "output"):
            if key in value:
                extracted = extract_message_text(value[key])
                if extracted:
                    return extracted
        return None

    content = getattr(value, "content", None)
    if content is not None:
        extracted = extract_message_text(content)
        if extracted:
            return extracted

    text = getattr(value, "text", None)
    if text is not None:
        extracted = extract_message_text(text)
        if extracted:
            return extracted

    return None


@dataclass(slots=True)
class _FileDump:
    relative_path: str
    supervisor_events: list[dict[str, object]] = field(default_factory=list)
    expert_events_by_name: dict[str, list[dict[str, object]]] = field(
        default_factory=dict
    )


class AuditDumpWriter:
    def __init__(
        self,
        *,
        run_id: str,
        root_dir: Path,
    ) -> None:
        self.run_id = run_id
        self.root_dir = root_dir.resolve()
        self._seq = 0
        self._lock = RLock()
        self._file_dumps: dict[str, _FileDump] = {}
        self._supervisor_run_ids: dict[str, str] = {}
        self._repo_events_by_actor: dict[str, list[dict[str, object]]] = {}
        self._repo_actor_run_ids: dict[str, str] = {}

        self.root_dir.mkdir(parents=True, exist_ok=True)
        (self.root_dir / "files").mkdir(parents=True, exist_ok=True)
        (self.root_dir / "repo").mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(
        cls,
        *,
        project_root: Path,
    ) -> AuditDumpWriter:
        run_id = generate_audit_dump_run_id()
        root_dir = project_root.resolve() / ".scout-ai" / "audit-dumps" / run_id
        return cls(run_id=run_id, root_dir=root_dir)

    def file_started(self, *, relative_path: str) -> None:
        with self._lock:
            normalized_path = _normalize_relative_path(relative_path)
            self._file_dumps[normalized_path] = _FileDump(relative_path=normalized_path)
            self._supervisor_run_ids[normalized_path] = uuid4().hex
            self._write_supervisor_timeline(normalized_path)

    def record_supervisor_event(
        self,
        *,
        relative_path: str,
        event_type: str,
        actor_run_id: str | None = None,
        **payload: object,
    ) -> None:
        self._append_event(
            relative_path=relative_path,
            actor_type="supervisor",
            actor_name="supervisor",
            actor_run_id=actor_run_id or self._supervisor_actor_run_id(relative_path),
            event_type=event_type,
            payload=payload,
        )

    def record_expert_event(
        self,
        *,
        relative_path: str,
        expert_name: str,
        event_type: str,
        actor_run_id: str | None = None,
        **payload: object,
    ) -> None:
        self._append_event(
            relative_path=relative_path,
            actor_type="expert",
            actor_name=expert_name,
            actor_run_id=actor_run_id,
            event_type=event_type,
            payload=payload,
        )

    def record_repo_event(
        self,
        *,
        actor_name: str,
        event_type: str,
        actor_run_id: str | None = None,
        **payload: object,
    ) -> None:
        normalized_actor_name = actor_name.strip()
        if not normalized_actor_name:
            raise ValueError("actor_name must be non-empty")

        with self._lock:
            self._seq += 1
            event: dict[str, object] = {
                "seq": self._seq,
                "timestamp": utc_now_iso(),
                "run_id": self.run_id,
                "file": "repo",
                "actor_type": "repo",
                "actor_name": normalized_actor_name,
                "actor_run_id": actor_run_id
                or self._repo_actor_run_id(normalized_actor_name),
                "event_type": event_type,
            }
            if payload:
                event.update(
                    {key: value for key, value in payload.items() if value is not None}
                )
            events = self._repo_events_by_actor.setdefault(normalized_actor_name, [])
            events.append(event)
            self._write_repo_timeline(normalized_actor_name)

    def _append_event(
        self,
        *,
        relative_path: str,
        actor_type: str,
        actor_name: str,
        event_type: str,
        actor_run_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        normalized_path = _normalize_relative_path(relative_path)
        with self._lock:
            self._seq += 1
            event: dict[str, object] = {
                "seq": self._seq,
                "timestamp": utc_now_iso(),
                "run_id": self.run_id,
                "file": normalized_path,
                "actor_type": actor_type,
                "actor_name": actor_name,
                "actor_run_id": actor_run_id,
                "event_type": event_type,
            }
            if payload:
                event.update(
                    {key: value for key, value in payload.items() if value is not None}
                )
            file_dump = self._require_file_dump(normalized_path)
            if actor_type == "supervisor":
                file_dump.supervisor_events.append(event)
                self._write_supervisor_timeline(normalized_path)
                return

            events = file_dump.expert_events_by_name.setdefault(actor_name, [])
            events.append(event)
            self._write_expert_timeline(normalized_path, actor_name)

    def _supervisor_actor_run_id(self, relative_path: str) -> str:
        normalized_path = _normalize_relative_path(relative_path)
        return self._supervisor_run_ids.setdefault(normalized_path, uuid4().hex)

    def _require_file_dump(self, relative_path: str) -> _FileDump:
        normalized_path = _normalize_relative_path(relative_path)
        file_dump = self._file_dumps.get(normalized_path)
        if file_dump is None:
            file_dump = _FileDump(relative_path=normalized_path)
            self._file_dumps[normalized_path] = file_dump
        return file_dump

    def _write_supervisor_timeline(self, relative_path: str) -> None:
        file_dump = self._require_file_dump(relative_path)
        self._write_actor_timeline(
            path=self._file_dir(relative_path) / "supervisor.timeline.md",
            title=f"Supervisor Timeline: `{relative_path}`",
            actor_name="supervisor",
            events=file_dump.supervisor_events,
            relative_path=file_dump.relative_path,
        )

    def _write_expert_timeline(self, relative_path: str, expert_name: str) -> None:
        file_dump = self._require_file_dump(relative_path)
        self._write_actor_timeline(
            path=self._file_dir(relative_path)
            / "experts"
            / f"{expert_name}.timeline.md",
            title=f"Expert Timeline: `{expert_name}`",
            actor_name=expert_name,
            events=file_dump.expert_events_by_name.get(expert_name, []),
            relative_path=file_dump.relative_path,
        )

    def _repo_actor_run_id(self, actor_name: str) -> str:
        return self._repo_actor_run_ids.setdefault(actor_name, uuid4().hex)

    def _write_repo_timeline(self, actor_name: str) -> None:
        self._write_actor_timeline(
            path=self.root_dir / "repo" / f"{actor_name}.timeline.md",
            title=f"Repository Timeline: `{actor_name}`",
            actor_name=actor_name,
            events=self._repo_events_by_actor.get(actor_name, []),
            relative_path="repo",
        )

    def _file_dir(self, relative_path: str) -> Path:
        return self.root_dir / "files" / PurePosixPath(relative_path)

    def _write_actor_timeline(
        self,
        *,
        path: Path,
        title: str,
        actor_name: str,
        events: list[dict[str, object]],
        relative_path: str,
    ) -> None:
        write_actor_timeline_markdown(
            path=path,
            title=title,
            relative_path=relative_path,
            actor_name=actor_name,
            events=events,
            missing_message_label="Final AI message: not captured in this run.",
        )


def _coerce_text(value: object) -> str | None:
    text = extract_message_text(value)
    if text is not None:
        return text
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return None


def _normalize_relative_path(relative_path: str) -> str:
    raw = relative_path.strip()
    if not raw:
        raise ValueError("relative_path must be non-empty")
    normalized = PurePosixPath(raw).as_posix()
    if normalized.startswith("/"):
        return normalized.removeprefix("/")
    return normalized
