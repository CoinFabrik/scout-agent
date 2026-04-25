from scout_agent.domain.audit import AuditState
from scout_agent.audit.io.reporting import AuditProgressReporter
from dataclasses import dataclass
from scout_agent.domain.facts import AggregateFactsDocument
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_root: Path
    facts_path: Path
    report_path: Path
    aggregate_facts_document: AggregateFactsDocument
    model_name: str
    llm_mode: str
    max_parallel_files: int
    recursion_limit: int
    agent_read_limit: int
    agent_grep_limit: int
    reporter: AuditProgressReporter
    initial_state: AuditState
    extra_prompt: str | None = None
    thread_id: str | None = None
