from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import uuid4

from scout_agent.domain.audit import Finding
from scout_agent.runtime.audit.dump_rendering import render_dump_artifacts
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
class _FileSummary:
    relative_path: str
    status: str = "running"
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None
    spawned_experts: list[str] = field(default_factory=list)
    findings_count: int = 0
    supervisor_tool_calls: int = 0
    expert_tool_calls: int = 0
    used_text_fallback: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "spawned_experts": list(self.spawned_experts),
            "findings_count": self.findings_count,
            "supervisor_tool_calls": self.supervisor_tool_calls,
            "expert_tool_calls": self.expert_tool_calls,
            "used_text_fallback": self.used_text_fallback,
        }


class AuditDumpWriter:
    def __init__(
        self,
        *,
        run_id: str,
        root_dir: Path,
        project_root: Path,
        facts_path: Path,
        report_path: Path,
        model_name: str,
        llm_mode: str,
        files_total: int,
    ) -> None:
        self.run_id = run_id
        self.root_dir = root_dir.resolve()
        self._project_root = project_root.resolve()
        self._facts_path = facts_path.resolve()
        self._report_path = report_path.resolve()
        self._model_name = model_name
        self._llm_mode = llm_mode
        self._files_total = files_total
        self._files_completed = 0
        self._started_at = utc_now_iso()
        self._finished_at: str | None = None
        self._status = "running"
        self._seq = 0
        self._lock = RLock()
        self._file_summaries: dict[str, _FileSummary] = {}
        self._supervisor_run_ids: dict[str, str] = {}

        self.root_dir.mkdir(parents=True, exist_ok=True)
        (self.root_dir / "files").mkdir(parents=True, exist_ok=True)
        self._write_run_metadata()
        render_dump_artifacts(self.root_dir)

    @classmethod
    def create(
        cls,
        *,
        project_root: Path,
        facts_path: Path,
        report_path: Path,
        model_name: str,
        llm_mode: str,
        files_total: int,
    ) -> AuditDumpWriter:
        run_id = generate_audit_dump_run_id()
        root_dir = project_root.resolve() / ".scout-ai" / "audit-dumps" / run_id
        return cls(
            run_id=run_id,
            root_dir=root_dir,
            project_root=project_root,
            facts_path=facts_path,
            report_path=report_path,
            model_name=model_name,
            llm_mode=llm_mode,
            files_total=files_total,
        )

    def close(self) -> None:
        return None

    def finalize_run(self, *, status: str) -> None:
        with self._lock:
            self._status = status
            self._finished_at = utc_now_iso()
            self._write_run_metadata()
            render_dump_artifacts(self.root_dir)

    def file_started(self, *, relative_path: str) -> None:
        with self._lock:
            normalized_path = _normalize_relative_path(relative_path)
            summary = _FileSummary(relative_path=normalized_path)
            self._file_summaries[normalized_path] = summary
            self._supervisor_run_ids[normalized_path] = uuid4().hex
            self._write_file_summary(summary)
            render_dump_artifacts(self.root_dir, relative_path=normalized_path)

    def file_completed(self, *, relative_path: str) -> None:
        with self._lock:
            summary = self._require_summary(relative_path)
            summary.status = "completed"
            summary.finished_at = utc_now_iso()
            self._files_completed += 1
            self._write_file_summary(summary)
            self._write_run_metadata()
            render_dump_artifacts(self.root_dir, relative_path=summary.relative_path)

    def file_failed(self, *, relative_path: str) -> None:
        with self._lock:
            summary = self._file_summaries.get(_normalize_relative_path(relative_path))
            if summary is None:
                return
            summary.status = "failed"
            summary.finished_at = utc_now_iso()
            self._write_file_summary(summary)
            render_dump_artifacts(self.root_dir, relative_path=summary.relative_path)

    def finding_verified(self, *, relative_path: str) -> None:
        with self._lock:
            summary = self._require_summary(relative_path)
            summary.findings_count += 1
            self._write_file_summary(summary)
            render_dump_artifacts(self.root_dir, relative_path=summary.relative_path)

    def supervisor_started(
        self,
        *,
        relative_path: str,
        actor_run_id: str | None = None,
    ) -> None:
        self._append_event(
            relative_path=relative_path,
            actor_type="supervisor",
            actor_name="supervisor",
            actor_run_id=actor_run_id or self._supervisor_actor_run_id(relative_path),
            event_type="started",
        )

    def supervisor_tool_used(
        self,
        *,
        relative_path: str,
        tool_name: str,
        target: str,
        line_start: int | None = None,
        line_end: int | None = None,
        max_lines: int | None = None,
        offset: int | None = None,
        limit: int | None = None,
        preview_text: str | None = None,
        preview_truncated: bool | None = None,
        actor_run_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        with self._lock:
            summary = self._require_summary(relative_path)
            summary.supervisor_tool_calls += 1
            self._write_file_summary(summary)

        self._append_event(
            relative_path=relative_path,
            actor_type="supervisor",
            actor_name="supervisor",
            actor_run_id=actor_run_id or self._supervisor_actor_run_id(relative_path),
            event_type="tool_used",
            payload={
                "tool_name": tool_name,
                "target": target,
                "line_start": line_start,
                "line_end": line_end,
                "max_lines": max_lines,
                "offset": offset,
                "limit": limit,
                "preview_text": preview_text,
                "preview_truncated": preview_truncated,
                "tool_call_id": tool_call_id,
            },
        )

    def supervisor_tool_denied(
        self,
        *,
        relative_path: str,
        tool_name: str,
        target: str,
        current_file: str,
        reason: str,
        actor_run_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        self._append_event(
            relative_path=relative_path,
            actor_type="supervisor",
            actor_name="supervisor",
            actor_run_id=actor_run_id or self._supervisor_actor_run_id(relative_path),
            event_type="tool_denied",
            payload={
                "tool_name": tool_name,
                "target": target,
                "current_file": current_file,
                "reason": reason,
                "tool_call_id": tool_call_id,
            },
        )

    def supervisor_delegated_expert(
        self,
        *,
        relative_path: str,
        expert_name: str,
        actor_run_id: str | None = None,
        expert_actor_run_id: str | None = None,
    ) -> None:
        with self._lock:
            summary = self._require_summary(relative_path)
            if expert_name not in summary.spawned_experts:
                summary.spawned_experts.append(expert_name)
                self._write_file_summary(summary)

        self._append_event(
            relative_path=relative_path,
            actor_type="supervisor",
            actor_name="supervisor",
            actor_run_id=actor_run_id or self._supervisor_actor_run_id(relative_path),
            event_type="delegated_expert",
            payload={
                "expert_name": expert_name,
                "expert_actor_run_id": expert_actor_run_id,
            },
        )

    def supervisor_response_received(
        self,
        *,
        relative_path: str,
        structured_response_present: bool,
        used_text_fallback: bool,
        finding_count: int,
        parse_failed: bool,
        final_message_text: str | None = None,
        final_message_truncated: bool | None = None,
        actor_run_id: str | None = None,
    ) -> None:
        with self._lock:
            summary = self._require_summary(relative_path)
            summary.used_text_fallback = (
                summary.used_text_fallback or used_text_fallback
            )
            self._write_file_summary(summary)

        self._append_event(
            relative_path=relative_path,
            actor_type="supervisor",
            actor_name="supervisor",
            actor_run_id=actor_run_id or self._supervisor_actor_run_id(relative_path),
            event_type="response_received",
            payload={
                "structured_response_present": structured_response_present,
                "used_text_fallback": used_text_fallback,
                "finding_count": finding_count,
                "parse_failed": parse_failed,
                "final_message_text": final_message_text,
                "final_message_truncated": final_message_truncated,
            },
        )

    def supervisor_completed(
        self,
        *,
        relative_path: str,
        actor_run_id: str | None = None,
    ) -> None:
        self._append_event(
            relative_path=relative_path,
            actor_type="supervisor",
            actor_name="supervisor",
            actor_run_id=actor_run_id or self._supervisor_actor_run_id(relative_path),
            event_type="completed",
        )

    def expert_started(
        self,
        *,
        relative_path: str,
        expert_name: str,
        actor_run_id: str,
    ) -> None:
        self.supervisor_delegated_expert(
            relative_path=relative_path,
            expert_name=expert_name,
            expert_actor_run_id=actor_run_id,
        )
        self._append_event(
            relative_path=relative_path,
            actor_type="expert",
            actor_name=expert_name,
            actor_run_id=actor_run_id,
            event_type="started",
        )

    def expert_tool_used(
        self,
        *,
        relative_path: str,
        expert_name: str,
        tool_name: str,
        target: str,
        line_start: int | None = None,
        line_end: int | None = None,
        max_lines: int | None = None,
        offset: int | None = None,
        limit: int | None = None,
        preview_text: str | None = None,
        preview_truncated: bool | None = None,
        actor_run_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        with self._lock:
            summary = self._require_summary(relative_path)
            summary.expert_tool_calls += 1
            self._write_file_summary(summary)

        self._append_event(
            relative_path=relative_path,
            actor_type="expert",
            actor_name=expert_name,
            actor_run_id=actor_run_id,
            event_type="tool_used",
            payload={
                "tool_name": tool_name,
                "target": target,
                "line_start": line_start,
                "line_end": line_end,
                "max_lines": max_lines,
                "offset": offset,
                "limit": limit,
                "preview_text": preview_text,
                "preview_truncated": preview_truncated,
                "tool_call_id": tool_call_id,
            },
        )

    def expert_tool_denied(
        self,
        *,
        relative_path: str,
        expert_name: str,
        tool_name: str,
        target: str,
        current_file: str,
        reason: str,
        actor_run_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        self._append_event(
            relative_path=relative_path,
            actor_type="expert",
            actor_name=expert_name,
            actor_run_id=actor_run_id,
            event_type="tool_denied",
            payload={
                "tool_name": tool_name,
                "target": target,
                "current_file": current_file,
                "reason": reason,
                "tool_call_id": tool_call_id,
            },
        )

    def expert_result(
        self,
        *,
        relative_path: str,
        expert_name: str,
        status: str,
        finding: Finding | None,
        actor_run_id: str | None = None,
        final_message_text: str | None = None,
        final_message_truncated: bool | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "status": status,
            "final_message_text": final_message_text,
            "final_message_truncated": final_message_truncated,
        }
        if finding is not None:
            payload["finding_summary"] = {
                "pattern": finding.pattern,
                "severity": finding.severity,
                "location": finding.location,
            }

        self._append_event(
            relative_path=relative_path,
            actor_type="expert",
            actor_name=expert_name,
            actor_run_id=actor_run_id,
            event_type="result",
            payload=payload,
        )

    def expert_completed(
        self,
        *,
        relative_path: str,
        expert_name: str,
        actor_run_id: str | None = None,
    ) -> None:
        self._append_event(
            relative_path=relative_path,
            actor_type="expert",
            actor_name=expert_name,
            actor_run_id=actor_run_id,
            event_type="completed",
        )

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
            event = {
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
            actor_path = self._actor_file_path(
                relative_path=normalized_path,
                actor_type=actor_type,
                actor_name=actor_name,
            )
            actor_path.parent.mkdir(parents=True, exist_ok=True)
            with actor_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
            render_dump_artifacts(self.root_dir, relative_path=normalized_path)

    def _supervisor_actor_run_id(self, relative_path: str) -> str:
        normalized_path = _normalize_relative_path(relative_path)
        return self._supervisor_run_ids.setdefault(normalized_path, uuid4().hex)

    def _actor_file_path(
        self,
        *,
        relative_path: str,
        actor_type: str,
        actor_name: str,
    ) -> Path:
        base_dir = self.root_dir / "files" / PurePosixPath(relative_path)
        if actor_type == "supervisor":
            return base_dir / "supervisor.jsonl"
        return base_dir / "experts" / f"{actor_name}.jsonl"

    def _file_summary_path(self, relative_path: str) -> Path:
        return self.root_dir / "files" / PurePosixPath(relative_path) / "summary.json"

    def _require_summary(self, relative_path: str) -> _FileSummary:
        normalized_path = _normalize_relative_path(relative_path)
        summary = self._file_summaries.get(normalized_path)
        if summary is None:
            summary = _FileSummary(relative_path=normalized_path)
            self._file_summaries[normalized_path] = summary
            self._write_file_summary(summary)
        return summary

    def _write_file_summary(self, summary: _FileSummary) -> None:
        summary_path = self._file_summary_path(summary.relative_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_run_metadata(self) -> None:
        payload = {
            "run_id": self.run_id,
            "status": self._status,
            "project_root": str(self._project_root),
            "facts_path": str(self._facts_path),
            "report_path": str(self._report_path),
            "model_name": self._model_name,
            "llm_mode": self._llm_mode,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "files_total": self._files_total,
            "files_completed": self._files_completed,
        }
        (self.root_dir / "run.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
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
