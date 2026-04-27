from scout_agent.domain.audit import FileAuditResponse


def parse_structured_audit_response(
    *,
    result: dict[str, object],
    actor_name: str,
) -> FileAuditResponse:
    structured = result.get("structured_response")
    if structured is None:
        raise ValueError(f"Structured response missing for {actor_name}.")

    try:
        return (
            structured
            if isinstance(structured, FileAuditResponse)
            else FileAuditResponse.model_validate(structured)
        )
    except ValueError as exc:
        raise ValueError(
            f"Structured response failed validation for {actor_name}: {exc}"
        ) from exc
