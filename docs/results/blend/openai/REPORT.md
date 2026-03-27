# Scout-Agent Report

## Metadata
- Project root: `/Users/josegarcia/Desktop/2025-02-blend/blend-contracts-v2`
- Generated at UTC: `2026-03-26T20:24:03Z`
- Model: `openai:gpt-5.4`
- LLM mode: `creative`
- execution_path_consistency completed: `True`

## Summary
- Files reviewed: 75
- Verified findings: 10
- CRITICAL: 0
- HIGH: 1
- MEDIUM: 9
- LOW: 0

## Findings

## Finding 1
- Pattern: Unauthorized reward-zone management path
- Severity: HIGH
- Location: backstop/src/contract.rs:274
- Description: Reward-zone membership changes are exposed through public contract entrypoints without the pool authorization required by the related `gulp_emissions` path, allowing arbitrary callers to add or remove pools from the reward zone if state predicates are met.
- Evidence: Guarded related path: `gulp_emissions` requires pool auth before reward-zone emission updates: `backstop/src/contract.rs:265-268` -> `pool.require_auth(); let (backstop_emissions, pool_emissions) = emissions::gulp_emissions(&e, &pool);`. Bypass/inconsistent paths: `add_reward` and `remove_reward` are public entrypoints with no `require_auth` at all: `backstop/src/contract.rs:274-276` -> `fn add_reward(...) { storage::extend_instance(&e); emissions::add_to_reward_zone(&e, to_add.clone(), to_remove.clone()); }` and `backstop/src/contract.rs:281-283` -> `fn remove_reward(...) { storage::extend_instance(&e); emissions::remove_from_reward_zone(&e, to_remove.clone()); }`. The underlying state-changing functions themselves also perform no auth checks: `backstop/src/emissions/manager.rs:18-23, 66-76` directly mutate `reward_zone` storage.

## Finding 2
- Pattern: Duplicate addresses in drop list
- Severity: MEDIUM
- Location: backstop/src/contract.rs:185
- Description: The constructor accepts and persists drop_list without enforcing unique recipient addresses. Later, drop() forwards the stored vector directly to emitter_client.drop after appending another entry. If drop_list contains the same address multiple times, that address will appear multiple times in the downstream distribution list, which can duplicate payout entries and alter intended allocation semantics.
- Evidence: In __constructor, the code only sums amounts and stores the vector: `let mut drop_total: i128 = 0; for (_, amount) in drop_list.iter() { drop_total += amount; } ... storage::set_drop_list(&e, &drop_list);` (backstop/src/contract.rs:185-192). There is no duplicate-address check before persistence. In drop(), the stored vector is loaded and passed through directly: `let mut drop_list = storage::get_drop_list(&e); ... drop_list.push_back((e.current_contract_address(), backfilled_emissions)); ... emitter_client.drop(&drop_list)` (backstop/src/contract.rs:299-303). Storage also preserves the raw `Vec<(Address, i128)>` unchanged: `get_drop_list` / `set_drop_list` in backstop/src/storage.rs:514-528.

## Finding 3
- Pattern: Duplicate addresses in claim input
- Severity: MEDIUM
- Location: backstop/src/emissions/claim.rs:18
- Description: execute_claim does not enforce uniqueness of pool_addresses. Duplicate pool addresses are summed into total claimed on the first loop, but per-pool claim amounts are stored in a Map keyed by Address, so duplicates overwrite the same entry. The second loop then reuses that same stored claim for each duplicate entry, causing repeated deposit/accounting for the same pool while claimed was already inflated by duplicate occurrences.
- Evidence: At lines 18-25, the function iterates over pool_addresses and adds every occurrence into the aggregate total: `for pool_id in pool_addresses.iter() { ... let claim_amt = claim_emissions(...); claimed += claim_amt; claims.set(pool_id, claim_amt); }`. Because `claims` is `Map<Address, i128>` (line 17), `claims.set(pool_id, claim_amt)` overwrites prior entries for the same Address. Later, lines 55-59 iterate `pool_addresses` again: `for pool_id in pool_addresses.iter() { let claim_amount = claims.get(pool_id.clone()).unwrap(); let deposit_amount = lp_tokens_out.fixed_mul_floor(claim_amount, claimed).unwrap(); }`. For duplicate addresses, each duplicate reads the same single map entry and executes deposit/accounting again, while `claimed` still includes every duplicate occurrence from the first loop.

## Finding 4
- Pattern: Backfill recovery resets time-dependent state and skips accrued interval
- Severity: MEDIUM
- Location: backstop/src/emissions/manager.rs:141
- Description: When distribute recovers from backfill mode, it clears backfill_status and overwrites last_distribution_time with the emitter timestamp before processing accrued emissions, permanently discarding the interval between the stored backfill time and the emitter's last distribution. This also shifts the 24-hour freshness gate used by remove_pool to the synthetic reset time rather than the last accounted distribution.
- Evidence: In distribute, an emitter lookup failure uses the current ledger time as a synthetic source (`Err(_) => { is_backfill = true; ... e.ledger().timestamp() }`, lines 119-125). Subsequent calls can advance accounting and persist that time (`new_emissions = i128(emitter_last_distribution - last_distribution) * SCALAR_7`, lines 159-170; `storage::set_last_distribution_time(e, &emitter_last_distribution)`, line 172). When the emitter later recovers and prior backfill_status was true, the function sets `needs_reset = true` and flips status false (lines 112-115), then immediately executes `storage::set_last_distribution_time(e, &emitter_last_distribution); return 0;` (lines 141-143). The code comment explicitly states: `This skips all emissions between the last distribution time and the emitter's last distribution time` (lines 136-140). The included test `test_distribute_backfill_emissions_over_needs_reset` confirms the behavior: starting from `last_distribution_time = 1713139200 - 10000` with backfill_status true, after `distribute(&e)` the reward index is unchanged, backfilled emissions are unchanged, and `last_distribution_time` is reset to `emitter_distro_time` (lines 1117-1121, 1151-1160). Since remove_pool only checks `storage::get_last_distribution_time(e) < e.ledger().timestamp() - 24*60*60` (lines 85-90), this reset can also make removal freshness depend on the reset timestamp rather than on actual settled emissions.

## Finding 5
- Pattern: Duplicate asset addresses overwrite map entries after repeated value accumulation
- Severity: MEDIUM
- Location: pool/src/auctions/auction.rs:75
- Description: create_auction forwards bid/lot vectors without uniqueness checks into helpers that iterate the vectors, accumulate auction value per occurrence, but store results in Map<Address,i128>. Repeated asset addresses can therefore increase calculated debt/interest totals while only one map entry survives, producing inflated lot/bid amounts and unsafe stored AuctionData.
- Evidence: auction.rs forwards bid and lot unchanged: `create_user_liq_auction_data(e, user, bid, lot, percent)`, `create_bad_debt_auction_data(e, user, bid, lot, percent)`, `create_interest_auction_data(e, user, bid, lot, percent)` (pool/src/auctions/auction.rs:85-89). In bad_debt_auction.rs, duplicates are counted in `debt_value += ...` inside `for bid_asset in bid { ... }` and then written with `auction_data.bid.set(reserve.asset, liability_balance);` so a duplicate address increases `debt_value` multiple times while the map key is overwritten once (pool/src/auctions/bad_debt_auction.rs:47-57). In backstop_interest_auction.rs, duplicates are counted in `interest_value += ...` inside `for lot_asset in lot { ... }` and then written with `auction_data.lot.set(reserve.asset, reserve.data.backstop_credit);`, again overwriting a single key after repeated accumulation (pool/src/auctions/backstop_interest_auction.rs:42-55). In user_liquidation_auction.rs, duplicates are inserted via `.set(reserve.config.index, amount)` for repeated assets (pool/src/auctions/user_liquidation_auction.rs:56-80), so no uniqueness enforcement exists before stored AuctionData is constructed.

## Finding 6
- Pattern: Missing zero-bid guard
- Severity: MEDIUM
- Location: pool/src/auctions/backstop_interest_auction.rs:103
- Description: fill_interest_auction treats a missing backstop-token bid entry as 0 via unwrap_or(0), skips donation, and still transfers the lot, so malformed/partial AuctionData with an empty bid can be filled for free.
- Evidence: create_interest_auction_data always creates a non-empty bid for the backstop token (`pool/src/auctions/backstop_interest_auction.rs:71-85`), but fill_interest_auction does not enforce that invariant: `let backstop_token_bid_amount = auction_data.bid.get(backstop_token).unwrap_or(0); if backstop_token_bid_amount > 0 { ...donate... }` followed by unconditional lot transfers in lines 112-121. The dedicated test `test_fill_interest_auction_empty_bid` constructs `AuctionData { bid: map![&e], lot: ... }` and successfully calls fill_interest_auction, asserting the filler receives both underlying assets while the backstop token balance is unchanged (`pool/src/auctions/backstop_interest_auction.rs:1239-1355`).

## Finding 7
- Pattern: Duplicate lot entries inflate auction valuation
- Severity: MEDIUM
- Location: pool/src/auctions/backstop_interest_auction.rs:42
- Description: create_interest_auction_data iterates the caller-supplied lot vector and adds each reserve's backstop_credit into interest_value before writing to auction_data.lot as a map. Duplicate asset addresses are not rejected, so the same reserve can be counted multiple times in interest_value while the map keeps only one final lot entry, causing an inflated bid amount and inconsistent auction pricing/state.
- Evidence: The function only checks max_positions against lot.len() and then loops directly over lot: `for lot_asset in lot { ... interest_value += ... reserve.data.backstop_credit ...; auction_data.lot.set(reserve.asset, reserve.data.backstop_credit); }` (lines 30, 42-55). Because `auction_data.lot` is a map, duplicate keys overwrite rather than accumulate, while `interest_value` is incremented on every iteration. Later, `bid_amount` is derived from this inflated `interest_value` (`let bid_amount = interest_value ...`, lines 82-84), but `fill_interest_auction` transfers/decrements only the deduplicated map entries by iterating `auction_data.lot.iter()` (lines 113-121).

## Finding 8
- Pattern: Duplicate elements in bid inflate debt_value
- Severity: MEDIUM
- Location: pool/src/auctions/bad_debt_auction.rs:47
- Description: create_bad_debt_auction_data iterates the caller-supplied bid Vec<Address> without enforcing uniqueness. Repeating the same asset causes its liability value to be added to debt_value multiple times, while auction_data.bid stores only one map entry for that asset, inflating the computed lot amount relative to the actual bid assets transferred.
- Evidence: In create_bad_debt_auction_data, `for bid_asset in bid { ... debt_value += ...; auction_data.bid.set(reserve.asset, liability_balance); }` (lines 47-57). Because `auction_data.bid` is a map keyed by asset, duplicate `bid_asset` entries overwrite the same key instead of creating multiple transferable positions, but `debt_value` is incremented on every iteration. The resulting `debt_value` is then used directly to compute `lot_amount` at lines 85-88: `let mut lot_amount = debt_value.fixed_mul_floor(...).fixed_div_floor(...);`. Later, fill_bad_debt_auction transfers only `auction_data.bid.clone()` (unique map entries) at lines 107-108, confirming duplicated inputs increase payout without increasing assets received.

## Finding 9
- Pattern: Duplicate elements in claim vector cause repeated accrual processing
- Severity: MEDIUM
- Location: pool/src/emissions/distributor.rs:15
- Description: `claim` forwards user-supplied `reserve_token_ids` without uniqueness validation, and `execute_claim` iterates every element and adds each claim result to `to_claim`. Duplicate reserve token ids therefore trigger repeated processing of the same reserve token claim path and can inflate the claimed total.
- Evidence: `pool/src/contract.rs:517-523` passes `reserve_token_ids` directly to `emissions::execute_claim(&e, &from, &reserve_token_ids, &to)`. In `pool/src/emissions/distributor.rs:18-19`, `let mut to_claim = 0; for reserve_token_id in reserve_token_ids.clone() { ... }`. Inside that loop, each iteration does `to_claim += claim_emissions(... reserve_token_id, ...);` at lines 37-44, with no deduplication check before accumulation. By contrast, `set_emissions_config` writes entries into `Map<u32,u64>` via `pool_emissions.set(key, metadata.share)` (`pool/src/emissions/manager.rs:38-50`), so duplicate metadata keys overwrite rather than accumulate, and `new_auction` similarly stores bid/lot into `Map<Address,i128>` with `.set(...)` (`pool/src/auctions/user_liquidation_auction.rs:61-64,78-80`, `bad_debt_auction.rs:57,89`, `backstop_interest_auction.rs:53-55,85`), so duplicates do not inflate auction amounts.

## Finding 10
- Pattern: Queue-time timelock bypass via stale status snapshot
- Severity: MEDIUM
- Location: pool/src/pool/config.rs:65
- Description: execute_queue_set_reserve fixes unlock_time using the status only at queue time. If a reserve is queued while status==6, it receives an immediate unlock_time and can still be executed later after status changes because execute_set_reserve re-checks only timestamp, not current status.
- Evidence: In pool/src/pool/config.rs, execute_initialize sets `status: 6` (lines 27-33). Then execute_queue_set_reserve starts with `let mut unlock_time = e.ledger().timestamp();` and only adds `SECONDS_PER_WEEK` when `storage::get_pool_config(e).status != 6` (lines 65-69), so status 6 queues are immediately unlocked. Later, execute_set_reserve only verifies `if queued_init.unlock_time > e.ledger().timestamp()` and then deletes the queue entry before initialization (lines 87-97); it never re-reads pool status. Separately, pool/src/pool/status.rs shows status can transition away from 6 (`execute_update_pool_status` maps non-admin statuses to 1/3/5 at lines 51-60, and `execute_set_pool_status` can set 0/2/3/4 at lines 80-116). Therefore a reserve queued during setup can remain immediately executable even after the pool leaves setup, bypassing the intended one-week timelock for non-setup status.

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
- pool/src/pool/reserve.rs
- pool/src/pool/status.rs
- pool/src/pool/submit.rs
- pool/src/pool/user.rs
- pool/src/storage.rs
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
