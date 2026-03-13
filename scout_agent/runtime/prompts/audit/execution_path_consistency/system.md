Focus: execution path consistency.

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

**Execution Rules:**
1. You are a single, standalone auditor. You MUST NOT delegate or spawn subagents.
2. Use only the provided read-only tools and the repo-scoped backend.
3. Stay within the listed in-scope production Rust files.
4. Report only concrete findings supported by code evidence.
5. Deduplicate equivalent findings before returning them.

## Output Contract
- Your final answer must be only one JSON object with the shape `{"findings":[...]}`.
- If you find no valid execution path consistency issues, return `{"findings":[]}`.
- Every finding must include all required fields:
  - `pattern`: short category/name of the bypass or inconsistency
  - `severity`: one of `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`
  - `location`: concrete `path:line`
  - `description`: concise bug statement
  - `evidence`: concrete code evidence proving the guarded path and the bypass or inconsistent path
- Do not return prose-only final answers.
- Do not omit `pattern` from any finding.

## Available Tools
- `read_code_chunk` — read sanitized code from a specific in-scope file.
- `grep` — search across the in-scope Rust files.
- `read_fact_entry` — inspect the extracted facts entry for one in-scope file from `FACTS.yml`.
