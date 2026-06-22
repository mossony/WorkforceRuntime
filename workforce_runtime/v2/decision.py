from __future__ import annotations

from typing import Literal, cast

from workforce_runtime.v2.models import (
    Decision,
    DecisionOption,
    ExpectedOutcome,
    ObservedOutcome,
    OrganizationChangeProposal,
    SimulationResult,
    utc_now,
)


class DecisionLedger:
    def create_for_proposals(
        self,
        *,
        decision_id: str,
        organization_id: str,
        project_id: str | None,
        owner_position_id: str,
        question: str,
        proposals: list[OrganizationChangeProposal],
        simulations: list[SimulationResult],
    ) -> Decision:
        evidence_ids = [result.id for result in simulations]
        options = [
            DecisionOption(id=f"option_{proposal.id}", description=f"Apply {proposal.id}", proposal_id=proposal.id)
            for proposal in proposals
        ]
        options.append(DecisionOption(id="option_no_change", description="Keep current organization unchanged"))
        return Decision(
            id=decision_id,
            organization_id=organization_id,
            project_id=project_id,
            question=question,
            owner_position_id=owner_position_id,
            status="awaiting_decision",
            options=options,
            evidence_ids=evidence_ids,
            participants=[proposal.proposer_id for proposal in proposals],
            expected_outcomes=[
                ExpectedOutcome(metric_name="median_approval_latency", expected_value=0.0, comparator="lt"),
            ],
            assumptions={"simulations_are_directional": "unknown"},
            revisit_conditions=["target metric worsens", "guardrail metric exceeds rollback threshold"],
        )

    def select_option(
        self,
        decision: Decision,
        *,
        option_id: str,
        rationale: list[str],
    ) -> Decision:
        if option_id not in {option.id for option in decision.options}:
            raise ValueError(f"unknown decision option: {option_id}")
        return decision.model_copy(
            update={
                "status": "decided",
                "selected_option_id": option_id,
                "rationale": rationale,
                "decided_at": utc_now(),
            }
        )

    def attach_evidence(self, decision: Decision, evidence_id: str) -> Decision:
        if evidence_id in decision.evidence_ids:
            return decision
        return decision.model_copy(update={"evidence_ids": [*decision.evidence_ids, evidence_id]})

    def register_participant(self, decision: Decision, position_id: str) -> Decision:
        if position_id in decision.participants:
            return decision
        return decision.model_copy(update={"participants": [*decision.participants, position_id]})

    def register_dissent(self, decision: Decision, *, position_id: str, argument: str) -> Decision:
        dissent = dict(decision.dissent)
        dissent[position_id] = argument
        return decision.model_copy(update={"dissent": dissent})

    def mark_assumption(
        self,
        decision: Decision,
        *,
        assumption: str,
        status: str,
    ) -> Decision:
        if status not in {"true", "false", "unknown"}:
            raise ValueError("assumption status must be true, false, or unknown")
        assumptions = dict(decision.assumptions)
        assumptions[assumption] = cast(Literal["true", "false", "unknown"], status)
        return decision.model_copy(update={"assumptions": assumptions})

    def supersede(self, decision: Decision, *, superseded_by_decision_id: str) -> Decision:
        return decision.model_copy(
            update={"status": "superseded", "superseded_by_decision_id": superseded_by_decision_id}
        )

    def evaluate(
        self,
        decision: Decision,
        *,
        observed_outcomes: list[ObservedOutcome],
    ) -> Decision:
        by_name = {outcome.metric_name: outcome.observed_value for outcome in observed_outcomes}
        comparisons: dict[str, bool] = {}
        for expected in decision.expected_outcomes:
            observed = by_name.get(expected.metric_name)
            if observed is None:
                continue
            comparisons[expected.metric_name] = self._compare(observed, expected)
        status = "validated" if comparisons and all(comparisons.values()) else "invalidated" if comparisons else "evaluating"
        return decision.model_copy(
            update={
                "status": status,
                "observed_outcomes": observed_outcomes,
                "evaluation": {"comparisons": comparisons},
            }
        )

    def reversal_rate(self, decisions: list[Decision]) -> float:
        decided = [decision for decision in decisions if decision.status in {"validated", "invalidated", "superseded"}]
        if not decided:
            return 0.0
        reversed_count = sum(1 for decision in decided if decision.status in {"invalidated", "superseded"})
        return reversed_count / len(decided)

    def _compare(self, observed: float, expected: ExpectedOutcome) -> bool:
        if expected.comparator == "lt":
            return observed < expected.expected_value + expected.tolerance
        if expected.comparator == "lte":
            return observed <= expected.expected_value + expected.tolerance
        if expected.comparator == "gt":
            return observed > expected.expected_value - expected.tolerance
        if expected.comparator == "gte":
            return observed >= expected.expected_value - expected.tolerance
        return abs(observed - expected.expected_value) <= expected.tolerance
