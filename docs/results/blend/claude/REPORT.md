# Scout-Agent Report

## ⚠️ DISCLAIMER: Partial Report

This report is **incomplete** because one or more audit tasks failed. 
The findings below only represent the successfully audited portion of the project. 
Please check the **Failures** section for details on what was missed.

## Metadata
- Project root: `/home/francis/Repos/scout-agent/benchmark/2025-02-blend-f23b3260763488f365ef6a95bfb139c95b0ed0f9/blend-contracts-v2`
- Generated at UTC: `2026-03-27T18:10:36Z`
- Model: `anthropic:claude-opus-4-6`
- LLM mode: `creative`
- execution_path_consistency completed: `False`

## Summary
- Files reviewed: 72
- Verified findings: 0
- CRITICAL: 0
- HIGH: 0
- MEDIUM: 0
- LOW: 0

## Failures
- `backstop/src/dependencies/comet.rs`: StructuredOutputValidationError - Failed to parse structured output for tool 'FileAuditResponse': Native structured output expected valid JSON for FileAuditResponse, but parsing failed: Expecting value: line 1 column 1 (char 0)..
- `backstop/src/emissions/distributor.rs`: StructuredOutputValidationError - Failed to parse structured output for tool 'ExpertResult': Native structured output expected valid JSON for ExpertResult, but parsing failed: Expecting value: line 1 column 1 (char 0)..
- `mocks/moderc3156/src/lib.rs`: StructuredOutputValidationError - Failed to parse structured output for tool 'FileAuditResponse': Native structured output expected valid JSON for FileAuditResponse, but parsing failed: Expecting value: line 1 column 1 (char 0)..
- `execution_path_consistency`: StructuredOutputValidationError - Failed to parse structured output for tool 'FileAuditResponse': Native structured output expected valid JSON for FileAuditResponse, but parsing failed: Expecting value: line 1 column 1 (char 0)..

## Findings

No verified findings.

## Coverage Appendix

- backstop/src/backstop/deposit.rs
- backstop/src/backstop/fund_management.rs
- backstop/src/backstop/mod.rs
- backstop/src/backstop/pool.rs
- backstop/src/backstop/user.rs
- backstop/src/backstop/withdrawal.rs
- backstop/src/constants.rs
- backstop/src/contract.rs
- backstop/src/dependencies/mod.rs
- backstop/src/dependencies/pool_factory.rs
- backstop/src/emissions/claim.rs
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
