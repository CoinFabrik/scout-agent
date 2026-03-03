# Improving LLM Effectiveness in Long-Context Workflows

## 1. Problem Statement: Reduced Effectiveness in Long Contexts

Although modern Large Language Models (LLMs) technically support large context
windows, [research](https://arxiv.org/pdf/2307.03172) and empirical evidence
show that model performance degrades as context length increases.

Key contributing factors include:

- **Reasoning Overload**: More tokens increase the cognitive load required for
  inference.

- **Position Bias**:
  - **Primacy bias**: higher effectiveness when relevant information appears
    early.
  - **Recency bias**: higher effectiveness when relevant information appears
    late.

- **“Lost in the Middle” Effect**: Accuracy drops when key information appears
  mid-context.

- **Information Dilution**: Irrelevant data reduces the signal-to-noise ratio.

![Accuracy vs LLMs](survey_image_1.png)

### Long-Context Failure Modes

```mermaid
flowchart LR
    A[Large Context Window] --> B[More Tokens to Reason Over]
    B --> C[Reasoning Overload]
    B --> D[Information Dilution]
    A --> E[Position Bias]
    E --> F[Primacy Bias]
    E --> G[Recency Bias]
    C --> I[Reduced Accuracy]
    D --> I
    F --> I
    G --> I
```

## 2. Core Design Principle: Signal-to-Noise Ratio

Effective long-context systems should prioritize:

- **Maximizing Signal-to-Noise Ratio (SNR)** in each API call.
- **Maintaining Persistence of Reasoning** across multiple calls.

Rather than sending large raw contexts, the system must actively select and
structure information.

```mermaid
flowchart TB
    A[Raw Repository / Data] --> B[Context Selection Layer]
    B --> C[High-Signal Context]
    B --> D[Noise Pruned]
    C --> E[LLM API Call]
    E --> F[Reasoned Output]
```

## 3. Context Construction Strategies

### 3.1 File-by-File Analysis

**Approach** One API call per file in the repository.

**Pros**

- Simple to implement.
- Minimal engineering overhead.

**Cons**

- Incomplete context.
- Cross-file dependencies are missing.
- Limited global reasoning.

```mermaid
flowchart LR
    F1[file1] --> LLM1[LLM Call]
    F2[file2] --> LLM2[LLM Call]
    F3[file3] --> LLM3[LLM Call]
```

### 3.2 Entry Point Analysis (Internal Call Expansion)

**Approach** Use libraries like
[tree-sitter](https://tree-sitter.github.io/tree-sitter/) to start from an entry
point (e.g., public function, CLI command) and include all internally called
functions and definitions in a single context.

**Challenges**

- Dynamic dispatch and polymorphism are difficult to resolve statically.
- Context size can grow quickly in large codebases.

```mermaid
flowchart LR
    C1[Called Function A]
    C2[Called Function B]
    C3[Called Function C]
    LLM[Single LLM Call]
    EP[Entry Point]
    C1 --> EP
    C2 --> EP
    C3 --> C2
    EP --> LLM
```

### 3.3 Autonomous Agents & Agent–Computer Interfaces (ACI)

**Approach** Instead of sending large static contexts, an autonomous agent
interacts with the filesystem using LM-friendly commands.

![ACI](survey_image_2.png)

This mirrors how a human auditor works: explore, inspect, reason, and iterate.

**SWE-agent Insights**: Documentation from the
[SWE-agent project](https://arxiv.org/pdf/2405.15793) provides key
implementation details to save development time:

- Limit file views to optimal line ranges.
- Prune success/error messages aggressively.
- Prefer many small, focused calls over a few large ones.

**Recommended Auditing Commands**

- get_entry_points()
- read_code(fn_name)
- get_callers(fn_name)
- find_usage(variable_name)

```mermaid
sequenceDiagram
    participant Agent
    participant FS as Filesystem
    participant LLM

    Agent->>LLM: Start audit
    LLM-->>Agent: List entry points

    Agent->>FS: get_entry_points()
    FS-->>Agent: Entry points list

    Agent->>LLM: Entry points list
    LLM-->>Agent: Read fileA

    Agent->>FS: read_code(fileA)
    FS-->>Agent: Code snippet

    Agent->>LLM: Analyze code
    LLM-->>Agent: Findings

    Agent->>LLM: Continue analysis
```

## 4. Graph-Based Repository Understanding

### 4.1 Repository Graphs

**[RepoGraph](https://arxiv.org/pdf/2410.14684)-style approaches** model the
repository as a dependency graph, preventing agents from getting stuck in local
contexts and improving cross-file reasoning compared to standard RAG.

```mermaid
graph TD
    A[File A] -->|calls| B[File B]
    B -->|calls| C[File C]
    A -->|imports| D[File D]
    D -->|uses| C
```

### 4.2 [Code Property Graphs (CPG)](https://arxiv.org/pdf/2507.16585)

**Approach** Use tools like Joern to extract vulnerability-relevant slices of
code.

**Benefits**

- Reduces effective code size by up to ~90%.
- Preserves semantic and security-critical context.

**Limitations**

- Requires DSL-aware or fine-tuned models.

```mermaid
flowchart LR
    A[Full Codebase] --> B[CPG Extraction]
    B --> C[Vulnerability-Relevant Nodes]
    C --> D[Code Slice]
    D --> E[LLM Analysis]
```

## 5. Optimization & Reasoning Persistence

### 5.1 [KV-Cache](https://medium.com/@joaolages/kv-caching-explained-276520203249) Optimization

To reduce Time-to-First-Token (TTFT) and inference costs:

- Keep prompt prefixes stable.
- Ensure context is append-only.

![KV-Cache Design](survey_image_3.png)

```mermaid
flowchart TB
    A[Stable Prompt Prefix] --> B[KV Cache Reuse]
    B --> C[Lower TTFT]
    C --> D[Lower Cost]
```

### 5.2 Context Engineering Techniques

**History Management**

- Maintain full logical history.
- Collapse or summarize older turns.
- Keep only the last N agent responses in full.

**Filesystem-Based Memory**

- Store large or compressed artifacts in files.
- Reference them by path in the prompt.
- Avoid lossy summarization.

**Recitation
([Manus Method](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus))**

- Continuously rewrite a concise “to-do list” at the end of the context.
- Push global objectives into the model’s recency window.
- Prevent task drift.

```mermaid
flowchart LR
    A[Global Objectives] --> B[Recited To-Do List]
    B --> C[Recency Bias Alignment]
    C --> D[Improved Task Focus]
```

## 6. Summary

Long-context failures are a structural limitation of current LLMs, not simply a
matter of context window size.

Systems that combine:

- Context pruning,
- Agent-based exploration,
- Graph-based representations,
- KV-cache–aware prompting,
- And recency-aligned objectives,

can achieve significantly higher accuracy, lower cost, and better alignment in
complex auditing and code analysis workflows.
