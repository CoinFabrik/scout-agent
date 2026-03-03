# Contracts

## Top-Level Files

- `FACTS.yaml`: serialized `FactsDocument`
- `REPORT.md`: final audit report

## FactsDocument

Key fields:

- `generated_at_utc`
- `project_root`
- `model`
- `llm_mode`
- `scope_fingerprint`
- `files`

Each file entry contains:

- `path`
- `content_sha256`
- `functions`

## AuditState

Core fields:

- `facts_index`
- `files_to_review`
- `current_file`
- `last_supervisor_decision`
- `pending_delegations`
- `completed_delegation_keys`
- `needs_info_notes`
- `finding_keys`
- `files_reviewed`
- `verified_findings`
- `expert_batch_items`

## SupervisorDecision

Strict runtime contract:

- complete file: `file_fully_analyzed=true` and `delegations=[]`
- incomplete file: `file_fully_analyzed=false` and `delegations` is non-empty

Anything else is invalid and must stop the audit.

## Reporter Protocols

Extraction reporter methods:

- `started`
- `file_started`
- `file_completed`
- `file_failed`
- `close`

Audit reporter methods:

- `started`
- `file_started`
- `supervisor_pass`
- `delegation_batch`
- `duplicate_delegations_filtered`
- `finding_verified`
- `file_completed`
- `close`
