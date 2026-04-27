# Repository Guidelines

## Project Overview

Scout-Agent is a Python CLI for auditing Soroban Rust smart contracts. The CLI has two main flows:

- `extract-facts`: discover in-scope Rust files, call an LLM, and write structured facts.
- `audit`: load extracted facts, run the supervisor/expert audit graph, and write a report.

Core package code lives in `scout_agent/`. The benchmark Soroban contract is in `benchmark/example_contract/`. Published example outputs and research docs live under `docs/`.

## Setup And Commands

- Install locally with dev tooling: `python -m pip install -e '.[dev]'`
- Run tests: `python -m pytest`
- Run the CLI after install: `scout-agent --help`
- Run without installing an entry point: `python -m scout_agent.cli.main --help`
- Extract facts: `scout-agent extract-facts /path/to/soroban-project --model provider:model`
- Audit: `scout-agent audit /path/to/soroban-project --model provider:model`

LLM-backed commands require the relevant API key in the environment: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `OPENAI_API_KEY`. `SCOUT_MODEL` can provide a default model.

## Code Map

- `scout_agent/app/`: CLI parsing, command handlers, console output, and settings resolution.
- `scout_agent/configuration/`: `scout.json`, LLM mode, and configuration validation.
- `scout_agent/domain/`: typed domain shapes for facts and audit state.
- `scout_agent/llm/`: model/provider resolution.
- `scout_agent/runtime/source/`: source discovery, filtering, and Rust parsing helpers.
- `scout_agent/runtime/extract/`: fact extraction pipeline and progress reporting.
- `scout_agent/runtime/audit/engine/`: LangGraph audit orchestration, expert agents, tools, memory, and callbacks.
- `scout_agent/runtime/audit/io/`: report writing and reporting helpers.
- `scout_agent/runtime/audit/prompts/`: packaged prompt templates and few-shot examples.

## Implementation Guidance

- Keep changes simple, explicit, and local to the relevant layer.
- Preserve the CLI contract in `scout_agent/app/cli.py` unless the user explicitly asks for a command-line change.
- Prefer existing dataclasses, typed dicts, settings resolvers, and domain helpers over new abstractions.
- Keep path handling based on `pathlib.Path`; validate user-controlled project paths before reading.
- For Rust source handling, use the existing discovery, filtering, and tree-sitter helpers instead of ad hoc parsing.
- Prompt changes are product behavior changes. Keep them narrow, and update nearby few-shot examples when needed.
- Treat `.scout-ai/`, generated `FACTS.yml`, generated `REPORT.md`, and LangGraph SQLite memory as run artifacts unless the task is specifically about checked-in example results under `docs/results/`.

## Testing Notes

There is currently no checked-in `tests/` directory, though `pyproject.toml` configures pytest. Add focused tests when changing pure Python behavior such as settings resolution, config validation, source filtering, path safety, fact aggregation, or report writing.

For LLM-backed flows, prefer unit tests around deterministic boundaries and small integration checks with mocked providers. Do not require live provider credentials in normal test runs.
