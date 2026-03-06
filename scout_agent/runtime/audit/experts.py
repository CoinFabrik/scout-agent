from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from deepagents import CompiledSubAgent
from langchain.agents import create_agent

from scout_agent.domain.audit import ExpertResult, ExpertTypeEnum
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt
from scout_agent.runtime.audit.tools import (
    EXPERT_READ_MAX_LINES,
    read_sanitized_code_chunk,
)

EXECUTION_PATH_CONSISTENCY_PROMPT = """Focus: execution path consistency.

A RESTRICTION can be BYPASSED through a different code path that performs 
the SAME OPERATION or contains the restricted operation.

Two scenarios to check:

**Scenario 1 - Function Containment:**
- Function A has a restriction (authorization, pause check, rate limit, etc.)
- Function B appears to do a different operation but internally calls or contains Function A
- By calling Function B, users bypass Function A's restriction

**Scenario 2 - Same Operation, Inconsistent Paths:**
- Function A and Function B perform the SAME operation
- Function A has restriction R
- Function B does NOT have restriction R
- Users can bypass restriction via the unrestricted path

**Auditing Strategy:**
To find inconsistencies, do not just compare similar-looking functions. Instead:
1. Identify a sensitive state change (e.g., updating a user's debt or balance).
2. Find the "primary" function that performs this change and note its restrictions (e.g., "Must not be paused").
3. Search the entire codebase for all other functions that perform that same state change.
4. Verify if any identified path skips the restrictions found in the primary function.

What NOT to report:
- A function with NO restrictions at all (that's just "missing restriction")

**Examples:**

```rust
// Scenario 1 - Function Containment
// Function A - WITH restriction
fn withdraw(amount: i128) {
    require(!is_paused());  // ✓ Blocked when paused
    balances.subtract(amount);
}

// Function B - DIFFERENT operation but contains Function A's logic
fn swap_and_withdraw(token: Address, amount: i128) {
    swap(token, amount);
    // Contains withdraw logic WITHOUT pause check!
    balances.subtract(amount);  // Bypasses pause!
}

// Scenario 2 - Same Operation, Inconsistent Paths
fn transfer(to: Address, amount: i128) {
    require_auth();  // ✓ Validates caller
    balances.transfer(&to, amount);
}

fn transfer_batch(transfers: Vec<(Address, i128)>) {
    // Same operation but NO auth check!
    for t in transfers {
        balances.transfer(&t.0, t.1);  // Inconsistent!
    }
}
```
"""

COLLECTION_VALIDATION_PROMPT = """Focus: array duplicate validation.

Validate no duplicate elements in arrays/vectors that could cause inflated calculations.

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
        name=ExpertTypeEnum.EXECUTION_PATH_CONSISTENCY.value,
        description="Audit state mutation paths for inconsistent validation or authorization.",
        system_prompt=EXECUTION_PATH_CONSISTENCY_PROMPT,
    ),
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
        runnable = create_agent(
            model=model,
            system_prompt=system_prompt,
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
) -> Callable[..., str]:
    def read_code_chunk(
        file: str,
        start_line: int = 1,
        max_lines: int = EXPERT_READ_MAX_LINES,
    ) -> str:
        """Read up to 100 lines of sanitized source code from an in-scope file."""
        try:
            return read_sanitized_code_chunk(
                project_root,
                file,
                allowed_paths=allowed_paths,
                start_line=start_line,
                max_lines=max_lines,
            )
        except (ValueError, FileNotFoundError) as exc:
            return f"Error: {exc}"

    return read_code_chunk
