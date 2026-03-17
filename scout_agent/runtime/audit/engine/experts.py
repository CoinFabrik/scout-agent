from langchain.agents.structured_output import ProviderStrategy

from dataclasses import dataclass
from pathlib import Path

from deepagents import CompiledSubAgent
from langchain.agents import create_agent

from scout_agent.domain.audit import ExpertResult
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.engine.tools import build_readonly_tools
from scout_agent.runtime.audit.prompts.audit_prompts import build_expert_system_prompt


@dataclass(frozen=True, slots=True)
class SubagentPromptSpec:
    name: str
    description: str


SUBAGENT_MANIFEST: tuple[SubagentPromptSpec, ...] = (
    SubagentPromptSpec(
        name="collection_validation",
        description="Audit vector or array inputs for missing uniqueness or duplicate-safe validation.",
    ),
    SubagentPromptSpec(
        name="time_state",
        description="Audit time-dependent state transitions and ordering.",
    ),
    SubagentPromptSpec(
        name="sentinel_logic",
        description="Audit sentinel and special-status value handling.",
    ),
)


def build_expert_subagents(
    *,
    model_name: str,
    llm_mode: str,
    project_root: Path,
    allowed_paths: list[str],
    recursion_limit: int,
    agent_read_limit: int,
    extra_prompt: str | None = None,
) -> list[CompiledSubAgent]:
    model = build_chat_model(model_name, llm_mode)
    subagents: list[CompiledSubAgent] = []

    for spec in SUBAGENT_MANIFEST:
        full_prompt = build_expert_system_prompt(
            expert_name=spec.name,
            extra_prompt=extra_prompt,
        )

        # Escape braces for LangChain prompt template interpolation
        escaped_system_prompt = full_prompt.replace("{", "{{").replace("}", "}}")
        runnable = create_agent(
            model=model,
            system_prompt=escaped_system_prompt,
            response_format=ProviderStrategy(ExpertResult, strict=True),
            tools=build_readonly_tools(
                root_dir=project_root,
                scope_path=project_root,
                agent_read_limit=agent_read_limit,
            ),
            name=spec.name,
        ).with_config({"recursion_limit": recursion_limit})
        subagents.append(
            {
                "name": spec.name,
                "description": spec.description,
                "runnable": runnable,
            }
        )

    return subagents
