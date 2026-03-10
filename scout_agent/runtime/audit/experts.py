from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import CompiledSubAgent
from langchain.agents import create_agent

from langchain_core.messages import AIMessage, HumanMessage
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
You are a specialist. You are strictly forbidden from reporting issues like reentrancy, inconsistent paths, or general logic bugs. Focus ONLY on collection duplicates.

**YOUR MISSION:**
1. Focus first on the **Specialist Brief** provided by the supervisor.
2. Validate or invalidate the specific suspicious pattern and lines mentioned in the brief.
3. If the collections are correctly validated regarding the supervisor's lead, report 'No findings'.

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
You are a specialist. You are strictly forbidden from reporting issues like access control, duplicate vector elements, or general smart contract bugs.

**YOUR MISSION:**
1. Focus first on the **Specialist Brief** provided by the supervisor.
2. Validate or invalidate the specific suspicious pattern and lines mentioned in the brief.
3. If the update order is correct regarding the supervisor's lead, report 'No findings'.

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
You are a specialist. You are strictly forbidden from reporting issues like time-dependent logic, reentrancy, or collection duplicates.

**YOUR MISSION:**
1. Focus first on the **Specialist Brief** provided by the supervisor.
2. Validate or invalidate the specific suspicious pattern and lines mentioned in the brief.
3. If the sentinel logic is correct regarding the supervisor's lead, report 'No findings'.

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

    for spec in SUBAGENT_MANIFEST:
        system_prompt = append_extra_prompt(spec.system_prompt, extra_prompt)

        few_shots = get_expert_few_shots(spec.name)

        # Convert few-shot messages to a text block since create_agent expects a string system_prompt
        few_shot_text = "\n\n## Example Session\n"
        for msg in few_shots:
            role = "USER" if isinstance(msg, HumanMessage) else "ASSISTANT"
            few_shot_text += f"\n### {role}:\n{msg.content}\n"

        full_prompt = f"{system_prompt}{few_shot_text}"

        # Escape braces for LangChain prompt template interpolation
        escaped_system_prompt = full_prompt.replace("{", "{{").replace("}", "}}")

        runnable = create_agent(
            model=model,
            system_prompt=escaped_system_prompt,
            tools=[
                _build_read_code_chunk_tool(
                    project_root=project_root,
                    allowed_paths=allowed_paths,
                )
            ],
            response_format=ExpertResult,
            name=spec.name,
        )
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
    # State to track call counts per (file, start_line) to prevent loops
    call_counts: dict[str, int] = {}

    def read_code_chunk(
        file: str,
        start_line: int = 1,
        max_lines: int = EXPERT_READ_MAX_LINES,
    ) -> str:
        """Read up to 100 lines of sanitized source code from an in-scope file."""
        requested_file = Path(file.strip()).as_posix()
        call_key = f"{requested_file}:{start_line}"

        count = call_counts.get(call_key, 0)
        if count >= 2:
            return (
                f"REPETITION DETECTED: You have already read {requested_file} starting at line {start_line} multiple times. "
                "To see more code, you MUST increment your `start_line` (e.g., to "
                f"{start_line + max_lines}). If you have already read the relevant code and cannot find a finding, "
                "return 'No findings' and explain why in your thought block."
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
            return content
        except (ValueError, FileNotFoundError) as exc:
            return f"Error: {exc}"

    return read_code_chunk
