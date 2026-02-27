# **Architecture & Technical Specification: Scout-Agent**
**A Multi-Agent Soroban Smart Contract Auditor**

## **1. System Overview**
Scout-Agent is a specialized multi-agent system designed to autonomously audit Soroban smart contracts written in Rust. Built on a graph-based multi-agent framework (e.g., DeepAgent / LangGraph), it utilizes a **Supervisor-Worker architecture**. A central Supervisor explores the codebase and dynamically delegates deep, pattern-specific analysis to specialized Expert Subagents.

The system relies on a strongly-typed Graph State to orchestrate tasks and manage memory internally. Context and findings are passed dynamically between nodes by the framework.

## **2. Command Line Interface (CLI)**
The system is divided into two distinct execution phases, invoked via CLI:

1. **`scout-agent extract-facts <project_root>`**
   *   Executes a preliminary static and LLM-assisted analysis of the codebase.
   *   Generates a `FACTS.yaml` file containing structured observations about every function (Authorization, Vector Parameters, Time-Dependent State, Sentinel Values).

2. **`scout-agent audit <project_root>`**
   *   Initializes the Graph State, loads `FACTS.yaml` into the global prompt context, and starts the multi-agent execution graph.

## **3. Multi-Agent Architecture**

The system employs discrete agents with distinct system prompts, tools, and output schemas.

### **3.1 The Supervisor Agent**
*   **Role**: Codebase exploration, architectural understanding, and delegation. The Supervisor maps the codebase to find restricted operations and state mutations, acting as a dispatcher rather than a direct vulnerability auditor.
*   **Tools**:
    *   `supervisor_read_code`: Reads code chunks with a strict maximum of **500 lines** per call to efficiently scan large files.
    *   `search_code`: Recursive directory/file search.
*   **Output Schema**: Returns a `SupervisorDecision` Pydantic model.
    ```python
    class Delegation(BaseModel):
        expert_type: ExpertTypeEnum
        target_file: str
        context_snippet: str
        reasoning: str

    class SupervisorDecision(BaseModel):
        file_fully_analyzed: bool
        delegations: List[Delegation]
    ```

### **3.2 The Expert Subagents**
Expert subagents are spawned asynchronously and concurrently by the framework based on the Supervisor's delegations. They have hyper-focused prompts for a single vulnerability class.
*   **Tools**:
    *   `expert_read_code`: Reads targeted code chunks with a strict maximum of **100 lines** per call to enforce deep, focused reasoning.
    *   `search_code`: Critical for cross-file verifications like Mutation Backtracking.
*   **Output Schema**: Returns an `ExpertResult` Pydantic model.
    ```python
    class Finding(BaseModel):
        pattern: str
        severity: str # CRITICAL, HIGH, MEDIUM, LOW
        location: str
        description: str
        evidence: str

    class ExpertResult(BaseModel):
        status: str # VULNERABLE, SAFE, NEEDS_INFO
        finding: Optional[Finding]
    ```

**Expert Types & Triggers:**
1.  **Execution Path Consistency Expert**
    *   *Triggered when*: The Supervisor identifies an entry point or internal function that mutates contract storage.
    *   *Responsibility*: Performs **Mutation Backtracking**. Identifies the specific state variable being modified, searches the entire codebase for all other locations where that same state is mutated, and verifies that all paths enforce consistent validations (e.g., authorization, pause checks, thresholds).
2.  **Collection Validation Expert**
    *   *Triggered when*: The Supervisor sees public functions accepting `Vec` or array-like parameters.
    *   *Responsibility*: Verifies that the elements are explicitly checked for uniqueness before state insertion or mathematical aggregation to prevent calculation inflation (e.g., duplicate token IDs counting votes multiple times).
3.  **Time-State Expert**
    *   *Triggered when*: The Supervisor identifies timestamp reads (`e.ledger().timestamp()`) or time-based yield/accumulation logic.
    *   *Responsibility*: Verifies that state accruals are triggered strictly *before* the underlying state variables are modified.
4.  **Sentinel Logic Expert**
    *   *Triggered when*: The Supervisor observes constants like `u32::MAX`, `None`, or `0` used to represent special status flags.
    *   *Responsibility*: Traces the variable's usage to ensure readers and iterators explicitly handle the sentinel state safely.

## **4. State Management (The Graph State)**

The audit's lifecycle and memory are managed entirely in memory via the `AuditState`. This state acts as the source of truth for execution flow and findings.

### **4.1 Graph State Definition**
The State is a typed object maintained by the framework across node transitions.
```python
class AuditState(TypedDict):
    files_to_review: List[str]
    files_reviewed: Annotated[List[str], operator.add]
    verified_findings: Annotated[List[Finding], operator.add]
```

## **5. Execution Flow (The Graph)**

The system operates as a cyclical, state-driven graph:
1.  **Init Node**: Reads project structure, populates `files_to_review` in the Graph State, loads `FACTS.yaml`, and transitions to the Supervisor.
2.  **Supervisor Node**: Processes the top file in `files_to_review` using `supervisor_read_code`. Identifies vulnerability triggers and outputs a `SupervisorDecision`.
3.  **Router Node (Conditional)**:
    *   If `delegations` exist, spawns the necessary Expert Nodes in parallel.
    *   If no delegations exist, marks the file complete, removes it from `files_to_review`, and routes back to the Supervisor Node for the next file.
4.  **Expert Nodes (Parallel)**: Execute their specific deep-dive prompts using `expert_read_code` and `search_code`. Output `ExpertResult` objects.
5.  **Reducer Node**: Aggregates all `ExpertResult` outputs. Appends identified vulnerabilities to `verified_findings` in the Graph State. Routes back to the Supervisor Node.
6.  **Termination & Coverage Enforcement (Report Node)**: The graph attempts to route to the final Report Node when the Supervisor indicates it has finished.
    *   **The Coverage Constraint**: The framework algorithmically verifies that `files_to_review` is empty. The Supervisor is **strictly prohibited from terminating the audit** if there are remaining, unreviewed files.
    *   **Report Generation**: Only when the Coverage Constraint is satisfied and no delegations are active will the Report Node compile `verified_findings` into the final `REPORT.md` on disk and terminate execution.
