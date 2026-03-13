from scout_agent.runtime.extract.reporting import ExtractProgressReporter
from dataclasses import dataclass
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scout_agent.app.settings import ExtractSettings
from scout_agent.domain.facts import FunctionSummary


@dataclass(frozen=True, slots=True)
class ExtractContext:
    settings: ExtractSettings
    reporter: ExtractProgressReporter


@dataclass(frozen=True, slots=True)
class ExtractFactsPipelineResult:
    project_root: Path
    facts_root: Path
    file_count: int
    function_count: int

    @property
    def facts_path(self) -> Path:
        return self.facts_root


@dataclass(frozen=True, slots=True)
class FileExtractionFailure:
    relative_path: str
    error_type: str
    message: str


class RetryableExtractionError(ValueError):
    """Raised for malformed extraction outputs that are safe to retry."""


class ExtractedFunctionSummary(BaseModel):
    function_key: str = Field(min_length=1)
    summary: FunctionSummary


class FileFactsExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    functions: list[ExtractedFunctionSummary] = Field(default_factory=list)

    @field_validator("functions")
    @classmethod
    def _function_keys_unique(
        cls,
        value: list[ExtractedFunctionSummary],
    ) -> list[ExtractedFunctionSummary]:
        seen: set[str] = set()
        duplicates: list[str] = []

        for item in value:
            if item.function_key in seen:
                duplicates.append(item.function_key)
            seen.add(item.function_key)

        if duplicates:
            duplicate_list = ", ".join(sorted(set(duplicates)))
            raise ValueError(
                f"Duplicate function_key values in extraction response: {duplicate_list}"
            )

        return value


class ExtractFactsParallelError(ValueError):
    def __init__(self, failures: list[FileExtractionFailure]) -> None:
        self.failures = failures
        super().__init__(self._render_message(failures))

    @staticmethod
    def _render_message(failures: list[FileExtractionFailure]) -> str:
        lines = [f"extract-facts failed for {len(failures)} file(s):"]
        for failure in failures:
            lines.append(
                f"- {failure.relative_path}: {failure.error_type}: {failure.message}"
            )
        return "\n".join(lines)
