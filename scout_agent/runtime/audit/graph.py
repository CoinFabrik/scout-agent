from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Collection

from scout_agent.app.console_reporting import PlainAuditProgressReporter
from scout_agent.domain.audit import AuditState, FileAuditResponse, Finding
from scout_agent.domain.facts import (
    FactsDocument,
    FunctionSummary,
    build_file_fact_index,
    file_path_from_function_key,
    present_summary_fields,
)
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.experts import CompiledSubAgent, build_expert_subagents
from scout_agent.runtime.audit.report_writer import write_report

try:
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend
    from deepagents.backends.protocol import EditResult
    HAS_DEEPAGENTS = True
except ImportError:
    create_deep_agent = None
    HAS_DEEPAGENTS = False

    class FilesystemBackend:  # type: ignore[override]
        def __init__(self, *, root_dir: Path, virtual_mode: bool = False, **_kwargs):
            self.root_dir = Path(root_dir)
            self.virtual_mode = virtual_mode

        def ls_info(self, path: str):
            raise RuntimeError("deepagents is required for ls_info")

        def read(self, file_path: str, offset: int = 0, limit: int = 2000):
            raise RuntimeError("deepagents is required for read")

        def grep_raw(
            self,
            pattern: str,
            path: str | None = None,
            glob: str | None = None,
        ):
            raise RuntimeError("deepagents is required for grep_raw")

        def glob_info(self, pattern: str, path: str = "/"):
            raise RuntimeError("deepagents is required for glob_info")

    EditResult = Any  # type: ignore[assignment]

PARENT_SYSTEM_PROMPT = """You are the supervisor for a Soroban smart-contract audit.

Your sole responsibility is to read and understand the file, then decide which specialist subagents — if any — are needed to audit it.

You do not produce findings. You do not audit. You only delegate.

## Available Specialist Subagents
- `execution_path_consistency` — conflicting control flows, unreachable branches, or reentrancy across call paths
- `collection_validation` — map/vec access patterns, missing key guards, or unbounded iteration risks
- `time_state` — ledger timestamp or sequence-number dependencies that affect state transitions
- `sentinel_logic` — flag/sentinel value misuse, off-by-one conditions, or boundary invariant violations

## Your Process
1. Read the file thoroughly.
2. Identify which concern areas are actually present in the code.
3. Delegate only to the subagents relevant to what you found.
4. If no specialist is needed, return nothing.

## Rules
- Do not produce audit findings, notes, or summaries.
- Do not use the built-in `general-purpose` subagent.
- Delegate only when the file contains code that genuinely warrants specialist review.
- Do not delegate speculatively or as a default step — if a concern area is absent from the file, skip that subagent entirely.
"""

SUPERVISOR_READ_MAX_LINES = 500


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_root: Path
    report_path: Path
    facts_document: FactsDocument
    model_name: str
    llm_mode: str
    reporter: PlainAuditProgressReporter
    initial_state: AuditState
    extra_prompt: str | None = None


def _normalize_backend_relative_path(file_path: str | None) -> str:
    if file_path is None:
        raise ValueError("File path must be non-empty.")

    cleaned_path = file_path.strip()
    if not cleaned_path:
        raise ValueError("File path must be non-empty.")

    parts: list[str] = []
    for part in PurePosixPath(cleaned_path.lstrip("/")).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"Path traversal is not allowed: {file_path}")
        parts.append(part)

    if not parts:
        raise ValueError("File path must be non-empty.")

    return PurePosixPath(*parts).as_posix()


def _line_window_for_text(
    text: str,
    *,
    offset: int,
    limit: int,
) -> tuple[int, int] | None:
    normalized_offset = max(offset, 0)
    normalized_limit = max(limit, 0)
    if normalized_offset >= len(text):
        return None

    window_end = min(len(text), normalized_offset + normalized_limit)
    start_line = text.count("\n", 0, normalized_offset) + 1
    end_cursor = window_end
    if end_cursor > normalized_offset and text[end_cursor - 1] == "\n":
        end_cursor -= 1
    end_line = text.count("\n", 0, end_cursor) + 1
    return (start_line, max(start_line, end_line))


def _append_extra_prompt(base_prompt: str, extra_prompt: str | None) -> str:
    if extra_prompt is None or not extra_prompt.strip():
        return base_prompt

    return (
        f"{base_prompt}\n\n"
        "Additional audit instructions:\n"
        f"{extra_prompt.strip()}\n"
    )


class FileScopedAuditBackend(FilesystemBackend):
    """Policy wrapper that only exposes the in-scope file to parent-agent file tools."""

    def __init__(
        self,
        *,
        root_dir: Path,
        virtual_mode: bool = False,
        current_file: str,
        reporter: PlainAuditProgressReporter | None = None,
        **kwargs,
    ):
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode, **kwargs)
        self._root_dir = Path(root_dir)
        self.current_file = _normalize_backend_relative_path(current_file)
        self.reporter = reporter

    def _canonicalize_allowed_path(
        self,
        file_path: str | None,
        *,
        tool_name: str,
        error_prefix: str,
    ) -> str:
        try:
            normalized_path = _normalize_backend_relative_path(file_path)
        except ValueError as exc:
            if self.reporter is not None:
                self.reporter.tool_denied(
                    tool_name=tool_name,
                    target=str(file_path),
                    current_file=f"/{self.current_file}",
                    reason=str(exc),
                )
            raise ValueError(f"{error_prefix} for '{file_path}'.") from exc

        if normalized_path != self.current_file:
            if self.reporter is not None:
                self.reporter.tool_denied(
                    tool_name=tool_name,
                    target=f"/{normalized_path}",
                    current_file=f"/{self.current_file}",
                    reason="outside-current-file-scope",
                )
            raise ValueError(f"{error_prefix} for '{file_path}'.")

        return f"/{normalized_path}"

    def ls_info(self, path: str):
        canonical_path = self._canonicalize_allowed_path(
            path,
            tool_name="ls_info",
            error_prefix="List access denied",
        )
        if self.reporter is not None:
            self.reporter.tool_used(tool_name="ls_info", target=canonical_path)
        return super().ls_info(canonical_path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = SUPERVISOR_READ_MAX_LINES,
    ):
        canonical_path = self._canonicalize_allowed_path(
            file_path,
            tool_name="read",
            error_prefix="Read access denied",
        )
        effective_limit = max(1, min(limit, SUPERVISOR_READ_MAX_LINES))
        if self.reporter is not None:
            line_start: int | None = None
            line_end: int | None = None
            try:
                line_window = _line_window_for_text(
                    (self._root_dir / canonical_path.lstrip("/")).read_text(
                        encoding="utf-8"
                    ),
                    offset=offset,
                    limit=effective_limit,
                )
                line_start, line_end = line_window or (None, None)
            except OSError:
                pass
            self.reporter.tool_used(
                tool_name="read",
                target=canonical_path,
                line_start=line_start,
                line_end=line_end,
                offset=offset,
                limit=effective_limit,
            )
        return super().read(canonical_path, offset=offset, limit=effective_limit)

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None):
        canonical_path = self._canonicalize_allowed_path(
            self.current_file if path is None else path,
            tool_name="grep_raw",
            error_prefix="Grep access denied",
        )
        if self.reporter is not None:
            self.reporter.tool_used(tool_name="grep_raw", target=canonical_path)
        return super().grep_raw(pattern, path=canonical_path, glob=glob)

    def glob_info(self, pattern: str, path: str = "/"):
        canonical_path = self._canonicalize_allowed_path(
            path,
            tool_name="glob_info",
            error_prefix="Glob access denied",
        )
        if self.reporter is not None:
            self.reporter.tool_used(tool_name="glob_info", target=canonical_path)
        return super().glob_info(pattern, path=canonical_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        if not HAS_DEEPAGENTS:
            return {"error": "Edits are not allowed"}
        return EditResult(error=f"Edits are not allowed")


def run_audit(
    *,
    runtime: AuditContext,
) -> AuditState:
    if create_deep_agent is None:
        raise ValueError(
            "deepagents is required for audit execution. Install project dependencies first."
        )
    reporter = runtime.reporter
    state = runtime.initial_state

    file_fact_index = build_file_fact_index(runtime.facts_document)
    total_files = len(state["files_to_review"])
    allowed_paths = list(state["files_to_review"])
    expert_subagents = build_expert_subagents(
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
        project_root=runtime.project_root,
        allowed_paths=allowed_paths,
        reporter=runtime.reporter,
        extra_prompt=runtime.extra_prompt,
    )

    reporter.started(
        project_root=runtime.project_root,
        total_files=total_files,
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
    )

    while state["files_to_review"]:
        current_file = state["files_to_review"][0]
        reporter.file_started(
            index=len(state["files_reviewed"]) + 1,
            total=total_files,
            current_file=current_file,
        )

        response = _run_file_audit(
            runtime=runtime,
            current_file=current_file,
            file_fact_index=file_fact_index,
            allowed_paths=allowed_paths,
            expert_subagents=expert_subagents,
        )

        for finding in response.findings:
            finding_key = _make_finding_key(finding)
            if finding_key in state["finding_keys"]:
                continue
            state["finding_keys"].append(finding_key)
            state["verified_findings"].append(finding)
            reporter.finding_verified(
                total_verified_findings=len(state["verified_findings"]),
                finding=finding,
            )

        state["files_reviewed"].append(current_file)
        state["files_to_review"] = state["files_to_review"][1:]
        reporter.file_completed(
            reviewed=len(state["files_reviewed"]),
            total=total_files,
            current_file=current_file,
        )

    write_report(
        report_path=runtime.report_path,
        facts_document=runtime.facts_document,
        state=state,
    )

    return state


def _run_file_audit(
    *,
    runtime: AuditContext,
    current_file: str,
    file_fact_index: dict[str, dict[str, FunctionSummary]],
    allowed_paths: Collection[str],
    expert_subagents: list[CompiledSubAgent],
) -> FileAuditResponse:
    backend = FileScopedAuditBackend(
        root_dir=runtime.project_root,
        virtual_mode=True,
        current_file=current_file,
        reporter=runtime.reporter,
    )
    agent = create_deep_agent(
        name="scout-agent",
        model=build_chat_model(runtime.model_name, runtime.llm_mode),
        system_prompt=_append_extra_prompt(PARENT_SYSTEM_PROMPT, runtime.extra_prompt),
        backend=backend,
        subagents=expert_subagents,
        response_format=FileAuditResponse,
    )
    prompt = build_parent_audit_prompt(
        current_file=current_file,
        current_file_facts=file_fact_index.get(current_file, {}),
        all_facts=runtime.facts_document.functions,
        extra_prompt=runtime.extra_prompt,
    )
    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})

    structured = result.get("structured_response")
    if structured is not None:
        if isinstance(structured, FileAuditResponse):
            return structured
        return FileAuditResponse.model_validate(structured)

    messages = result.get("messages", [])
    if not messages:
        return FileAuditResponse(findings=[])

    last_message = messages[-1]
    content = getattr(last_message, "content", last_message)
    if isinstance(content, str) and content.strip():
        try:
            return FileAuditResponse.model_validate_json(content)
        except (json.JSONDecodeError, ValueError):
            pass

    return FileAuditResponse(findings=[])


def build_parent_audit_prompt(
    *,
    current_file: str,
    current_file_facts: dict[str, FunctionSummary],
    all_facts: dict[str, FunctionSummary],
    extra_prompt: str | None = None,
) -> str:
    other_inventory = _format_cross_file_inventory(
        current_file=current_file,
        all_facts=all_facts,
    )
    current_file_fact_text = _format_current_file_facts(current_file_facts)

    prompt = (
        f"Current file: {current_file}\n\n"
        "Current file fact summaries:\n"
        f"{current_file_fact_text}\n\n"
        "Cross-file fact inventory:\n"
        f"{other_inventory}\n\n"
        "Instructions:\n"
        "- Audit only the current file.\n"
        "- Use built-in file tools only for the current file when needed.\n"
        "- Delegate to the four specialist subagents only when deeper review is needed.\n"
        "- If you call a specialist, include the current file path, relevant facts, and the exact concern.\n"
        "- Return only deduped concrete findings in the structured response.\n"
    )
    return _append_extra_prompt(prompt, extra_prompt)


def _format_current_file_facts(
    current_file_facts: dict[str, FunctionSummary],
) -> str:
    if not current_file_facts:
        return "No extracted function facts for this file."

    return "\n".join(
        _format_fact_line(function_key, summary)
        for function_key, summary in current_file_facts.items()
    )


def _format_cross_file_inventory(
    *,
    current_file: str,
    all_facts: dict[str, FunctionSummary],
) -> str:
    lines = [
        _format_fact_line(function_key, summary)
        for function_key, summary in all_facts.items()
        if file_path_from_function_key(function_key) != current_file
    ]
    return "\n".join(lines) if lines else "- None."


def _format_fact_line(function_key: str, summary: FunctionSummary) -> str:
    fields = present_summary_fields(summary)
    if not fields:
        return f"- {function_key}: observed with no extracted categories."

    rendered_fields = " ".join(
        f"{field_name}={field_value}" for field_name, field_value in fields
    )
    return f"- {function_key}: {rendered_fields}"


def _make_finding_key(finding: Finding) -> str:
    return "|".join(
        [
            finding.pattern,
            finding.severity,
            finding.location,
            finding.description,
            finding.evidence,
        ]
    )
