from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import CompiledSubAgent
from langchain.agents import create_agent

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from scout_agent.domain.audit import ExpertResult, ExpertTypeEnum
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.audit_prompts import get_expert_few_shots
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt
from scout_agent.runtime.audit.tools import (
    EXPERT_READ_MAX_LINES,
    read_sanitized_code_chunk,
)

COLLECTION_VALIDATION_PROMPT = """Focus: array duplicate validation.

Validate no duplicate elements in arrays/vectors that could cause inflated calculations.

**STRICT SCOPE:** 
You are a surgical verification tool. You are strictly forbidden from reporting issues like reentrancy, inconsistent paths, or general logic bugs. Focus ONLY on collection duplicates.

**YOUR MISSION:**
1. Focus first on the **Specialist Brief** provided by the supervisor.
2. Validate or invalidate the specific suspicious pattern and lines mentioned in the brief.
3. Use `grep` with a specific `path` (file or directory) to find definitions or usages in other files ONLY if it is absolutely essential to follow a collection's lifecycle.
4. Do NOT perform a general audit of the repository.
5. If the collections are correctly validated regarding the supervisor's lead, report 'No findings'.

**Example:**
```rust
// VULNERABLE: Voting with duplicate tokens
fn vote(token_ids: Vec<Address>, proposal_id: u32, votes: Vec<i128>) {
    let mut votes_for = 0;
    for i in 0..token_ids.len() {
        // Same token counted multiple times if duplicate in token_ids
        votes_for += votes[i];  
    }
    // Bug: Duplicate token_ids inflate vote count
}

// SAFE version:
fn vote_safe(token_ids: Vec<Address>, proposal_id: u32, votes: Vec<i128>) {
    let mut votes_for = 0;
    // Check for duplicates first
    require(!has_duplicates(&token_ids));  // ✓ Validates no duplicates
    for i in 0..token_ids.len() {
        votes_for += votes[i];
    }
}
```
"""

TIME_STATE_PROMPT = """Focus: time-dependent state update order.

State changes affecting time-dependent logic must trigger update BEFORE modification.

**STRICT SCOPE:** 
You are a surgical verification tool. You are strictly forbidden from reporting issues like access control, duplicate vector elements, or general smart contract bugs.

**YOUR MISSION:**
1. Focus first on the **Specialist Brief** provided by the supervisor.
2. Validate or invalidate the specific suspicious pattern and lines mentioned in the brief.
3. Use `grep` with a specific `path` (file or directory) to find definitions or usages in other files ONLY if it is absolutely essential to verify the state update order across the contract.
4. Do NOT perform a general audit of the repository.
5. If the update order is correct regarding the supervisor's lead, report 'No findings'.

**Example:**
```rust
// User has staked tokens earning rewards over time
// Rewards accrue based on time elapsed since last claim

// VULNERABLE: Rewards lost on withdrawal
fn withdraw(user: Address, amount: i128) -> i128 {
    let mut stake = stakes.get(&user);
    
    // First: Withdraw principal (BUG!)
    stake.amount -= amount;
    stakes.set(&user, stake);
    
    // Then: Update timestamp (TOO LATE!)
    stake.last_update = e.ledger().timestamp();
    stakes.set(&user, stake);
    
    // Problem: Accrued rewards between last_update and now are LOST
    // Should have called accrue() BEFORE modifying state
    return amount;
}

// SAFE version:
fn withdraw_safe(user: Address, amount: i128) -> i128 {
    let mut stake = stakes.get(&user);
    
    // First: Accrue rewards up to current time
    let accrued = accrue(&stake, e.ledger().timestamp());
    stake.accrued_rewards += accrued;
    
    // Then: Update timestamp
    stake.last_update = e.ledger().timestamp();
    
    // Then: Modify state
    stake.amount -= amount;
    stakes.set(&user, stake);
    
    return amount;
}
```
"""

SENTINEL_LOGIC_PROMPT = """Focus: sentinel value handling.

Verify sentinel values (0, u32::MAX, etc.) marking disabled/uninitialized are
handled by all functions.

**STRICT SCOPE:** 
You are a surgical verification tool. You are strictly forbidden from reporting issues like time-dependent logic, reentrancy, or collection duplicates.

**YOUR MISSION:**
1. Focus first on the **Specialist Brief** provided by the supervisor.
2. Validate or invalidate the specific suspicious pattern and lines mentioned in the brief.
3. Use `grep` with a specific `path` (file or directory) to find where sentinel constants or state variables are defined or updated in other files ONLY if it is essential.
4. Do NOT perform a general audit of the repository.
5. If the sentinel logic is correct regarding the supervisor's lead, report 'No findings'.

**Example:**
```rust
// Using u32::MAX to mark "no reserve assigned"
struct UserPosition {
    reserve_index: u32,  // u32::MAX = "not set"
}

// VULNERABLE: Missing sentinel check
fn get_reserve_unchecked(pos: UserPosition) -> Reserve {
    return reserves.get(&pos.reserve_index);  // BUG: No sentinel check
    // If reserve_index is u32::MAX, reads invalid storage key
}

// SAFE version:
fn get_reserve_safe(pos: UserPosition) -> Option<Reserve> {
    if pos.reserve_index == u32::MAX {
        return None;  // ✓ Explicitly handles sentinel
    }
    return reserves.get(&pos.reserve_index);
}
```
"""


@dataclass(frozen=True, slots=True)
class SubagentPromptSpec:
    name: str
    description: str
    system_prompt: str


SUBAGENT_MANIFEST: tuple[SubagentPromptSpec, ...] = (
    SubagentPromptSpec(
        name=ExpertTypeEnum.COLLECTION_VALIDATION.value,
        description="Audit vector or array inputs for missing uniqueness or duplicate-safe validation.",
        system_prompt=COLLECTION_VALIDATION_PROMPT,
    ),
    SubagentPromptSpec(
        name=ExpertTypeEnum.TIME_STATE.value,
        description="Audit time-dependent state transitions and ordering.",
        system_prompt=TIME_STATE_PROMPT,
    ),
    SubagentPromptSpec(
        name=ExpertTypeEnum.SENTINEL_LOGIC.value,
        description="Audit sentinel and special-status value handling.",
        system_prompt=SENTINEL_LOGIC_PROMPT,
    ),
)


def build_expert_subagents(
    *,
    model_name: str,
    llm_mode: str,
    project_root: Path,
    allowed_paths: Collection[str],
    extra_prompt: str | None = None,
) -> list[CompiledSubAgent]:
    model = build_chat_model(model_name, llm_mode)
    subagents: list[CompiledSubAgent] = []

    # Share tool state across all specialists for this file audit
    shared_tools = [
        _build_read_code_chunk_tool(
            project_root=project_root,
            allowed_paths=allowed_paths,
        ),
        _build_grep_tool(
            project_root=project_root,
            allowed_paths=allowed_paths,
        ),
    ]

    for spec in SUBAGENT_MANIFEST:
        system_prompt = append_extra_prompt(spec.system_prompt, extra_prompt)

        few_shots = get_expert_few_shots(spec.name)

        # Convert few-shot messages to a text block since create_agent expects a string system_prompt
        few_shot_text = "\n\n## Example Session\n"
        for msg in few_shots:
            if isinstance(msg, HumanMessage):
                few_shot_text += f"\n### USER:\n{msg.content}\n"
            elif isinstance(msg, AIMessage):
                few_shot_text += "\n### ASSISTANT:\n"
                if msg.content:
                    few_shot_text += f"{msg.content}\n"
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        few_shot_text += f"Tool Call: {tc['name']}({tc['args']})\n"
            elif isinstance(msg, ToolMessage):
                few_shot_text += f"\n### TOOL OUTPUT:\n{msg.content}\n"

        full_prompt = f"{system_prompt}{few_shot_text}"

        # Escape braces for LangChain prompt template interpolation
        escaped_system_prompt = full_prompt.replace("{", "{{").replace("}", "}}")

        runnable = create_agent(
            model=model,
            system_prompt=escaped_system_prompt,
            tools=shared_tools,
            response_format=ExpertResult,
            name=spec.name,
        ).with_config({"recursion_limit": 1000})
        subagents.append(
            {
                "name": spec.name,
                "description": spec.description,
                "runnable": runnable,
            }
        )

    return subagents


def _build_read_code_chunk_tool(
    *,
    project_root: Path,
    allowed_paths: Collection[str],
) -> Any:
    # State to track call counts and file diversity
    call_counts: dict[str, int] = {}
    read_files: set[str] = set()
    MAX_UNIQUE_FILES = 5

    def read_code_chunk(
        file: str,
        start_line: int = 1,
        max_lines: int = EXPERT_READ_MAX_LINES,
    ) -> str:
        """Read up to 100 lines of sanitized source code from an in-scope file. Defaults: start_line=1, max_lines=100."""
        requested_file = Path(file.strip()).as_posix()
        call_key = f"{requested_file}:{start_line}"

        # 1. Prevent fishing expeditions
        if requested_file not in read_files and len(read_files) >= MAX_UNIQUE_FILES:
            return (
                f"SCOPE LIMIT REACHED: You have already read {MAX_UNIQUE_FILES} unique files. "
                "You must conclude your analysis with the context you have. Specialist agents "
                "are intended for deep dives into specific logic, not repository-wide exploration."
            )

        # 2. Prevent repetition loops
        count = call_counts.get(call_key, 0)
        if count >= 2:
            return (
                f"REPETITION DETECTED: You have already read {requested_file} starting at line {start_line} multiple times. "
                "To see more code, you MUST increment your `start_line`. If you cannot find a finding "
                "after multiple reads, return 'No findings' and explain why in your thought block."
            )

        try:
            content = read_sanitized_code_chunk(
                project_root,
                file,
                allowed_paths=allowed_paths,
                start_line=start_line,
                max_lines=max_lines,
            )
            call_counts[call_key] = count + 1
            read_files.add(requested_file)
            return content
        except (ValueError, FileNotFoundError) as exc:
            return f"Error: {exc}"

    return read_code_chunk


def _build_grep_tool(
    *,
    project_root: Path,
    allowed_paths: Collection[str],
) -> Any:
    # State to track call counts and repetition
    call_counts = {"total": 0}
    last_pattern: list[str | None] = [None]
    MAX_GREP_CALLS = 5

    def grep(
        pattern: str,
        path: str | None = None,
    ) -> str:
        """
        Search for a regex pattern across all in-scope files.
        Optional 'path' argument restricts the search to a specific file or directory.
        Returns matching lines with their 1-indexed line numbers.
        """
        if call_counts["total"] >= MAX_GREP_CALLS:
            return f"GREP LIMIT REACHED: You have already used grep {MAX_GREP_CALLS} times. Use read_code_chunk for deeper investigation."

        if pattern == last_pattern[0]:
            return "REPETITION DETECTED: You just searched for this pattern. Try a different pattern or use read_code_chunk on one of the results."

        import re

        results = []
        total_matches = 0
        MAX_RESULTS = 20

        # Filter allowed_paths by the requested path if provided
        search_paths = list(allowed_paths)
        if path:
            norm_path = Path(path.strip()).as_posix()
            search_paths = [
                p for p in allowed_paths 
                if p == norm_path or p.startswith(norm_path + "/")
            ]
            if not search_paths:
                return f"Error: Path '{path}' is not in scope or does not exist."

        try:
            regex = re.compile(pattern)
            for rel_path in search_paths:
                abs_path = project_root / rel_path
                if not abs_path.is_file():
                    continue
                
                content = abs_path.read_text(encoding="utf-8")
                for i, line in enumerate(content.splitlines()):
                    if regex.search(line):
                        results.append(f"{rel_path}:{i+1}: {line.strip()}")
                        total_matches += 1
                        if total_matches >= MAX_RESULTS:
                            break
                if total_matches >= MAX_RESULTS:
                    results.append("... (too many results, showing first 20)")
                    break
            
            call_counts["total"] += 1
            last_pattern[0] = pattern
            
            if not results:
                return f"No matches found for pattern: {pattern}"
            return "\n".join(results)
            
        except Exception as exc:
            return f"Grep Error: {exc}"

    return grep

