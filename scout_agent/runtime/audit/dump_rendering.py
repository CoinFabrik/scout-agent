from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


_MARKDOWN_AI_TEXT_LIMIT = 4000
_READABLE_TOOL_NAMES = {
    "read": "read file",
    "read_file": "read file",
    "read_code_chunk": "read code chunk",
    "task": "dispatch subagent task",
}


def render_dump_artifacts(
    root_dir: Path,
    *,
    relative_path: str | None = None,
) -> None:
    resolved_root = root_dir.resolve()
    run_path = resolved_root / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"Dump directory does not contain run.json: {resolved_root}")

    run_payload = _load_json(run_path)
    summaries = _load_all_summaries(resolved_root)
    _write_index_markdown(
        root_dir=resolved_root,
        run_payload=run_payload,
        summaries=summaries,
    )

    if relative_path is None:
        target_paths = sorted(summaries)
    else:
        target_paths = [_normalize_relative_path(relative_path)]

    for target_path in target_paths:
        _write_file_markdown_bundle(
            root_dir=resolved_root,
            relative_path=target_path,
            summary_payload=summaries.get(target_path, {}),
        )


def _write_index_markdown(
    *,
    root_dir: Path,
    run_payload: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
) -> None:
    lines = [
        "# Audit Dump",
        "",
        f"- Run ID: `{run_payload.get('run_id', 'unknown')}`",
        f"- Status: `{run_payload.get('status', 'unknown')}`",
        f"- Model: `{run_payload.get('model_name', 'unknown')}`",
        f"- LLM mode: `{run_payload.get('llm_mode', 'unknown')}`",
        f"- Started: `{run_payload.get('started_at', 'unknown')}`",
        f"- Finished: `{run_payload.get('finished_at', 'running')}`",
        (
            "- Progress: "
            f"{run_payload.get('files_completed', 0)}/{run_payload.get('files_total', 0)} file(s)"
        ),
        "",
    ]

    if not summaries:
        lines.append("No file dump data captured yet.")
        _write_markdown(root_dir / "index.md", lines)
        return

    lines.extend(
        [
            "| File | Status | Findings | Spawned Experts |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for relative_path in sorted(summaries):
        summary = summaries[relative_path]
        experts = ", ".join(summary.get("spawned_experts", [])) or "-"
        file_link = _markdown_relative_file_link(relative_path)
        lines.append(
            "| "
            f"[`{relative_path}`]({file_link}) | "
            f"{summary.get('status', 'unknown')} | "
            f"{summary.get('findings_count', 0)} | "
            f"{experts} |"
        )

    _write_markdown(root_dir / "index.md", lines)


def _write_file_markdown_bundle(
    *,
    root_dir: Path,
    relative_path: str,
    summary_payload: dict[str, Any],
) -> None:
    file_dir = root_dir / "files" / PurePosixPath(relative_path)
    supervisor_events = _load_jsonl(file_dir / "supervisor.jsonl")
    expert_paths = sorted((file_dir / "experts").glob("*.jsonl")) if (file_dir / "experts").exists() else []
    expert_events_by_name = {
        expert_path.stem: _load_jsonl(expert_path)
        for expert_path in expert_paths
    }

    legacy_dump = _is_legacy_dump(supervisor_events, expert_events_by_name)
    ordered_experts = sorted(
        expert_events_by_name,
        key=lambda name: _first_seq(expert_events_by_name[name]),
    )
    _remove_stale_readable_markdown(file_dir)
    _write_file_markdown(
        file_dir=file_dir,
        relative_path=relative_path,
        summary_payload=summary_payload,
        expert_names=ordered_experts,
    )
    _write_actor_timeline_markdown(
        path=file_dir / "supervisor.timeline.md",
        title=f"Supervisor Timeline: `{relative_path}`",
        relative_path=relative_path,
        actor_name="supervisor",
        events=supervisor_events,
        legacy_dump=legacy_dump,
        missing_message_label="Final AI message: not captured in this run.",
    )
    for expert_name in ordered_experts:
        _write_actor_timeline_markdown(
            path=file_dir / "experts" / f"{expert_name}.timeline.md",
            title=f"Expert Timeline: `{expert_name}`",
            relative_path=relative_path,
            actor_name=expert_name,
            events=expert_events_by_name[expert_name],
            legacy_dump=legacy_dump,
            missing_message_label="Final AI message: not captured in this run.",
        )


def _write_file_markdown(
    *,
    file_dir: Path,
    relative_path: str,
    summary_payload: dict[str, Any],
    expert_names: list[str],
) -> None:
    lines = [
        f"# File Dump: `{relative_path}`",
        "",
        "## Summary",
        "",
        f"- Status: `{summary_payload.get('status', 'unknown')}`",
        f"- Started: `{summary_payload.get('started_at', 'unknown')}`",
        f"- Finished: `{summary_payload.get('finished_at', 'running')}`",
        f"- Findings accepted: `{summary_payload.get('findings_count', 0)}`",
        f"- Supervisor tool calls: `{summary_payload.get('supervisor_tool_calls', 0)}`",
        f"- Expert tool calls: `{summary_payload.get('expert_tool_calls', 0)}`",
        f"- Spawned experts: `{', '.join(summary_payload.get('spawned_experts', [])) or '-'}`",
        f"- Used text fallback: `{summary_payload.get('used_text_fallback', False)}`",
        "",
        "## Readable Artifacts",
        "",
        "- [Supervisor timeline](./supervisor.timeline.md)",
    ]

    if expert_names:
        for expert_name in expert_names:
            lines.append(
                f"- [Expert timeline: `{expert_name}`](./experts/{expert_name}.timeline.md)"
            )
    else:
        lines.append("- No expert timelines captured yet.")

    _write_markdown(file_dir / "file.md", lines)


def _write_actor_timeline_markdown(
    *,
    path: Path,
    title: str,
    relative_path: str,
    actor_name: str,
    events: list[dict[str, Any]],
    legacy_dump: bool,
    missing_message_label: str,
) -> None:
    lines = [
        f"# {title}",
        "",
        f"- File: `{relative_path}`",
        f"- Actor: `{actor_name}`",
        "",
    ]

    if legacy_dump:
        lines.extend(
            [
                "> Legacy dump: actor correlation IDs, line ranges, previews, or final AI text may be missing.",
                "",
            ]
        )

    lines.extend(
        _render_actor_section(
            title="Timeline",
            events=events,
            missing_message_label=missing_message_label,
        )
    )

    _write_markdown(path, lines)


def _render_actor_section(
    *,
    title: str,
    events: list[dict[str, Any]],
    missing_message_label: str,
) -> list[str]:
    lines = [f"## {title}", ""]
    if not events:
        lines.extend(["No events captured yet.", ""])
        return lines

    groups = _group_events_by_actor_run(events)
    multiple_groups = len(groups) > 1
    for index, group in enumerate(groups, start=1):
        if multiple_groups:
            actor_run_id = group[0].get("actor_run_id")
            label = actor_run_id or "legacy"
            lines.extend([f"### Pass {index}", "", f"- Actor run id: `{label}`", ""])

        for event in group:
            event_type = event.get("event_type")
            if event_type == "started":
                lines.append(f"- Started at `{event.get('timestamp', 'unknown')}`")
                continue
            if event_type == "delegated_expert":
                lines.append(
                    f"- Delegated expert: `{event.get('expert_name', 'unknown')}`"
                )
                continue
            if event_type == "tool_used":
                lines.extend(_render_tool_event(event))
                continue
            if event_type == "tool_denied":
                lines.extend(_render_tool_denied_event(event))
                continue
            if event_type == "response_received":
                lines.extend(
                    _render_response_event(
                        event,
                        missing_message_label=missing_message_label,
                    )
                )
                continue
            if event_type == "result":
                lines.extend(
                    _render_result_event(
                        event,
                        missing_message_label=missing_message_label,
                    )
                )
                continue

        lines.append("")

    return lines


def _render_tool_event(event: dict[str, Any]) -> list[str]:
    tool_name = str(event.get("tool_name", "unknown"))
    label = _READABLE_TOOL_NAMES.get(tool_name, tool_name.replace("_", " "))
    target = str(event.get("target", "not captured"))
    details: list[str] = []
    line_start = event.get("line_start")
    line_end = event.get("line_end")
    max_lines = event.get("max_lines")
    if line_start is not None and line_end is not None:
        details.append(f"lines {line_start}-{line_end}")
    elif max_lines is not None:
        details.append(f"max_lines {max_lines}")

    offset = event.get("offset")
    limit = event.get("limit")
    if offset is not None:
        details.append(f"offset {offset}")
    if limit is not None:
        details.append(f"limit {limit}")

    tool_call_id = event.get("tool_call_id")
    if tool_call_id:
        details.append(f"tool_call_id `{tool_call_id}`")

    bullet = f"- {label.capitalize()} on `{target}`"
    if details:
        bullet += f" ({', '.join(details)})"

    lines = [bullet]
    lines.extend(_render_preview_block(event))
    return lines


def _render_tool_denied_event(event: dict[str, Any]) -> list[str]:
    tool_name = str(event.get("tool_name", "unknown"))
    label = _READABLE_TOOL_NAMES.get(tool_name, tool_name.replace("_", " "))
    target = str(event.get("target", "not captured"))
    reason = str(event.get("reason", "unknown"))
    return [f"- {label.capitalize()} denied on `{target}`: {reason}"]


def _render_response_event(
    event: dict[str, Any],
    *,
    missing_message_label: str,
) -> list[str]:
    lines = [
        (
            "- Response received: "
            f"structured={event.get('structured_response_present', False)} "
            f"fallback={event.get('used_text_fallback', False)} "
            f"findings={event.get('finding_count', 0)} "
            f"parse_failed={event.get('parse_failed', False)}"
        )
    ]
    lines.extend(
        _render_ai_text_block(
            event,
            missing_message_label=missing_message_label,
        )
    )
    return lines


def _render_result_event(
    event: dict[str, Any],
    *,
    missing_message_label: str,
) -> list[str]:
    status = str(event.get("status", "unknown"))
    finding_summary = event.get("finding_summary")
    bullet = f"- Result: `{status}`"
    if isinstance(finding_summary, dict) and finding_summary:
        bullet += (
            " "
            f"at `{finding_summary.get('location', 'unknown')}`: "
            f"{finding_summary.get('pattern', 'unknown')}"
        )
    lines = [bullet]
    lines.extend(
        _render_ai_text_block(
            event,
            missing_message_label=missing_message_label,
        )
    )
    return lines


def _render_preview_block(event: dict[str, Any]) -> list[str]:
    preview_text = event.get("preview_text")
    if not isinstance(preview_text, str) or not preview_text:
        return ["Preview: not captured in this run."]

    lines = [
        "",
        "```text",
        preview_text.rstrip(),
        "```",
    ]
    if event.get("preview_truncated"):
        lines.append("_Preview truncated._")
    return lines


def _render_ai_text_block(
    event: dict[str, Any],
    *,
    missing_message_label: str,
) -> list[str]:
    final_message_text = event.get("final_message_text")
    if not isinstance(final_message_text, str) or not final_message_text.strip():
        return [missing_message_label]

    message_text = final_message_text.strip()
    truncated = False
    if len(message_text) > _MARKDOWN_AI_TEXT_LIMIT:
        message_text = message_text[:_MARKDOWN_AI_TEXT_LIMIT].rstrip()
        truncated = True

    lines = ["", "Final AI message:"]
    lines.extend(f"> {line}" if line else ">" for line in message_text.splitlines())
    if truncated or event.get("final_message_truncated"):
        lines.append("> [truncated in Markdown]")
    return lines


def _group_events_by_actor_run(
    events: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    ordered_events = sorted(events, key=_event_sort_key)
    actor_run_ids = [event.get("actor_run_id") for event in ordered_events]
    if not actor_run_ids or any(actor_run_id is None for actor_run_id in actor_run_ids):
        return [ordered_events]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in ordered_events:
        grouped[str(event["actor_run_id"])].append(event)

    return sorted(
        grouped.values(),
        key=lambda group: _first_seq(group),
    )


def _is_legacy_dump(
    supervisor_events: list[dict[str, Any]],
    expert_events_by_name: dict[str, list[dict[str, Any]]],
) -> bool:
    all_events = list(supervisor_events)
    for events in expert_events_by_name.values():
        all_events.extend(events)
    if not all_events:
        return False
    return any("actor_run_id" not in event for event in all_events)


def _remove_stale_readable_markdown(file_dir: Path) -> None:
    legacy_timeline = file_dir / "timeline.md"
    if legacy_timeline.exists():
        legacy_timeline.unlink()

    experts_dir = file_dir / "experts"
    if not experts_dir.exists():
        return

    for expert_timeline in experts_dir.glob("*.timeline.md"):
        expert_timeline.unlink()


def _markdown_relative_file_link(relative_path: str) -> str:
    return f"./files/{PurePosixPath(relative_path).as_posix()}/file.md"


def _load_all_summaries(root_dir: Path) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for summary_path in sorted((root_dir / "files").glob("**/summary.json")):
        payload = _load_json(summary_path)
        relative_path = payload.get("relative_path")
        if isinstance(relative_path, str) and relative_path.strip():
            summaries[_normalize_relative_path(relative_path)] = payload
    return summaries


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        events.append(json.loads(stripped))
    return events


def _write_markdown(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _event_sort_key(event: dict[str, Any]) -> tuple[int, str]:
    seq = event.get("seq")
    if not isinstance(seq, int):
        seq = 0
    timestamp = str(event.get("timestamp", ""))
    return seq, timestamp


def _first_seq(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    seq = events[0].get("seq")
    return seq if isinstance(seq, int) else 0


def _normalize_relative_path(relative_path: str) -> str:
    raw = relative_path.strip()
    if not raw:
        raise ValueError("relative_path must be non-empty")
    normalized = PurePosixPath(raw).as_posix()
    if normalized.startswith("/"):
        return normalized.removeprefix("/")
    return normalized
