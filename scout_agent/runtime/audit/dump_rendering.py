from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any


_MARKDOWN_AI_TEXT_LIMIT = 4000
_READABLE_TOOL_NAMES = {
    "read": "read file",
    "read_file": "read file",
    "read_code_chunk": "read code chunk",
    "task": "dispatch subagent task",
}


def write_actor_timeline_markdown(
    *,
    path: Path,
    title: str,
    relative_path: str,
    actor_name: str,
    events: list[dict[str, Any]],
    missing_message_label: str,
) -> None:
    lines = [
        f"# {title}",
        "",
        f"- File: `{relative_path}`",
        f"- Actor: `{actor_name}`",
        "",
    ]
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
            label = actor_run_id or "unknown"
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

    return sorted(grouped.values(), key=lambda group: _first_seq(group))


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
