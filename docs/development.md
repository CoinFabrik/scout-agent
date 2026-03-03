# Development

## Setup

Install the package in editable mode:

```bash
pip install -e .
```

For test work, install the development extras as well:

```bash
pip install -e .[dev]
```

## Local Workflow

Typical loop:

1. run `scout-agent extract-facts <project_root>`
2. inspect `FACTS.yaml`
3. run `scout-agent audit <project_root>`
4. inspect `REPORT.md`

## Useful Commands

Syntax check:

```bash
python3 -m py_compile $(rg --files scout_agent tests -g '*.py')
```

Tests:

```bash
python3 -m pytest
```

Text search:

```bash
rg "pattern" scout_agent tests docs
```

## Runtime Notes

Extraction is in-memory for a single invocation. Audit also runs as a single invocation. There is no hidden run registry, no interactive selection step, and no alternate console mode.

## What To Change Where

- command wiring: [`scout_agent/app/`](/Users/josegarcia/Desktop/scout-agent/scout_agent/app)
- configuration: [`scout_agent/configuration/`](/Users/josegarcia/Desktop/scout-agent/scout_agent/configuration)
- extraction runtime: [`scout_agent/runtime/extract/`](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/extract)
- audit runtime: [`scout_agent/runtime/audit/`](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/audit)
- source discovery and parsing: [`scout_agent/runtime/source/`](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/source)
