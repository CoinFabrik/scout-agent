# Scout-Agent Report

## Metadata
- Project root: `/Users/josegarcia/Desktop/reflector-contract`
- Generated at UTC: `2026-03-26T20:35:49Z`
- Model: `openai:gpt-5.4`
- LLM mode: `creative`
- execution_path_consistency completed: `True`

## Summary
- Files reviewed: 14
- Verified findings: 3
- CRITICAL: 0
- HIGH: 0
- MEDIUM: 3
- LOW: 0

## Findings

## Finding 1
- Pattern: Unchecked sentinel-like short vector disables fees
- Severity: MEDIUM
- Location: beam-contract/src/cost.rs:26
- Description: set_costs_config accepts any Vec<u64> length, but estimate_invocation_cost treats missing entries from a short stored vector as 0 via unwrap_or_default(). That 0 is then interpreted as 'disabled/no charge' for both the base invocation cost and the period modifier, so malformed persisted config can silently disable fees or period scaling instead of preserving the intended default schedule.
- Evidence: beam-contract/src/cost.rs:26-27 stores arbitrary `costs` with no length validation. beam-contract/src/cost.rs:31-40 only supplies the 5-entry default when storage is entirely missing. Once any vector is stored, beam-contract/src/cost.rs:79 reads `costs.get(invocation as u32).unwrap_or_default()` and returns 0 when `cost < 1` at lines 80-82, while lines 85-89 read index 0 with `unwrap_or_default()` and skip scaling unless `period_modifier > 0`. Therefore a short vector in storage causes absent entries to resolve to 0 and be treated as disabled behavior.

## Finding 2
- Pattern: admin-only config bypasses Beam invocation-cost authorization path
- Severity: MEDIUM
- Location: beam-contract/src/lib.rs:404
- Description: Beam price-query functions enforce caller authorization and burn invocation fees before delegating to the shared oracle logic, but `set_invocation_costs_config` modifies that fee schedule through an unguarded path. This lets any caller set query costs to zero and then use the guarded Beam read paths without paying the intended fee restriction.
- Evidence: Guarded query path: `beam-contract/src/lib.rs:173-176` requires `caller.require_auth();` and `charge_invocation_fee(...)` before `PriceOracleContractBase::price(...)`; similarly `lastprice/prices/x_* /twap` do the same. Sensitive state setter: `beam-contract/src/cost.rs:26-27` writes invocation cost config with `e.storage().instance().set(&COST_CONFIG_KEY, &costs);`. Bypass path: `beam-contract/src/lib.rs:404-406` exposes `pub fn set_invocation_costs_config(e: &Env, config: Vec<u64>) { set_costs_config(e, &config); }` with no `require_auth`/admin check, despite the comment at `394-403` stating 'Requires admin authorization'. Since `charge_invocation_fee` at `beam-contract/src/cost.rs:50-61` uses this same config to compute/burn fees, an attacker can first zero out costs via the unrestricted setter and then call the otherwise fee-restricted Beam execution path.

## Finding 3
- Pattern: Zero-price sentinel conflates missing asset with valid stored record
- Severity: MEDIUM
- Location: oracle/src/prices.rs:56
- Description: In protocol v2 history retrieval, a stored update can return price 0 for an asset even when the asset was not present in that record, because extraction uses 0 as a placeholder and retrieve_asset_price_data does not verify the asset bit in the record mask. This is inconsistent with legacy v1, where zero prices are skipped entirely, so a present zero-valued record is indistinguishable from an absent asset and can be returned incorrectly if history mask and stored record ever diverge.
- Evidence: oracle/src/prices.rs:76-87 returns 0 when the asset bit is not found in the update record (`0` sentinel). oracle/src/prices.rs:54-57 then loads the record and unconditionally returns `Some(normalize_price_data(price, timestamp))` after `let price = extract_single_update_record_price(&record, asset);` with no per-record mask check. Meanwhile oracle/src/prices.rs:293-299 explicitly skips zero prices in legacy storage (`if price == 0 { continue; }`), confirming zero is treated as absence on the write path. Thus zero serves both as 'missing asset' placeholder and as returned price value in v2 retrieval.

## Coverage Appendix

- beam-contract/src/cost.rs
- beam-contract/src/lib.rs
- oracle/src/assets.rs
- oracle/src/auth.rs
- oracle/src/events.rs
- oracle/src/lib.rs
- oracle/src/mapping.rs
- oracle/src/price_oracle.rs
- oracle/src/prices.rs
- oracle/src/protocol.rs
- oracle/src/settings.rs
- oracle/src/timestamps.rs
- oracle/src/types.rs
- pulse-contract/src/lib.rs
