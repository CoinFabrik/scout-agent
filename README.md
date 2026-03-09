# Scout-Agent

Scout-Agent is a two-step CLI for Soroban smart contract review:

1. `extract-facts` scans in-scope Rust files and writes `FACTS.yaml`.
2. `audit` reads `FACTS.yaml`, runs the supervisor/expert graph, and writes `REPORT.md`.

The product is intentionally simple:

- one run per command
- minimal interactive audit progress UI
- no hidden run state
- no interactive resume flow

## Install

```bash
pip install -e .
```

Environment variables:

- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `SCOUT_MODEL` as an optional default model

## Commands

Generate facts:

```bash
scout-agent extract-facts /path/to/project --model anthropic:claude-sonnet-4-5
```

Run the audit:

```bash
scout-agent audit /path/to/project --model anthropic:claude-sonnet-4-5
```

Render readable Markdown for an existing dump:

```bash
scout-agent render-dump /path/to/project/.scout-ai/audit-dumps/<run-id>
```

Optional flags:

- `--llm-mode`
- `--facts-path`
- `--report-path`
- `--dump-runtime` to write incremental debug artifacts under `.scout-ai/audit-dumps/`
- `--ui plain` to disable the audit TUI
- `--max-parallel-files` for `extract-facts`

`scout.json` can provide defaults for:

- `model`
- `mode`
- `files`
- `max_parallel_files`

## Outputs

`extract-facts` writes:

- `FACTS.yaml`

`audit` writes:

- `REPORT.md`
- `.scout-ai/audit-dumps/<run-id>/` when `--dump-runtime` is enabled
- `index.md` and per-file `timeline.md` inside the dump when rendered or during live dump generation

`audit` uses a minimal full-screen TUI by default on interactive terminals and
falls back to plain line-based output on non-TTY stdout. Use `--ui plain` to
force line-based output.

Typical audit events include:

- audit start
- current file
- supervisor pass count
- delegation batch count
- verified findings
- file completion

## Runtime Shape

Extraction:

- discover production Rust files
- parse and filter source
- extract file facts in bounded parallelism
- write one `FactsDocument`

Audit:

- initialize `AuditState` from `FACTS.yaml`
- run the LangGraph supervisor/expert flow
- enforce the strict supervisor contract
- write one markdown report

## Development

Useful checks:

```bash
python3 -m py_compile $(rg --files scout_agent tests -g '*.py')
python3 -m pytest
```

The repo docs under [`docs/`](/Users/josegarcia/Desktop/scout-agent/docs) describe the smaller runtime and file layout.
