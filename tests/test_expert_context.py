from pathlib import Path

from scout_agent.domain.audit import Delegation, ExpertTypeEnum
from scout_agent.domain.facts import (
    AuthorizationFact,
    FileFacts,
    FunctionFactBundle,
    FunctionFacts,
    SentinelValuesFact,
    TimeDependentStateFact,
    VectorParametersFact,
)
from scout_agent.runtime.audit.expert_context import build_expert_execution_context


def test_expert_context_anchor_resolution_by_signature(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "gateway.rs").write_text(
        "pub fn settle(e: Env) {\n"
        "    let now = e.ledger().timestamp();\n"
        "    update(now);\n"
        "}\n",
        encoding="utf-8",
    )

    facts_index = {
        "contracts/gateway.rs": FileFacts(
            path="contracts/gateway.rs",
            content_sha256="deadbeef",
            functions=[
                FunctionFacts(
                    function_id="contracts/gateway.rs::settle#L1",
                    name="settle",
                    kind="function",
                    visibility="public",
                    line_start=1,
                    line_end=4,
                    signature="pub fn settle(e: Env)",
                    impl_target=None,
                    facts=FunctionFactBundle(
                        authorization=AuthorizationFact(
                            status="unknown",
                            reasoning="x",
                            evidence=[],
                        ),
                        vector_parameters=VectorParametersFact(
                            status="absent",
                            reasoning="x",
                            parameters=[],
                        ),
                        time_dependent_state=TimeDependentStateFact(
                            status="present",
                            reasoning="x",
                            evidence=["contracts/gateway.rs:2 e.ledger().timestamp()"],
                        ),
                        sentinel_values=SentinelValuesFact(
                            status="absent",
                            reasoning="x",
                            values=[],
                        ),
                    ),
                )
            ],
        )
    }

    delegation = Delegation(
        expert_type=ExpertTypeEnum.TIME_STATE,
        target_file="contracts/gateway.rs",
        context_snippet="pub fn settle(e: Env)",
        reasoning="Time sequencing may matter.",
    )

    context = build_expert_execution_context(
        project_root=tmp_path,
        facts_index=facts_index,
        delegation=delegation,
    )

    assert "pub fn settle(e: Env)" in context.code_snapshot
    assert "timestamp" in context.code_snapshot
    assert context.search_results is not None
    assert "contracts/gateway.rs:2:" in context.search_results


def test_expert_context_falls_back_to_raw_line_anchor(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "sentinel.rs").write_text(
        "const SENTINEL: u32 = u32::MAX;\n"
        "pub fn read_value() {\n"
        "    let _ = SENTINEL;\n"
        "}\n",
        encoding="utf-8",
    )

    facts_index = {
        "contracts/sentinel.rs": FileFacts(
            path="contracts/sentinel.rs",
            content_sha256="deadbeef",
            functions=[],
        )
    }

    delegation = Delegation(
        expert_type=ExpertTypeEnum.SENTINEL_LOGIC,
        target_file="contracts/sentinel.rs",
        context_snippet="u32::MAX",
        reasoning="Sentinel handling needs review.",
    )

    context = build_expert_execution_context(
        project_root=tmp_path,
        facts_index=facts_index,
        delegation=delegation,
    )

    assert "u32::MAX" in context.code_snapshot
    assert context.search_results is not None
    assert "contracts/sentinel.rs:1:" in context.search_results


def test_expert_context_falls_back_to_file_start_when_no_anchor_exists(
    tmp_path: Path,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    lines = [f"let line_{index} = {index};" for index in range(1, 61)]
    (contracts / "plain.rs").write_text("\n".join(lines) + "\n", encoding="utf-8")

    facts_index = {
        "contracts/plain.rs": FileFacts(
            path="contracts/plain.rs",
            content_sha256="deadbeef",
            functions=[],
        )
    }

    delegation = Delegation(
        expert_type=ExpertTypeEnum.COLLECTION_VALIDATION,
        target_file="contracts/plain.rs",
        context_snippet="this does not exist",
        reasoning="Need default file-start snapshot.",
    )

    context = build_expert_execution_context(
        project_root=tmp_path,
        facts_index=facts_index,
        delegation=delegation,
    )

    snapshot_lines = context.code_snapshot.splitlines()
    assert snapshot_lines[0].startswith("   1: let line_1 = 1;")
    assert snapshot_lines[-1].startswith("  60: let line_60 = 60;")
    assert context.search_results is None
