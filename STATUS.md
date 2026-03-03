# Status

## Current Shape

Scout-Agent is operating as a straightforward two-command CLI:

- `extract-facts` produces `FACTS.yaml`
- `audit` consumes `FACTS.yaml` and produces `REPORT.md`
- console output is plain text only
- execution is single-run with no persisted run state

## Implemented

- production-only Rust source discovery
- file-level fact extraction with bounded parallelism
- typed `FactsDocument` generation
- audit initialization from `FACTS.yaml`
- LangGraph supervisor/expert workflow
- strict supervisor decision validation
- duplicate delegation fail-fast behavior
- markdown report generation

## Known Gaps

- broader fixture coverage for real-world Soroban projects
- provider timeout and retry tuning
- more audit-specialized evaluation data
- more report-quality regression tests
