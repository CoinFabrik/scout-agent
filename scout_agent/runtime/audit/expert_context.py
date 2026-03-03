from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from scout_agent.domain.audit import Delegation, ExpertTypeEnum
from scout_agent.domain.facts import FileFacts, FunctionFacts
from scout_agent.runtime.audit.tools import expert_read_code, search_code


@dataclass(frozen=True, slots=True)
class ExpertExecutionContext:
    code_snapshot: str
    search_results: str | None
    allowed_paths: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Anchor:
    function: FunctionFacts | None
    line_number: int
    matched: bool


def build_expert_execution_context(
    *,
    project_root: Path,
    facts_index: dict[str, FileFacts],
    delegation: Delegation,
) -> ExpertExecutionContext:
    target_file = delegation.target_file
    if target_file not in facts_index:
        raise ValueError(
            f"Delegation target file is outside facts_index: {target_file}"
        )

    target_file_facts = facts_index[target_file]
    allowed_paths = frozenset(facts_index.keys())
    anchor = _find_anchor(
        project_root=project_root.resolve(),
        target_file_facts=target_file_facts,
        context_snippet=delegation.context_snippet,
    )
    start_line, max_lines = _read_window_for_anchor(anchor)

    code_snapshot = expert_read_code(
        project_root.resolve(),
        target_file,
        allowed_paths=allowed_paths,
        start_line=start_line,
        max_lines=max_lines,
    )

    search_results = _build_search_results(
        project_root=project_root.resolve(),
        facts_index=facts_index,
        delegation=delegation,
        anchor=anchor,
    )

    return ExpertExecutionContext(
        code_snapshot=code_snapshot,
        search_results=search_results,
        allowed_paths=allowed_paths,
    )


def _find_anchor(
    *,
    project_root: Path,
    target_file_facts: FileFacts,
    context_snippet: str,
) -> _Anchor:
    stripped_context = context_snippet.strip()

    for function in target_file_facts.functions:
        if stripped_context and stripped_context in function.signature:
            return _Anchor(
                function=function, line_number=function.line_start, matched=True
            )

    for function in target_file_facts.functions:
        if stripped_context and stripped_context in function.function_id:
            return _Anchor(
                function=function, line_number=function.line_start, matched=True
            )

    file_path = project_root / Path(target_file_facts.path)
    for line_number, line in enumerate(
        file_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if stripped_context and stripped_context in line:
            return _Anchor(function=None, line_number=line_number, matched=True)

    return _Anchor(function=None, line_number=1, matched=False)


def _read_window_for_anchor(anchor: _Anchor) -> tuple[int, int]:
    if anchor.function is not None:
        start_line = max(1, anchor.function.line_start - 10)
        end_line = min(anchor.function.line_end + 10, start_line + 99)
        return start_line, end_line - start_line + 1

    if anchor.matched:
        return max(1, anchor.line_number - 10), 40

    return 1, 100


def _build_search_results(
    *,
    project_root: Path,
    facts_index: dict[str, FileFacts],
    delegation: Delegation,
    anchor: _Anchor,
) -> str | None:
    allowed_paths = frozenset(facts_index.keys())

    if delegation.expert_type == ExpertTypeEnum.COLLECTION_VALIDATION:
        return None

    if delegation.expert_type == ExpertTypeEnum.EXECUTION_PATH_CONSISTENCY:
        if anchor.function is not None:
            pattern = rf"\b{re.escape(anchor.function.name)}\b"
        else:
            pattern = re.escape(delegation.context_snippet.strip())
        return _normalize_search_results(
            search_code(
                project_root,
                allowed_paths=allowed_paths,
                pattern=pattern,
            )
        )

    if delegation.expert_type == ExpertTypeEnum.TIME_STATE:
        return _normalize_search_results(
            search_code(
                project_root,
                allowed_paths=allowed_paths,
                pattern=r"timestamp\s*\(",
                file_glob=delegation.target_file,
            )
        )

    if delegation.expert_type == ExpertTypeEnum.SENTINEL_LOGIC:
        token = _sentinel_token_from_context(delegation.context_snippet)
        if token is None:
            return None
        return _normalize_search_results(
            search_code(
                project_root,
                allowed_paths=allowed_paths,
                pattern=_sentinel_pattern(token),
                file_glob=delegation.target_file,
            )
        )

    raise ValueError(f"Unsupported expert type: {delegation.expert_type}")


def _normalize_search_results(results: str) -> str | None:
    if results == "No matches found.":
        return None
    return results


def _sentinel_token_from_context(context_snippet: str) -> str | None:
    for token in ("u32::MAX", "None", "0"):
        if token in context_snippet:
            return token
    return None


def _sentinel_pattern(token: str) -> str:
    if token == "0":
        return r"\b0\b"
    if token == "None":
        return r"\bNone\b"
    return re.escape(token)
