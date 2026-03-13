from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from scout_agent.domain.audit import (
    AuditState,
    FinalDedupResponse,
    Finding,
)
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.audit_prompts import (
    build_final_dedup_system_prompt,
    build_final_dedup_user_prompt,
)
from scout_agent.runtime.audit.dump import (
    AuditDumpWriter,
    build_preview_text,
    extract_message_text,
)

if TYPE_CHECKING:
    from scout_agent.runtime.audit.graph import AuditContext

FINAL_DEDUP_ACTOR_NAME: Final[str] = "final_dedup"
_SEVERITY_ORDER: Final[dict[str, int]] = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}


@dataclass(frozen=True, slots=True)
class _ParsedFinalDedupResponse:
    response: FinalDedupResponse
    structured_response_present: bool
    used_text_fallback: bool
    final_message_text: str | None


def run_final_finding_dedup(
    *,
    runtime: AuditContext,
    state: AuditState,
) -> AuditState:
    findings = list(state["verified_findings"])
    state["pre_final_dedup_finding_count"] = len(findings)
    state["final_dedup_removed_count"] = 0
    state["final_dedup_status"] = "not_run"

    if len(findings) <= 1:
        reason = "Not enough findings for final dedup."
        runtime.reporter.final_dedup_skipped(reason=reason)
        _record_final_dedup_skipped(
            dump_writer=runtime.dump_writer,
            reason=reason,
            input_finding_count=len(findings),
            output_finding_count=len(findings),
        )
        return state

    runtime.reporter.final_dedup_started(total_findings=len(findings))
    prompt_text = build_final_dedup_user_prompt(
        findings=findings,
        extra_prompt=runtime.extra_prompt,
    )
    preview_text, preview_truncated = build_preview_text(prompt_text)
    _record_final_dedup_started(
        dump_writer=runtime.dump_writer,
        input_finding_count=len(findings),
        input_findings_preview=preview_text,
        input_findings_preview_truncated=preview_truncated,
    )

    try:
        parsed = _invoke_final_dedup(
            runtime=runtime,
            prompt_text=prompt_text,
        )
        groups = _validate_final_dedup_groups(
            response=parsed.response,
            finding_count=len(findings),
        )
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        state["final_dedup_status"] = "skipped"
        runtime.reporter.final_dedup_skipped(reason=reason)
        _record_final_dedup_skipped(
            dump_writer=runtime.dump_writer,
            reason=reason,
            input_finding_count=len(findings),
            output_finding_count=len(findings),
        )
        return state

    deduped_findings = _apply_final_dedup_groups(findings=findings, groups=groups)
    removed_count = len(findings) - len(deduped_findings)
    state["verified_findings"] = deduped_findings
    state["final_dedup_removed_count"] = removed_count
    state["final_dedup_status"] = "applied"
    runtime.reporter.final_dedup_completed(
        remaining_findings=len(deduped_findings),
        removed_count=removed_count,
    )
    _record_final_dedup_completed(
        dump_writer=runtime.dump_writer,
        parsed=parsed,
        input_finding_count=len(findings),
        output_finding_count=len(deduped_findings),
        removed_count=removed_count,
        group_count=len(groups),
    )
    return state


def _invoke_final_dedup(
    *,
    runtime: AuditContext,
    prompt_text: str,
) -> _ParsedFinalDedupResponse:
    from langchain.agents import create_agent
    from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
    from langchain_core.messages import HumanMessage

    system_prompt = build_final_dedup_system_prompt(extra_prompt=runtime.extra_prompt)
    model = build_chat_model(runtime.model_name, runtime.llm_mode)
    agent = create_agent(
        model=model,
        system_prompt=system_prompt,
        middleware=[
            AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        ],
        tools=[],
        response_format=FinalDedupResponse,
        name=FINAL_DEDUP_ACTOR_NAME,
    ).with_config({"recursion_limit": runtime.recursion_limit})

    result = agent.invoke(
        {"messages": [HumanMessage(content=prompt_text)]},
        config={"recursion_limit": runtime.recursion_limit},
    )
    return _parse_final_dedup_response(result=result)


def _parse_final_dedup_response(
    *,
    result: dict[str, object],
) -> _ParsedFinalDedupResponse:
    final_message_text = _extract_final_message_text(result)
    structured = result.get("structured_response")
    if structured is not None:
        response = (
            structured
            if isinstance(structured, FinalDedupResponse)
            else FinalDedupResponse.model_validate(structured)
        )
        return _ParsedFinalDedupResponse(
            response=response,
            structured_response_present=True,
            used_text_fallback=False,
            final_message_text=final_message_text,
        )

    text_response = _parse_text_fallback_response(result)
    if text_response is not None:
        return _ParsedFinalDedupResponse(
            response=text_response,
            structured_response_present=False,
            used_text_fallback=True,
            final_message_text=final_message_text,
        )

    raise ValueError("Final dedup response missing structured output.")


def _parse_text_fallback_response(
    result: dict[str, object],
) -> FinalDedupResponse | None:
    messages = result.get("messages")
    if not isinstance(messages, list) or not messages:
        return None

    last_message = messages[-1]
    content = getattr(last_message, "content", last_message)
    if not isinstance(content, str) or not content.strip():
        return None

    try:
        return FinalDedupResponse.model_validate_json(content)
    except ValueError:
        return None


def _validate_final_dedup_groups(
    *,
    response: FinalDedupResponse,
    finding_count: int,
) -> list[list[int]]:
    seen_indices: set[int] = set()
    normalized_groups: list[list[int]] = []

    for group in response.groups:
        normalized_group: list[int] = []
        for raw_index in group.member_indices:
            if raw_index < 1 or raw_index > finding_count:
                raise ValueError(f"Unknown finding index in final dedup: {raw_index}")
            normalized_index = raw_index - 1
            if normalized_index in seen_indices:
                raise ValueError(
                    f"Duplicate finding index in final dedup: {raw_index}"
                )
            seen_indices.add(normalized_index)
            normalized_group.append(normalized_index)
        if not normalized_group:
            raise ValueError("Final dedup group cannot be empty.")
        normalized_groups.append(normalized_group)

    if len(seen_indices) != finding_count:
        raise ValueError("Final dedup response must include every finding exactly once.")

    return normalized_groups


def _apply_final_dedup_groups(
    *,
    findings: list[Finding],
    groups: list[list[int]],
) -> list[Finding]:
    deduped_indices: list[int] = []
    for group in sorted(groups, key=min):
        deduped_indices.append(_select_canonical_index(findings=findings, group=group))
    return [findings[index] for index in deduped_indices]


def _select_canonical_index(
    *,
    findings: list[Finding],
    group: list[int],
) -> int:
    return min(
        group,
        key=lambda index: (
            _SEVERITY_ORDER[findings[index].severity],
            index,
        ),
    )


def _extract_final_message_text(result: dict[str, object]) -> str | None:
    messages = result.get("messages")
    if isinstance(messages, list) and messages:
        extracted = extract_message_text(messages[-1])
        if extracted:
            return extracted

    extracted = extract_message_text(result.get("output"))
    if extracted:
        return extracted
    return None


def _record_final_dedup_started(
    *,
    dump_writer: AuditDumpWriter | None,
    input_finding_count: int,
    input_findings_preview: str | None,
    input_findings_preview_truncated: bool | None,
) -> None:
    if dump_writer is None:
        return
    dump_writer.record_repo_event(
        actor_name=FINAL_DEDUP_ACTOR_NAME,
        event_type="started",
        input_finding_count=input_finding_count,
        input_findings_preview=input_findings_preview,
        input_findings_preview_truncated=input_findings_preview_truncated,
    )


def _record_final_dedup_completed(
    *,
    dump_writer: AuditDumpWriter | None,
    parsed: _ParsedFinalDedupResponse,
    input_finding_count: int,
    output_finding_count: int,
    removed_count: int,
    group_count: int,
) -> None:
    if dump_writer is None:
        return
    dump_writer.record_repo_event(
        actor_name=FINAL_DEDUP_ACTOR_NAME,
        event_type="response_received",
        structured_response_present=parsed.structured_response_present,
        used_text_fallback=parsed.used_text_fallback,
        parse_failed=False,
        input_finding_count=input_finding_count,
        output_finding_count=output_finding_count,
        removed_count=removed_count,
        group_count=group_count,
        final_message_text=parsed.final_message_text,
        final_message_truncated=False if parsed.final_message_text else None,
    )
    dump_writer.record_repo_event(
        actor_name=FINAL_DEDUP_ACTOR_NAME,
        event_type="completed",
        input_finding_count=input_finding_count,
        output_finding_count=output_finding_count,
        removed_count=removed_count,
        group_count=group_count,
    )


def _record_final_dedup_skipped(
    *,
    dump_writer: AuditDumpWriter | None,
    reason: str,
    input_finding_count: int,
    output_finding_count: int,
) -> None:
    if dump_writer is None:
        return
    dump_writer.record_repo_event(
        actor_name=FINAL_DEDUP_ACTOR_NAME,
        event_type="skipped",
        reason=reason,
        input_finding_count=input_finding_count,
        output_finding_count=output_finding_count,
    )
