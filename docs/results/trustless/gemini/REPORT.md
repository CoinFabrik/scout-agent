# Scout-Agent Report

## Metadata
- Project root: `/Users/josegarcia/Desktop/Trustless-Work-Smart-Escrow`
- Generated at UTC: `2026-03-26T15:37:39Z`
- Model: `gemini:gemini-3.1-pro-preview`
- LLM mode: `creative`
- execution_path_consistency completed: `True`

## Summary
- Files reviewed: 14
- Verified findings: 6
- CRITICAL: 0
- HIGH: 3
- MEDIUM: 1
- LOW: 2

## Findings

## Finding 1
- Pattern: Function Containment
- Severity: HIGH
- Location: contracts/escrow/src/core/escrow.rs:76
- Description: The `change_escrow_properties` function allows `platform_address` to overwrite the stored `Escrow`, containing the operation of modifying a milestone's `status` or `evidence`. Calling this path bypasses the strict `service_provider.require_auth()` restriction enforced in the dedicated `change_milestone_status` function.
- Evidence: In `contracts/escrow/src/core/milestone.rs:21`, mutating a milestone requires `service_provider.require_auth()`. In `contracts/escrow/src/core/escrow.rs:76-96`, `change_escrow_properties` blindly stores the provided `escrow_properties` with only `platform_address.require_auth()`. `validate_escrow_property_change_conditions` does not restrict modifying `status` or `evidence`, bypassing the service provider authorization.

## Finding 2
- Pattern: Same Operation, Inconsistent Paths
- Severity: HIGH
- Location: contracts/escrow/src/core/validators/escrow.rs:41
- Description: The `change_escrow_properties` and `initialize_escrow` functions perform the same operation of writing `Escrow` parameters to storage. However, `update_escrow` does not enforce the invariants applied during initialization, allowing users to bypass restrictions on maximum `platform_fee`, zero `amount`, and maximum `milestones.len()`.
- Evidence: `validate_initialize_escrow_conditions` enforces `escrow_properties.platform_fee <= 9900` and validates `escrow_properties.amount` and `escrow_properties.milestones.len()` (lines 86-115). `validate_escrow_property_change_conditions` completely omits the `platform_fee` check and mistakenly applies the `amount` and `milestones.len()` checks to the `existing_escrow` instead of `new_escrow` (lines 74, 78). This provides an unrestricted path to write invalid parameters.

## Finding 3
- Pattern: Bypass of sentinel guard
- Severity: HIGH
- Location: contracts/escrow/src/core/validators/escrow.rs:74
- Description: The validate_escrow_property_change_conditions function incorrectly checks existing_escrow.amount instead of new_escrow.amount for the 0 sentinel limit. This allows a caller to bypass initialization limits and set the new escrow amount to 0 (and configure more than 10 milestones).
- Evidence: On lines 74-80 of contracts/escrow/src/core/validators/escrow.rs, the checks `if existing_escrow.amount == 0` and `if existing_escrow.milestones.len() > 10` validate the pre-existing state. The `new_escrow` properties are never verified for these constraints, enabling `change_escrow_properties` to overwrite the state with the uninitialized 0 amount sentinel or an excessive number of milestones.

## Finding 4
- Pattern: Inconsistent sentinel and state flag handling
- Severity: MEDIUM
- Location: contracts/escrow/src/core/dispute.rs:60
- Description: resolve_dispute executes unconditional token transfers for fees (allowing 0-value transfers) while conditionally guarding net funds with `> 0`, and resets the `disputed` flag, allowing dispute_escrow to re-dispute an already resolved escrow.
- Evidence: At lines 60-61, `trustless_work_fee` and `platform_fee` are transferred without the `> 0` sentinel check that is explicitly used for `net_approver_funds` (line 63) and `net_receiver_funds` (line 67). Additionally, at lines 72-73, `resolve_dispute` sets `resolved = true` and `disputed = false`. Because `dispute_escrow` (line 86) and its underlying validation only require `!escrow.flags.disputed` and fail to check if `resolved == true`, the explicit clearing of the `disputed` flag allows a resolved escrow to incorrectly re-enter the disputed state.

## Finding 5
- Pattern: Array Duplicate Validation
- Severity: LOW
- Location: contracts/escrow/src/core/escrow.rs:111
- Description: The `addresses` vector is iterated over without checking for duplicate values, which can cause redundant cross-contract queries and result in duplicate entries in the returned balances vector.
- Evidence:         let mut balances: Vec<AddressBalance> = Vec::new(&e);
        for address in addresses.iter() {
            let escrow = Self::get_escrow_by_contract_id(e.clone(), &address)?;
            let token_client = TokenClient::new(&e, &escrow.trustline.address);
            let balance = token_client.balance(&address);
            balances.push_back(AddressBalance {
                address: address.clone(),
                balance,
                trustline_decimals: escrow.trustline.decimals,
            });
        }

## Finding 6
- Pattern: Same Operation, Inconsistent Paths
- Severity: LOW
- Location: contracts/escrow/src/core/escrow.rs:99
- Description: The `get_multiple_escrow_balances` function performs the operation of reading escrow state and token balances while enforcing `signer.require_auth()`. Users can bypass this restriction by using the unauthenticated `get_escrow_by_contract_id` and token `balance` functions, which perform the same operation without authorization.
- Evidence: In `contracts/escrow/src/core/escrow.rs:104`, `signer.require_auth()` is enforced in `get_multiple_escrow_balances`. However, the underlying data retrieval paths `get_escrow_by_contract_id` (line 124) and the token client `balance` function are completely unauthenticated, rendering the authorization check bypassable and inconsistent.

## Coverage Appendix

- contracts/escrow/src/contract.rs
- contracts/escrow/src/core/dispute.rs
- contracts/escrow/src/core/escrow.rs
- contracts/escrow/src/core/milestone.rs
- contracts/escrow/src/core/validators/dispute.rs
- contracts/escrow/src/core/validators/escrow.rs
- contracts/escrow/src/core/validators/milestone.rs
- contracts/escrow/src/error.rs
- contracts/escrow/src/events/handler.rs
- contracts/escrow/src/lib.rs
- contracts/escrow/src/modules/fee/calculator.rs
- contracts/escrow/src/modules/math/basic.rs
- contracts/escrow/src/modules/math/safe.rs
- contracts/escrow/src/storage/types.rs
