from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workforce_runtime.v2.governance import ChangeValidator
from workforce_runtime.v2.models import Finding, Metric, OrganizationChangeProposal, OrganizationSnapshot


class LLMGovernorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: str
    root_cause_hypotheses: list[str] = Field(default_factory=list)
    missing_evidence_requests: list[str] = Field(default_factory=list)
    candidate_proposals: list[OrganizationChangeProposal] = Field(default_factory=list)
    expected_effects: dict[str, float | int | str] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    suggested_evaluation_metrics: list[str] = Field(default_factory=list)


class LLMGovernorError(RuntimeError):
    def __init__(self, message: str, *, attempts: int, errors: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.errors = errors


class StructuredLLMGovernor:
    """LLM-governor adapter with structured-output validation and no mutation access."""

    def __init__(
        self,
        provider: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        validator: ChangeValidator | None = None,
        max_attempts: int = 2,
    ) -> None:
        self.provider = provider
        self.validator = validator or ChangeValidator()
        self.max_attempts = max(max_attempts, 1)
        self.last_errors: list[str] = []
        self.attempt_count = 0

    def propose_changes(
        self,
        *,
        snapshot: OrganizationSnapshot,
        metrics: list[Metric],
        findings: list[Finding],
    ) -> LLMGovernorResponse:
        context = self._build_context(snapshot=snapshot, metrics=metrics, findings=findings)
        errors: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            self.attempt_count = attempt
            try:
                response = LLMGovernorResponse.model_validate(self.provider(context | {"attempt": attempt, "prior_errors": errors}))
            except ValidationError as exc:
                errors.append(f"attempt {attempt}: invalid response schema: {exc}")
                continue
            validation_errors = self._validate_response(response, snapshot=snapshot, findings=findings)
            if validation_errors:
                errors.extend(f"attempt {attempt}: {error}" for error in validation_errors)
                continue
            self.last_errors = []
            return response
        self.last_errors = errors
        raise LLMGovernorError("LLM governor failed to produce valid proposals", attempts=self.attempt_count, errors=errors)

    def _build_context(
        self,
        *,
        snapshot: OrganizationSnapshot,
        metrics: list[Metric],
        findings: list[Finding],
    ) -> dict[str, Any]:
        return {
            "organization": snapshot.state.organization.model_dump(mode="json"),
            "snapshot": {
                "id": snapshot.id,
                "positions": {
                    position_id: {
                        "title": position.title,
                        "reports_to_position_id": position.reports_to_position_id,
                        "responsibilities": position.responsibilities,
                    }
                    for position_id, position in snapshot.state.positions.items()
                },
                "policies": snapshot.state.policies,
            },
            "findings": [finding.model_dump(mode="json") for finding in findings],
            "metrics": [
                {
                    "name": metric.name,
                    "position_id": metric.position_id,
                    "value": metric.value,
                    "unit": metric.unit,
                    "sample_size": metric.sample_size,
                    "confidence": metric.confidence,
                }
                for metric in metrics
            ],
            "constraints": {
                "full_event_history_included": False,
                "governor_can_mutate_state": False,
            },
        }

    def _validate_response(
        self,
        response: LLMGovernorResponse,
        *,
        snapshot: OrganizationSnapshot,
        findings: list[Finding],
    ) -> list[str]:
        errors: list[str] = []
        finding_ids = {finding.id for finding in findings}
        position_ids = set(snapshot.state.positions)
        for proposal in response.candidate_proposals:
            hallucinated_findings = [finding_id for finding_id in proposal.finding_ids if finding_id not in finding_ids]
            if hallucinated_findings:
                errors.append(f"proposal {proposal.id} references unknown findings: {hallucinated_findings}")
            hallucinated_positions = [position_id for position_id in proposal.affected_positions if position_id not in position_ids]
            if hallucinated_positions:
                errors.append(f"proposal {proposal.id} references unknown positions: {hallucinated_positions}")
            if not proposal.finding_ids and proposal.rationale:
                proposal.metadata["unsupported_claims"] = list(proposal.rationale)
            validation = self.validator.validate(state=snapshot.state, proposal=proposal)
            if not validation.ok:
                errors.extend(f"proposal {proposal.id}: {error}" for error in validation.errors)
        if len(response.candidate_proposals) < 2:
            errors.append("LLM governor must return at least two alternative proposals")
        return errors
