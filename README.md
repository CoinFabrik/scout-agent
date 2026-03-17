# Scout-Agent

Scout-Agent is a multi-agent AI auditor designed for [Soroban](https://soroban.stellar.org/) smart contracts. It uses a specialized **Supervisor-Expert** architecture to identify security vulnerabilities and logic flaws in Rust-based smart contracts.

The agent operates in a two-step process to ensure depth and accuracy:
1. **Fact Extraction:** Scans the codebase to build a semantic map of functions, state dependencies, and potential risk areas.
2. **Specialized Audit:** A Supervisor agent coordinates multiple Expert agents to deep-dive into specific vulnerability patterns.

## Architecture

Scout-Agent is built on a "Specialist" model rather than a general-purpose auditor:

- **Supervisor:** Routes investigation tasks to experts based on identified triggers.
- **Expert: Collection Validation:** Focuses on input `Vec`/`Map` usage in loops or calculations without uniqueness checks.
- **Expert: Time & State:** Analyzes state updates that depend on ledger time or sequence.
- **Expert: Sentinel Logic:** Detects inconsistent handling of special values (e.g., `0`, `u32::MAX`) used to represent uninitialized states.

## Installation

```bash
# Install from source
pip install -e .
```

### Environment Variables

You must provide an API key for the model provider you intend to use:

- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `SCOUT_MODEL` (Optional: Default model identifier)

## Usage

### 1. Extract Facts
First, scan the project to generate a `FACTS.yml` file. This provides the context needed for the audit phase.

```bash
scout-agent extract-facts /path/to/soroban-project --model anthropic:claude-3-7-sonnet-20250219
```

**Key Flags:**
- `--model`: (Required) Model identifier in `provider:model_name` format.
- `--facts-path`: Custom output directory for facts (defaults to `<project_root>/.scout-ai/facts`).
- `--max-parallel-files`: Number of files to process concurrently.

### 2. Run Audit
Execute the multi-agent audit using the extracted facts.

```bash
scout-agent audit /path/to/soroban-project --model provider:model_name
```

**Key Flags:**
- `--ui [tui|plain]`: Toggle between the interactive Terminal UI (default) and plain text output.
- `--report-path`: Custom path for the final `REPORT.md`.

### Configuration (`scout.json`)
You can place a `scout.json` file in your project root to define persistent defaults:

```json
{
  "model": "anthropic:claude-3-7-sonnet-20250219",
  "mode": "consistent",
  "max_parallel_files": 4,
  "files": ["src/lib.rs", "src/token.rs"]
}
```

## Outputs

- **`FACTS.yml`**: A structured summary of the contract's functions, including notes on authorization, collection usage, and time dependencies.
- **`REPORT.md`**: The final audit report containing verified findings with severity, location, and evidence.

For more details on the runtime and prompt engineering, see the [docs/](docs/) directory.
