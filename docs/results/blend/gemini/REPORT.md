# Scout-Agent Report

## ⚠️ DISCLAIMER: Partial Report

This report is **incomplete** because one or more audit tasks failed. 
The findings below only represent the successfully audited portion of the project. 
Please check the **Failures** section for details on what was missed.

## Metadata
- Project root: `/Users/josegarcia/Desktop/2025-02-blend/blend-contracts-v2`
- Generated at UTC: `2026-03-26T18:27:58Z`
- Model: `gemini:gemini-3.1-pro-preview`
- LLM mode: `creative`
- execution_path_consistency completed: `True`

## Summary
- Files reviewed: 73
- Verified findings: 24
- CRITICAL: 3
- HIGH: 18
- MEDIUM: 3
- LOW: 0

## Failures
- `pool/src/pool/reserve.rs`: PolicyViolationError - Expert shut down: reached 3 consecutive REPETITIVE_GREP violations.
- `pool/src/storage.rs`: PolicyViolationError - Expert shut down: reached 3 consecutive REPETITIVE_GREP violations.

## Findings

## Finding 1
- Pattern: Duplicate Array Elements Inflation
- Severity: CRITICAL
- Location: pool/src/auctions/bad_debt_auction.rs:47
- Description: Lack of duplicate checks in the `bid` array allows array elements to be processed multiple times. In `create_bad_debt_auction_data`, passing duplicate `bid_asset` addresses repeatedly adds the same liability balance to `debt_value`. However, the final `auction_data.bid` Map only stores the un-inflated liability per unique asset. This artificially inflates the requested `lot_amount` of backstop tokens for the auction without increasing the actual debt required to bid. An attacker can exploit this to drain backstop tokens by filling the auction: paying the normal debt while receiving heavily inflated backstop tokens in return. (A similar issue exists with `lot` duplicates in `create_interest_auction_data` inflating `interest_value`, which can be used to DoS interest auctions).
- Evidence:     let mut debt_value = 0;
    for bid_asset in bid {
        let reserve = pool.load_reserve(e, &bid_asset, false);
        let liability_balance = backstop_positions
            .liabilities
            .get(reserve.config.index)
            .unwrap_or(0);
        if liability_balance > 0 {
            let asset_to_base = pool.load_price(e, &reserve.asset);
            let asset_balance = reserve.to_asset_from_d_token(e, liability_balance);
            debt_value += i128(asset_to_base).fixed_mul_floor(e, &asset_balance, &reserve.scalar);
            auction_data.bid.set(reserve.asset, liability_balance);
        } else {
            panic_with_error!(e, PoolError::InvalidBid);
        }
    }

## Finding 2
- Pattern: Missing Array Duplicate Validation
- Severity: CRITICAL
- Location: pool/src/auctions/bad_debt_auction.rs:47-61
- Description: The `create_bad_debt_auction_data` function iterates over the `bid` vector to accumulate `debt_value` but lacks a check for duplicate addresses. While `auction_data.bid.set()` silently deduplicates the actual assumed liabilities in a map, the `debt_value` is continually increased for each duplicate element. This allows an attacker to inflate `debt_value` to artificially high levels and extract up to the total available backstop tokens via an inflated `lot_amount`, while only assuming the underlying deduplicated bad debt.
- Evidence: ```rust
    let mut debt_value = 0;
    for bid_asset in bid {
        let reserve = pool.load_reserve(e, &bid_asset, false);
        let liability_balance = backstop_positions
            .liabilities
            .get(reserve.config.index)
            .unwrap_or(0);
        if liability_balance > 0 {
            let asset_to_base = pool.load_price(e, &reserve.asset);
            let asset_balance = reserve.to_asset_from_d_token(e, liability_balance);
            // Multiple duplicate bid_assets will continually increment debt_value
            debt_value += i128(asset_to_base).fixed_mul_floor(e, &asset_balance, &reserve.scalar);
            // But the map will overwrite previous entries, leaving the actual bid unaltered
            auction_data.bid.set(reserve.asset, liability_balance);
        } else {
            panic_with_error!(e, PoolError::InvalidBid);
        }
    }
```

## Finding 3
- Pattern: Missing state cache update
- Severity: CRITICAL
- Location: pool/src/pool/submit.rs:86
- Description: In `execute_submit_with_flash_loan`, the flash loan asset's reserve is loaded and modified but never cached back into the pool. This discards the liability modifications (like `d_supply` increments) and causes either a transaction revert or permanent corruption of the pool's debt accounting.
- Evidence: On `pool/src/pool/submit.rs:86-88`, a local `reserve` is loaded and mutated via `from_state.add_liabilities`, which increases `reserve.data.d_supply` and updates emissions. However, `pool.cache_reserve(reserve)` is missing before the block ends. Consequently, these changes are lost. Later, `pool.store_cached_reserves` will panic with `InternalReserveNotFound` if the asset is untouched by other requests, or it will overwrite the pool with stale ledger data if the same asset is modified by subsequent requests, permanently desynchronizing total pool liabilities from user liabilities.

## Finding 4
- Pattern: Sentinel value mismatch
- Severity: HIGH
- Location: backstop/src/backstop/fund_management.rs:40
- Description: Passing 0 for shares in execute_donate increases the token balance while leaving the shares unchanged, decoupling them and breaking the shares == 0 sentinel.
- Evidence: In fund_management.rs, pool_balance.deposit(amount, 0) is called during execute_donate. This increases self.tokens but leaves self.shares at 0. PoolBalance::convert_to_shares (in pool.rs) strictly relies on self.shares == 0 as the sentinel for an uninitialized pool, returning tokens at a 1:1 ratio. Because the sentinel ignores the newly non-zero self.tokens, the next depositor will be granted 1:1 shares, instantly capturing all previously donated tokens.

## Finding 5
- Pattern: Time-state update omitted before mutation
- Severity: HIGH
- Location: backstop/src/backstop/withdrawal.rs:56
- Description: execute_withdraw mutates user and pool balances without first refreshing the time-dependent emission state, allowing the next depositor to unfairly accrue past emissions if the pool becomes empty.
- Evidence: In backstop/src/backstop/withdrawal.rs, execute_withdraw calls user_balance.withdraw_shares and pool_balance.withdraw (modifying shares and q4w) without first calling emissions::update_emissions (unlike execute_queue_withdrawal). If pool_balance.shares is reduced to 0, the next update_emission_data call returns early without advancing emis_data.last_time. This preserves the accumulated un-updated time elapsed, which is later unfairly distributed to the next depositor(s).

## Finding 6
- Pattern: Time-state update skipped before mutation
- Severity: HIGH
- Location: backstop/src/backstop/withdrawal.rs:62
- Description: execute_withdraw mutates pool_balance without calling emissions::update_emissions first. If all shares are withdrawn, the missing timestamp sync incorrectly allows the next depositor to accrue emissions from the entire empty pool duration.
- Evidence: In execute_withdraw (backstop/src/backstop/withdrawal.rs:50-71), pool_balance.withdraw() mutates pool_balance.shares but emissions::update_emissions is never called. Consequently, if the pool is emptied (pool_balance.shares drops to 0), the emissions timestamp (last_time) is not synced. Because emissions::update_emission_data early-returns when shares == 0, the next user's deposit will also fail to update last_time. When the new depositor later updates emissions, the index calculation uses the ancient last_time, allowing them to illegitimately claim emissions for the entire period when the pool was empty.

## Finding 7
- Pattern: Missing Array Deduplication
- Severity: HIGH
- Location: backstop/src/emissions/claim.rs:18
- Description: Lack of duplicate validation in `pool_addresses` causes user's claimed backstop emissions to be overwritten with 0 in the `claims` map, permanently locking their swapped LP tokens in the contract.
- Evidence: In `execute_claim`, the loop over `pool_addresses` calls `claim_emissions`, adding to `claimed` and storing `claims.set(pool_id, claim_amt)`. A duplicate address makes the second call return 0 (since accrued emissions are cleared) and overwrites the map entry to 0. The second loop then calculates `deposit_amount` as 0 for both iterations based on `claims.get(pool_id)`. The total LP tokens swapped from the user's `claimed` amount are left permanently locked in the backstop contract without crediting the user's balance.

## Finding 8
- Pattern: Unvalidated Array Duplicates
- Severity: HIGH
- Location: backstop/src/emissions/claim.rs:18
- Description: Passing duplicate `pool_id`s in `pool_addresses` causes the second call to `claim_emissions` to return 0 (as accrued emissions were cleared on the first pass). This 0 is then used in `claims.set`, overwriting the user's legitimately claimed amount. In the second loop, `claims.get` returns 0, leading to a calculated `deposit_amount` of 0. As a result, the claimed emissions are permanently stuck in the backstop contract and the user loses their funds.
- Evidence: let mut claims: Map<Address, i128> = Map::new(e);
for pool_id in pool_addresses.iter() {
    // ...
    let claim_amt = claim_emissions(...); // Returns 0 on duplicate pass
    claimed += claim_amt;
    claims.set(pool_id, claim_amt); // Overwrites the legitimate amount with 0
}
// ...
for pool_id in pool_addresses.iter() {
    let claim_amount = claims.get(pool_id.clone()).unwrap(); // Returns 0 for duplicated pools
    let deposit_amount = lp_tokens_out.fixed_mul_floor(claim_amount, claimed).unwrap(); // Evaluates to 0
    // ...
}

## Finding 9
- Pattern: Missing slippage protection
- Severity: HIGH
- Location: backstop/src/emissions/claim.rs:49
- Description: The `execute_claim` function calls `dep_tokn_amt_in_get_lp_tokns_out` on the Comet AMM to convert claimed BLND emissions into LP tokens, but hardcodes `0` as the minimum LP tokens out. This lack of slippage protection exposes the backstop to sandwich attacks, allowing an attacker to extract value from the claimed emissions by manipulating the pool's exchange rate.
- Evidence: In `execute_claim`, the call to `CometClient::new(e, &lp_id).dep_tokn_amt_in_get_lp_tokns_out` passes `&0` as its third argument (`min_lp_out`). This hardcoded zero disables slippage protection during the AMM deposit, meaning the contract will accept any amount of LP tokens returned, no matter how small.

## Finding 10
- Pattern: Time-state update omitted on early return
- Severity: HIGH
- Location: backstop/src/emissions/distributor.rs:59
- Description: update_emission_data returns early without updating last_time when pool_balance.shares == 0. When a user deposits into an empty pool, their deposit leaves last_time unchanged. On their next interaction, they retroactively receive all emissions from the empty period.
- Evidence: Lines 59-66 in backstop/src/emissions/distributor.rs return `Some(emis_data)` if `pool_balance.shares == 0` without updating `emis_data.last_time` in storage. Since `execute_deposit` calls `update_emission_data` before increasing `shares`, `last_time` remains unupdated. On the next emission update with `shares > 0`, the delta `max_timestamp - emis_data.last_time` will span the entire period the pool was empty, retroactively distributing those emissions to the new depositor.

## Finding 11
- Pattern: Missing sentinel guard
- Severity: HIGH
- Location: backstop/src/emissions/manager.rs:228
- Description: update_rz_emis_data lacks a check for the i128::MAX sentinel marking removed pools. Calling gulp_emissions on a removed pool evaluates to_gulp = true, subtracts i128::MAX from gulp_index resulting in negative accrued emissions, and silently overwrites the pool's index to gulp_index. This destroys the sentinel and allows the removed pool to accrue and steal emissions from legitimate pools on subsequent actions.
- Evidence: In `remove_pool`, the emission index is set to `i128::MAX` as a sentinel. However, `update_rz_emis_data` does not guard against this. At line 228, `if emission_data.index < gulp_index || to_gulp` allows execution because `to_gulp` is true for `gulp_emissions`. The calculation `gulp_index - emission_data.index` yields a large negative `new_emissions`. `set_rz_emissions` then saves the normal `gulp_index`, permanently wiping the `i128::MAX` sentinel.

## Finding 12
- Pattern: Sentinel Value Bypass
- Severity: HIGH
- Location: backstop/src/emissions/manager.rs:228
- Description: The `i128::MAX` sentinel value used to disable emissions for removed pools is bypassed when `gulp_emissions` is called. The `emission_data.index < gulp_index || to_gulp` condition allows execution to proceed when `to_gulp = true`, calculating `gulp_index - i128::MAX`. This evaluates to a large negative number without panicking and results in a negative emission calculation. `set_rz_emissions` is then called, which overwrites the `i128::MAX` sentinel with `gulp_index` and clears any previously accrued emissions. Consequently, the removed pool will silently begin receiving unauthorized emissions again in subsequent distributions.
- Evidence: In `update_rz_emis_data`, line 228 evaluates to true because `to_gulp` is true, bypassing the intended `index < gulp_index` safeguard. At line 232, `gulp_index - emission_data.index` becomes `gulp_index - i128::MAX`, yielding a negative value that fits in `i128`. The resulting negative emission is added to `accrued`, and `set_rz_emissions` (line 235) saves the state with `index = gulp_index` and `accrued = 0`. This completely removes the `i128::MAX` sentinel.

## Finding 13
- Pattern: State mutated before time-dependent accrual
- Severity: HIGH
- Location: backstop/src/emissions/manager.rs:94
- Description: remove_pool mutates the pool's emission data index to i128::MAX without first lazily updating its accrued rewards. This causes the pool to permanently lose any emissions that accumulated globally since its last local update.
- Evidence: Lines 94-95 directly read get_rz_emis_data and write back the old unaccrued amount while setting the index to i128::MAX. Any pending rewards between the pool's last updated index and the global gulp_index are ignored and lost because update_rz_emis_data is not called prior to modification.

## Finding 14
- Pattern: Missing TTL extension on initial storage creation
- Severity: HIGH
- Location: backstop/src/storage.rs:236
- Description: Newly created persistent storage entries for users (such as balances and emission data) are at risk of premature expiration because their Time-To-Live (TTL) is never extended upon creation.
- Evidence: The function `get_persistent_default` (lines 104-112) only calls `extend_ttl` if the entry already exists; otherwise, it returns a default value. When a user deposits or receives emissions for the first time, setters like `set_user_balance` (line 236), `set_backstop_emis_data` (line 470), and `set_user_emis_data` (line 496) write the new entries using `e.storage().persistent().set()`, but they fail to call `extend_ttl` afterwards. This is inconsistent with how other storage components (e.g., `set_pool_balance` on line 272) are written. Consequently, these new user entries default to the network's initial minimal TTL and will be evicted quickly if the user does not interact with them again in a short window, leading to a complete loss of their funds or accrued rewards.

## Finding 15
- Pattern: Unvalidated Array Duplicates
- Severity: HIGH
- Location: pool/src/auctions/backstop_interest_auction.rs:42
- Description: The `lot` vector is iterated over to accumulate `interest_value` without checking for duplicate assets. Because `auction_data.lot.set()` overwrites the same key in the map, duplicate elements will artificially inflate the `interest_value` while the map correctly stores the item only once. This mismatch results in an artificially inflated required `bid_amount`, allowing attackers to bypass the minimum 200 USDC interest limit and create unfillable interest auctions, causing a Denial of Service (DoS) for legitimate liquidations.
- Evidence: ```rust
    let mut interest_value = 0; // expressed in the oracle's decimals
    for lot_asset in lot {
        // ...
        let reserve = pool.load_reserve(e, &lot_asset, false);
        if reserve.data.backstop_credit > 0 {
            let asset_to_base = pool.load_price(e, &reserve.asset);
            interest_value += i128(asset_to_base).fixed_mul_floor(
                e,
                &reserve.data.backstop_credit,
                &reserve.scalar,
            );
            auction_data
                .lot
                .set(reserve.asset, reserve.data.backstop_credit);
        }
    }
```

## Finding 16
- Pattern: Broken atomicity / MEV stealing
- Severity: HIGH
- Location: pool/src/auctions/user_liquidation_auction.rs:32
- Description: Setting `block` to `e.ledger().sequence() + 1` enforces a multi-block delay that breaks atomicity between auction creation and filling, exposing creators to MEV stealing.
- Evidence: In `create_user_liq_auction_data`, `block` is strictly initialized to `e.ledger().sequence() + 1`. Any attempt to fill the auction in the same sequence/block will result in an underflow panic when `scale_auction` calculates `e.ledger().sequence() - auction_data.block` (enforced by `overflow-checks = true` in Cargo.toml). Because of this forced delay, liquidators cannot atomically create and fill an auction in a single transaction. Instead, the auction must sit publicly on the ledger for at least one block, guaranteeing that MEV bots can snipe the profitable `fill` operation in the next block. This completely destroys the economic incentive to initiate liquidations (pay gas for `new_auction`), which can lead to the protocol accumulating bad debt.

## Finding 17
- Pattern: Time-dependent logic skipped during zero supply
- Severity: HIGH
- Location: pool/src/emissions/distributor.rs:163
- Description: In `update_emission_data`, returning early when `supply == 0` skips updating `last_time`. When a user subsequently deposits, the stale `last_time` causes the entire zero-supply period's emissions to accrue to the first depositor.
- Evidence: Lines 160-166 return early if `supply == 0` without updating `res_emission_data.last_time`. Later, when `supply > 0`, lines 174-176 calculate `additional_idx` using `e.ledger().timestamp() - res_emission_data.last_time`. Because `last_time` is stale, the time difference spans the entire empty-pool period, inappropriately allocating all accumulated emissions to the new supply.

## Finding 18
- Pattern: Time-state update before calculation
- Severity: HIGH
- Location: pool/src/emissions/manager.rs:133
- Description: update_reserve_emission_eps overwrites emission_data.last_time and computes un-emitted tokens using e.ledger().timestamp() instead of last_time, permanently losing accumulated emissions when supply is 0.
- Evidence: On lines 133-148, `emission_data.last_time` is forcefully updated to `e.ledger().timestamp()`, and un-emitted tokens are calculated using `emission_data.expiration - e.ledger().timestamp()`. If `supply == 0`, `distributor::update_emission_data` correctly refrains from advancing `last_time`, indicating emissions from `old_last_time` to `now` were skipped and should roll over. By using the current timestamp instead of the un-advanced `last_time`, all skipped emissions from `old_last_time` to `e.ledger().timestamp()` are ignored and permanently trapped in the contract. Additionally, if the previous emission block has expired (`expiration <= now`), the check `emission_data.expiration > e.ledger().timestamp()` incorrectly prevents recovering any of those skipped emissions.

## Finding 19
- Pattern: Time-state update after mutation
- Severity: HIGH
- Location: pool/src/pool/config.rs:44
- Description: execute_update_pool modifies pool_config.bstop_rate before accruing active reserves, retroactively applying the new rate to past unaccrued interest.
- Evidence: In execute_update_pool (lines 51, 56), pool_config.bstop_rate is mutated and saved to storage without first loading and accruing the active reserves. Because Reserve::accrue uses this globally configured bstop_rate to compute the backstop credit for the time elapsed since a reserve's last update, modifying it here retroactively applies the new take rate to all historical unaccrued interest.

## Finding 20
- Pattern: State Machine Flaw / Lost Sentinel Status
- Severity: HIGH
- Location: pool/src/pool/status.rs:37
- Description: The explicit `Admin On-Ice` (2) status is permanently erased if the pool undergoes a temporary health deterioration, incorrectly restoring the pool to fully `Active` and re-enabling borrows.
- Evidence: In `execute_update_pool_status`, if a pool is explicitly set to `Admin On-Ice` (status `2`), a backstop deterioration (`q4w_pct >= 75%`) transitions it to the permissionless `Frozen` state (status `5`) on line 40. Later, when health recovers (`q4w_pct < 30%`), the match falls into the `_ =>` branch for status `5`, unconditionally transitioning the pool to `Active` (status `1`) on line 60. By multiplexing admin overrides and health states into a single variable, the admin's explicit security pause is permanently lost and borrowing is incorrectly re-enabled.

## Finding 21
- Pattern: Same Operation, Inconsistent Paths
- Severity: HIGH
- Location: pool/src/pool/submit.rs:86
- Description: The `execute_submit_with_flash_loan` function allows users to bypass pool pause statuses (On-Ice/Frozen) and reserve-level disables when borrowing. It adds liabilities directly to the user's state without checking `pool.require_action_allowed` or `reserve.require_action_allowed`. If the user supplies sufficient collateral to pass the final health check, they can retain this flash loan debt, effectively borrowing restricted assets in violation of the protocol's emergency controls.
- Evidence: In `pool/src/pool/actions.rs`, normal borrows via `build_actions_from_request` properly validate the action against pool and reserve statuses:
```rust
    for request in requests.iter() {
        pool.require_action_allowed(e, request.request_type);
        // ... inside apply_borrow ...
        reserve.require_action_allowed(e, request.request_type);
```
These checks enforce that borrowing (action type 4) is blocked if `pool.config.status > 1` or if `!reserve.config.enabled`.

However, the flash loan path in `pool/src/pool/submit.rs:execute_submit_with_flash_loan` performs the exact same operation (minting debt tokens and adding liabilities) without verifying either restriction for the flash loan amount:
```rust
        let mut reserve = pool.load_reserve(e, &flash_loan.asset, true);
        let d_tokens_minted = reserve.to_d_token_up(e, flash_loan.amount);
        from_state.add_liabilities(e, &mut reserve, d_tokens_minted);
        reserve.require_utilization_below_max(e);
```
Because it skips checking `action_type == 4` for the flash loan but permits the debt to remain open if collateralized, users can maliciously borrow from frozen pools or disabled reserves.

## Finding 22
- Pattern: Missing sentinel guard
- Severity: MEDIUM
- Location: backstop/src/emissions/manager.rs:87
- Description: The uninitialized sentinel value 0 for last_distribution_time is used in arithmetic without a guard, unconditionally panicking and preventing pool removal.
- Evidence: In `remove_pool`, `get_last_distribution_time` returns 0 when unset. The code evaluates `if last_distribution < e.ledger().timestamp() - 24 * 60 * 60`, which is always true for 0 (epoch 0) on modern ledgers. Unlike `distribute` which explicitly checks `if last_distribution == 0`, `remove_pool` treats the sentinel as a literal timestamp, causing an unhandled panic (BackstopError::BadRequest) when attempting to swap or remove a pool before distributions have begun.

## Finding 23
- Pattern: Sentinel value handling
- Severity: MEDIUM
- Location: pool/src/pool/actions.rs:409
- Description: If a user passes a sentinel value like `i128::MAX` to 'repay all' debt via `submit()`, the contract pushes `request.amount` to the `spender_transfer` action instead of clamping it to their actual borrowed amount. The standard `submit()` function executes non-netted transfers, meaning it attempts a raw `transfer` of `i128::MAX` from the user, which reverts due to insufficient balance and breaks the sentinel 'repay all' capability.
- Evidence: In `apply_repay` (pool/src/pool/actions.rs:409), if the requested repayment burns more debt than the user owes (`d_tokens_burnt > cur_d_tokens`), the code correctly computes `amount_to_refund`. However, it registers the full `request.amount` (e.g., `i128::MAX`) for the spender transfer via `actions.add_for_spender_transfer(&reserve.asset, request.amount);`. When executed in `submit.rs` via `handle_transfers`, this calls `TokenClient::new(e, &address).transfer(spender, ...)` with `i128::MAX`. Since the spender does not have `i128::MAX` tokens, the transaction reverts.

## Finding 24
- Pattern: Skipped state storage
- Severity: MEDIUM
- Location: pool/src/pool/gulp.rs:23
- Description: execute_gulp returns early without calling reserve.store(e) when token_balance_delta <= 0, discarding time-dependent interest accruals and ir_mod updates calculated in Reserve::load.
- Evidence: In pool/src/pool/gulp.rs lines 23-25, the function returns early if token_balance_delta <= 0. As a result, the time-dependent state updates (such as ir_mod, d_rate, and last_time) that were performed inside Reserve::load on line 18 are not saved to the ledger. This prevents gulp from functioning as a proper permissionless crank and causes interest rates to stagnate between user interactions when there are no excess tokens to gulp.

## Coverage Appendix

- backstop/src/backstop/deposit.rs
- backstop/src/backstop/fund_management.rs
- backstop/src/backstop/mod.rs
- backstop/src/backstop/pool.rs
- backstop/src/backstop/user.rs
- backstop/src/backstop/withdrawal.rs
- backstop/src/constants.rs
- backstop/src/contract.rs
- backstop/src/dependencies/comet.rs
- backstop/src/dependencies/mod.rs
- backstop/src/dependencies/pool_factory.rs
- backstop/src/emissions/claim.rs
- backstop/src/emissions/distributor.rs
- backstop/src/emissions/manager.rs
- backstop/src/emissions/mod.rs
- backstop/src/errors.rs
- backstop/src/events.rs
- backstop/src/lib.rs
- backstop/src/storage.rs
- backstop/src/testutils.rs
- mocks/mock-pool-factory/src/errors.rs
- mocks/mock-pool-factory/src/lib.rs
- mocks/mock-pool-factory/src/pool_factory.rs
- mocks/mock-pool-factory/src/storage.rs
- mocks/moderc3156/src/lib.rs
- pool-factory/src/errors.rs
- pool-factory/src/events.rs
- pool-factory/src/lib.rs
- pool-factory/src/pool_factory.rs
- pool-factory/src/storage.rs
- pool-factory/src/test.rs
- pool/src/auctions/auction.rs
- pool/src/auctions/backstop_interest_auction.rs
- pool/src/auctions/bad_debt_auction.rs
- pool/src/auctions/mod.rs
- pool/src/auctions/user_liquidation_auction.rs
- pool/src/constants.rs
- pool/src/contract.rs
- pool/src/dependencies/backstop.rs
- pool/src/dependencies/mod.rs
- pool/src/emissions/distributor.rs
- pool/src/emissions/manager.rs
- pool/src/emissions/mod.rs
- pool/src/errors.rs
- pool/src/events.rs
- pool/src/lib.rs
- pool/src/pool/actions.rs
- pool/src/pool/bad_debt.rs
- pool/src/pool/config.rs
- pool/src/pool/gulp.rs
- pool/src/pool/health_factor.rs
- pool/src/pool/interest.rs
- pool/src/pool/mod.rs
- pool/src/pool/pool.rs
- pool/src/pool/status.rs
- pool/src/pool/submit.rs
- pool/src/pool/user.rs
- pool/src/testutils.rs
- pool/src/validator.rs
- test-suites/fuzz/fuzz_targets/fuzz_pool_general.rs
- test-suites/fuzz/lib.rs
- test-suites/src/assertions.rs
- test-suites/src/backstop.rs
- test-suites/src/emitter.rs
- test-suites/src/lib.rs
- test-suites/src/liquidity_pool.rs
- test-suites/src/moderc3156.rs
- test-suites/src/oracle.rs
- test-suites/src/pool.rs
- test-suites/src/pool_factory.rs
- test-suites/src/setup.rs
- test-suites/src/snapshot.rs
- test-suites/src/token.rs
