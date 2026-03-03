# Runtime Graph

## Nodes

- `supervisor`
- `router`
- `expert`
- `reducer`
- `advance_file`
- `report`

## Flow

1. start at `supervisor`
2. build a `SupervisorDecision`
3. `router` sends work to `expert` if there are pending delegations
4. `reducer` folds expert output into the shared state
5. `advance_file` moves to the next file when the current file is complete
6. `report` writes `REPORT.md` after the last file

## Router Rules

- if `pending_delegations` is non-empty, fan out expert work
- if `pending_delegations` is empty, the file must already be complete
- `file_fully_analyzed=false` with no pending delegations is invalid and raises immediately

## Guard Rails

- the supervisor decision is validated after every model call
- duplicate-only delegation batches fail fast
- repeated same-file passes are logged through `supervisor_pass`
