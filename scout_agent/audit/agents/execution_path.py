from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage

from scout_agent.audit.graph.callbacks import RuntimeProgressHandler
from scout_agent.audit.graph.context import AuditContext
from scout_agent.audit.graph.memory import get_sqlite_saver
from scout_agent.audit.prompts.audit_prompts import (
    build_execution_path_consistency_audit_prompt,
    build_execution_path_consistency_system_prompt,
)
from scout_agent.audit.prompts.prompt_utils import escape_prompt_text
from scout_agent.audit.structured_output import parse_structured_audit_response
from scout_agent.audit.tools.readonly import build_readonly_tools
from scout_agent.domain.audit import FileAuditResponse
from scout_agent.llm.providers import build_chat_model


def run_execution_path_consistency_audit(
    *,
    runtime: AuditContext,
    outer_thread_id: str,
    generation: int,
) -> FileAuditResponse:
    model = build_chat_model(runtime.model_name, runtime.llm_mode)
    system_prompt = build_execution_path_consistency_system_prompt(
        aggregate_facts_document=runtime.aggregate_facts_document,
        agent_grep_limit=runtime.agent_grep_limit,
        extra_prompt=runtime.extra_prompt,
    )

    memory_dir = runtime.project_root / ".scout-ai"
    memory_dir.mkdir(parents=True, exist_ok=True)
    checkpointer = get_sqlite_saver(memory_dir / ".audit_memory.sqlite")

    agent = create_agent(
        model=model,
        system_prompt=escape_prompt_text(system_prompt),
        tools=build_readonly_tools(
            root_dir=runtime.project_root,
            scope_path=runtime.project_root,
            agent_read_limit=0,
            agent_grep_limit=runtime.agent_grep_limit,
        ),
        response_format=ProviderStrategy(FileAuditResponse, strict=True),
        name="execution_path_consistency",
        checkpointer=checkpointer,
    )

    callback_handler = RuntimeProgressHandler(
        reporter=runtime.reporter,
        expert_names=set(),
        current_file="repo",
        primary_actor_name="execution_path_consistency",
    )

    result = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    content=build_execution_path_consistency_audit_prompt(
                        extra_prompt=runtime.extra_prompt
                    )
                )
            ]
        },
        config={
            "callbacks": [callback_handler],
            "recursion_limit": runtime.recursion_limit,
            "configurable": {
                "thread_id": _build_execution_path_consistency_thread_id(
                    outer_thread_id=outer_thread_id,
                    generation=generation,
                )
            },
        },
    )
    return parse_structured_audit_response(
        result=result,
        actor_name="execution_path_consistency",
    )


def _build_execution_path_consistency_thread_id(
    *,
    outer_thread_id: str,
    generation: int,
) -> str:
    return f"{outer_thread_id}:execution_path_consistency:g{generation}"
