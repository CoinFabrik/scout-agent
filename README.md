# Scout-Agent

Scout-Agent is a multi-agent auditor for Soroban smart contracts written in Rust. It runs in two phases:

1. `extract-facts`: scans in-scope production Rust files and writes structured per-file facts plus an aggregate `FACTS.yml`.
2. `audit`: runs a supervisor-worker audit over those facts, plus a repo-wide `execution_path_consistency` pass, and writes `REPORT.md`.

The current audit stack focuses on these patterns:

- `collection_validation`: duplicate-sensitive `Vec` or array-like inputs.
- `time_state`: time-dependent state transitions and ordering.
- `sentinel_logic`: special-value and sentinel handling.
- `execution_path_consistency`: cross-file validation consistency for state mutations.

## How It Works

### Phase 1: Fact Extraction

`extract-facts` discovers in-scope Rust files, strips test-only sections from the analysis view, and asks the model to summarize each function into a small structured schema:

- `authorization`
- `vector_params`
- `time_dependent`
- `sentinel_values`

The command writes one facts file per source file plus an aggregate `FACTS.yml`.

### Phase 2: Audit

`audit` loads the extracted facts, validates that they still match the current source hashes, and then runs:

- a per-file supervisor-worker audit
- a repo-wide `execution_path_consistency` audit in parallel

Top-level audit state is checkpointed in `.scout-ai/memory.sqlite`. Per-agent audit runs also use `.scout-ai/.audit_memory.sqlite`. Each run prints a thread ID; pass that ID to `--resume` to continue an interrupted audit.

## Scope Rules

By default, Scout-Agent walks the target project and audits production `.rs` files only.

- Included: non-test Rust files under the project root
- Excluded directories: `.git`, `.venv`, `build`, `dist`, `node_modules`, `target`
- Excluded test paths: anything under `tests/`, `tests.rs`, `test_*.rs`, `*_test.rs`

You can narrow scope with `scout.json` by listing specific Rust files or directories in `files`.

## Installation

Scout-Agent requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For local development:

```bash
pip install -e .[dev]
```

## Credentials And Defaults

`.env` files are loaded automatically at startup.

Set the provider key for the model you plan to use:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`

Optional default model:

- `SCOUT_MODEL=provider:model`

Model identifiers must use the `provider:model` format.

Examples:

- `openai:<model-name>`
- `anthropic:<model-name>`
- `gemini:<model-name>`

## Quick Start

Run against the included benchmark contract:

```bash
scout-agent extract-facts benchmark/example_contract --model openai:<model-name>
scout-agent audit benchmark/example_contract --model openai:<model-name>
```

Or run against your own Soroban project:

```bash
scout-agent extract-facts /path/to/project --model provider:model
scout-agent audit /path/to/project --model provider:model
```

## CLI

Common arguments for both commands:

- `project_root`: target Soroban project root
- `--model`: model identifier in `provider:model` form
- `--llm-mode`: `consistent` or `creative`

### `extract-facts`

```bash
scout-agent extract-facts /path/to/project \
  --model provider:model \
  --facts-path .scout-ai/facts \
  --max-parallel-files 4
```

Flags:

- `--facts-path`: output directory for per-file facts and aggregate `FACTS.yml`
  Default: `<project_root>/.scout-ai/facts`
- `--max-parallel-files`: extraction concurrency
  Default: `scout.json.max_parallel_files` or `4`

### `audit`

```bash
scout-agent audit /path/to/project \
  --model provider:model \
  --facts-path .scout-ai/facts \
  --report-path REPORT.md \
  --max-parallel-files 4 \
  --agent-read-limit 15 \
  --agent-grep-limit 15
```

Flags:

- `--facts-path`: facts directory to load
  Default: `<project_root>/.scout-ai/facts`
- `--report-path`: output path for `REPORT.md`
  Default: `<project_root>/REPORT.md`
- `--extra-prompt`: path to a `.txt` file appended to supervisor, expert, and `execution_path_consistency` prompts
- `--max-parallel-files`: file-level audit concurrency
  Default: `scout.json.max_parallel_files` or `4`
- `--agent-read-limit`: max unique files each agent may read; `0` disables the cap
  Default: `scout.json.agent_read_limit` or `15`
- `--agent-grep-limit`: max grep calls each agent may make; `0` disables the cap
  Default: `scout.json.agent_grep_limit` or `15`
- `--resume THREAD_ID`: resume a previous audit from its printed thread ID

## `scout.json`

Place `scout.json` at the target project root to set defaults:

```json
{
  "model": "openai:<model-name>",
  "mode": "consistent",
  "files": ["src", "contracts/pool/src/lib.rs"],
  "max_parallel_files": 4,
  "agent_read_limit": 15,
  "agent_grep_limit": 15
}
```

Supported keys currently used by the CLI:

- `model`
- `mode`
- `files`
- `max_parallel_files`
- `agent_read_limit`
- `agent_grep_limit`

## Outputs

### Facts Directory

Default location: `<project_root>/.scout-ai/facts`

Contents:

- aggregate facts: `FACTS.yml`
- per-file facts: `<relative/path/to/file.rs>.facts.yaml`

`audit` validates facts against current file hashes. If the code changed after extraction, rerun `extract-facts`.

### Report

Default location: `<project_root>/REPORT.md`

The report includes:

- metadata
- finding counts by severity
- verified findings
- partial-run failures, if any
- a coverage appendix listing reviewed files

### Checkpoints

Audit checkpoints live in:

- `<project_root>/.scout-ai/memory.sqlite` for top-level audit state and `--resume`
- `<project_root>/.scout-ai/.audit_memory.sqlite` for per-agent audit memory

Use the printed thread ID with `--resume` to continue an interrupted run.

## Repository Notes

- Package entrypoint: `scout_agent.app.main:main`
- Console script: `scout-agent`
- Prompt assets live under `scout_agent/runtime/audit/prompts/`
- Example outputs live under `docs/results/`
