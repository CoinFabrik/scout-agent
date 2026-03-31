# **Scout AI: 2026-Q1 Results Document**

## **Executive Summary**

This document outlines the results and findings of the Scout AI Proof of Concept (POC) for the first quarter of 2026. Following the fourth quarter of 2025, which demonstrated that structured prompting could detect known vulnerabilities, we identified context dilution as a critical barrier when scaling to full repository audits. To solve context saturation, this Q1 POC pivots to a research and implementation phase leveraging multi agent workflows and chained API calls. This report evaluates the efficacy, scalability, and performance of this agentic architecture against both an initial and an expanded set of Soroban smart contracts.

## **Introduction**

Our previous research established that non EVM ecosystems like Soroban suffer from a data moat. Without the massive datasets of documented hacks available to Ethereum, standard model fine tuning is unviable for Stellar.

During the Q4 2025 grant, we successfully engineered Scout AI, a tool for structured prompting, and proved that abstract prompts crafted by auditors can detect confirmed vulnerabilities when evaluating isolated files. However, experimental results showed that simply feeding more files into a large context window is insufficient for consistent detection. When exposed to a full repository, models suffered from context dilution and lost track of specific variable constraints amidst the noise.

To fulfill our Q1 2026 objectives, we pivoted to a research and implementation phase designed specifically to solve this context saturation. This document outlines the design and benchmark results of an advanced proof of concept leveraging multi agent workflows and chained API calls. By intelligently narrowing the scope of analysis before the final vulnerability assessment, this architecture aims to improve recall and eliminate the noise issues observed in previous iterations.

## **Methodology and POC Design**

To maximize the signal to noise ratio and maintain persistence of reasoning, the Q1 POC departs from monolithic prompts and implements Scout Agent, an autonomous multi agent auditing system built on the LangGraph framework. The architecture operates in two sequential phases to optimize token usage and context relevance.

### **Phase 1: Fact Extraction**
To prevent the agents from being overwhelmed by reading every line of a repository, the system first builds a lightweight semantic index. This is achieved via a parser aware of Rust syntax that traverses the project to identify functions, signatures, and source locations. An LLM assisted extractor then reads these specific functions to generate a structured summary of critical data, such as authorization checks, storage mutations, and sentinel value usage. These summaries are persisted as `FACTS.yml` files to provide the downstream agents with a detailed map of the codebase.

### **Phase 2: Multi Agent Audit**
The audit phase orchestrates a team of specialized LLM agents using a fan out and fan in state machine. A dispatch node routes work to parallel supervisor agents built via the `deepagents` wrapper. Each supervisor is responsible for a single file and is strictly capped on its `read_file` tool to between 100 and 500 lines to prevent context window overflow. These supervisors delegate specific vulnerability checks to specialized experts, such as the subagents for collection validation or sentinel logic. Simultaneously, an Execution Path Consistency (EPC) agent utilizes the extracted facts to trace and audit cross file state mutations, ensuring repository wide consistency without the overhead of massive file reads.

## **Test Datasets**

To accurately benchmark the performance of the new architecture and address feedback from the previous grant review, we divided our evaluation into two distinct contract corpuses.

### **Initial Set of Contracts**
This dataset consists of smart contracts selected during the Q4 2025 grant to validate the core capabilities of the POC. The test cases include confirmed vulnerabilities found in past professional audit reports, allowing us to verify if the POC can successfully reproduce relevant findings. The primary reference for this set is the [2025-02 Blend audit](https://github.com/code-423n4/2025-02-blend).

### **Expanded Set of Contracts**
The expanded dataset is a broader and independent corpus used to evaluate the generalization and robustness of the Scout Agent prompts. Testing against issues that the prompts were not explicitly crafted for is critical to mitigate the risk of overfitting. This evaluation proves whether the AI can generalize its reasoning to unseen implementations of our four target vulnerability categories. The full list can be found in the [extended contract set documentation](https://github.com/CoinFabrik/scout-agent/blob/main/docs/extended_contract_set.md).

## **Quantitative Metrics**

### **Evaluation Scope and Criteria**
To accurately measure the performance of the multi agent architecture, the Scout Agent POC was strictly scoped to detect four specific vulnerability categories: **Collection Validation** for missing uniqueness checks, **Time State** for improper ordering logic, **Sentinel Logic** for the mishandling of constants like `u32::MAX`, and **Execution Path Consistency** for inconsistent cross file validation.

The quantitative metrics are defined strictly within these targeted categories to ensure a fair assessment. **Precision** measures the percentage of AI reported vulnerabilities that are true positives confirmed by the audit report, while **Recall** measures the percentage of in scope vulnerabilities from the audit report that the AI successfully detected. While the ground truth reports contain a wider variety of findings, these out of scope issues were intentionally filtered out of the baseline prior to evaluation.

### **Initial Dataset Results**

**Note on Model Selection:** These benchmarks compare OpenAI and Google models. Anthropic's `claude-opus-4-6` was excluded due to fatal JSON response parsing errors intrinsic to the updated tool use schemas of the model, which resulted in zero recorded findings despite internal reasoning.

| Metric | Q4 2025 POC GPT (Single Prompt) | Q1 2026 POC GPT (Multi Agent) | Delta |
| :---- | :---- | :---- | :---- |
| **Precision** | 20% | 30% | \+10% |
| **Recall** | 10% | 25% | \+15% |

| Metric | Q4 2025 POC Gemini (Single Prompt) | Q1 2026 POC Gemini (Multi Agent) | Delta |
| :---- | :---- | :---- | :---- |
| **Precision** | 0% | 46% | \+46% |
| **Recall** | 0% | 40% | \+40% |

### **Expanded Dataset Results**

#### **Reflector**

| Metric | GPT 5.4 | Gemini Pro 3.1 |
| :---- | :---- | :---- |
| **Precision** | 33% | 27% |
| **Recall** | 17% | 33% |

#### **Trustless**

| Metric | GPT 5.4 | Gemini Pro 3.1 |
| :---- | :---- | :---- |
| **Precision** | 0% | 50% |
| **Recall** | 0% | 11% |

#### **Duplicate Entries in Vector**

| Metric | GPT 5.4 | Gemini Pro 3.1 |
| :---- | :---- | :---- |
| **Precision** | 100% | 100% |
| **Recall** | 50% | 100% |

## **Performance and Throughput**

The transition to a multi agent architecture introduces a significant trade off between performance and runtime. While multi agent workflows effectively reduce token limits per call and solve context saturation, they substantially increase the total number of API calls. For the Initial Set of 31,475 lines of code, the average runtime per audit was 1 hour, 8 minutes, and 5 seconds. In contrast, the smaller Expanded Set of 2,086 lines averaged 9 minutes and 27 seconds.

## **Qualitative Analysis**

### **Agent Navigation and Reasoning**
The Expert Subagents demonstrated strong proficiency in basic repository navigation by utilizing search tools effectively to locate state variables and function signatures. However, qualitative analysis of execution traces revealed a tendency to read peripheral files that lacked semantic relevance to the actual execution path. This suggests that while the phase for fact extraction provides a detailed map, agents still require improved heuristics to prioritize which files to investigate in detail.

### **Model Specific Parsing and Reliability**
A significant operational failure was observed with Anthropic's `claude-opus-4-6`. The multi agent workflow relies on strictly formatted JSON outputs to pass state between agents. This model frequently failed to correctly close JSON structures or adhere to the required schema, which caused the framework to drop the outputs entirely. This highlights a critical brittleness where the intelligence of an LLM is effectively bottlenecked by its adherence to strict output syntax.

### **Tool Call Loops and Configuration Trade Offs**
The use of search tools introduced a new behavioral failure mode involving infinite tool call loops where an agent repeatedly issues the same search command despite receiving unhelpful results. Testing revealed a direct correlation between the temperature parameter of the model and the frequency of these loops. Low temperature and deterministic presets intended to minimize hallucinations caused agents to become rigidly anchored to failed search strategies. This indicates that mitigating hallucinations inadvertently increases the risk of navigation dead ends, which requires sophisticated fallback heuristics to allow agents to adapt their reasoning.

## **System Resilience and Failure Mitigation**

To manage the inherent unpredictability of LLMs, we implemented robust state management via dual layer checkpointing. Graph level checkpoints track file review progress to allow interrupted audits to resume, while agent level checkpoints maintain conversation memory. Additionally, a runtime monitor was developed to detect tool call loops and inject warning prompts that steer the agent away from repetitive actions. While this resolved the majority of loops, it underscores that multi agent workflows require highly sophisticated fallback mechanisms to handle localized failures gracefully.

## **Conclusion and Next Steps**

The Q1 POC successfully demonstrated that pivoting to a multi agent architecture intelligently narrows the scope of analysis and overcomes the context dilution observed in single prompt approaches. However, advancing this approach to production requires addressing several feasibility factors. Latency and cost remain primary concerns because multi agent workflows consume more tokens through repeated state passing and tool calls. Furthermore, enterprise readiness will depend on implementing adapters for local open weights models or zero retention APIs to ensure absolute code privacy, which is not currently possible with standard public endpoints.

Following the completion of the Q1 2026 grant, our next steps involve integrating these AI POCs into a unified product roadmap. This will focus on refining fallback heuristics, optimizing token throughput, and expanding the expertise of the agent to broader vulnerability categories.

Additionally, we will explore the viability of implementing iterative QA passes as a post-processing layer in the audit pipeline. Rather than relying on a single pass for final results, this approach would introduce sequential validation stages where independent agents re-evaluate previously identified findings. These QA agents would be tasked with:

* Verifying the correctness of detected vulnerabilities
* Eliminating false positives through adversarial review
* Re-ranking findings based on confidence and reproducibility

This multi-pass validation strategy aims to increase precision without significantly degrading recall, leveraging the strengths of agent specialization while mitigating noise introduced during initial detection. Early hypotheses suggest that structured QA loops could serve as a cost-effective alternative to increasing base model complexity, while improving trustworthiness for production-grade audits.
