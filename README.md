# Scout-Agent

Scout-Agent is a two-step CLI for Soroban smart contract review:

1. `extract-facts` scans in-scope Rust files and writes `FACTS.yaml`.
2. `audit` reads `FACTS.yaml`, runs the supervisor/expert graph, and writes `REPORT.md`.

The product is intentionally simple:

- one run per command
- plain text console output
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

Optional flags:

- `--llm-mode`
- `--facts-path`
- `--report-path`
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

Console output is line-based and always plain text. Typical audit events include:

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
