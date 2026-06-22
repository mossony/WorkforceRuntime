from __future__ import annotations

from workforce_runtime.v2.models import Metric, OrganizationChangeProposal, SimulationResult


class HistoricalReplaySimulator:
    def baseline(self, *, metrics: list[Metric]) -> SimulationResult:
        values = {metric.name: metric.value for metric in metrics}
        sample_size = sum(metric.sample_size for metric in metrics)
        warnings = ["low-sample baseline"] if sample_size < 10 else []
        return SimulationResult(
            id="simulation_baseline",
            baseline_metric_values=values,
            scenario_metric_values=values,
            assumptions=["Baseline replay uses observed metric values directly."],
            warnings=warnings,
            comparable=True,
        )

    def simulate(
        self,
        *,
        proposal: OrganizationChangeProposal,
        baseline_metrics: list[Metric],
    ) -> SimulationResult:
        baseline = {metric.name: metric.value for metric in baseline_metrics}
        scenario = dict(baseline)
        assumptions = [
            "Simulation focuses on queueing, latency, routing, retry, and cost.",
            "Semantic reasoning quality is held constant.",
        ]
        warnings: list[str] = []
        if sum(metric.sample_size for metric in baseline_metrics) < 10:
            warnings.append("low-sample scenario; treat effect sizes as directional")

        effect_multiplier = self._effect_multiplier(proposal)
        for metric_name in [
            "median_approval_latency",
            "median_review_latency",
            "median_task_cycle_time",
            "cost_per_accepted_artifact_tokens",
        ]:
            value = scenario.get(metric_name)
            if isinstance(value, (int, float)):
                scenario[metric_name] = max(value * effect_multiplier, 0)
        for metric_name in ["rejection_rate", "rework_rate"]:
            value = scenario.get(metric_name)
            if isinstance(value, (int, float)):
                quality_change = float(proposal.expected_effects.get("quality_change", 0) or 0)
                scenario[metric_name] = max(value + quality_change, 0)

        uncertainty: dict[str, tuple[float, float]] = {}
        for metric_name, value in scenario.items():
            if isinstance(value, (int, float)):
                uncertainty[metric_name] = (float(value) * 0.85, float(value) * 1.15)
        return SimulationResult(
            id=f"simulation_{proposal.id}",
            proposal_id=proposal.id,
            baseline_metric_values=baseline,
            scenario_metric_values=scenario,
            assumptions=assumptions,
            warnings=warnings,
            uncertainty=uncertainty,
            comparable=True,
        )

    def _effect_multiplier(self, proposal: OrganizationChangeProposal) -> float:
        explicit = proposal.expected_effects.get("median_approval_latency_change")
        if isinstance(explicit, (int, float)):
            return max(1.0 + float(explicit), 0.05)
        if any(change.change_type == "create_position" for change in proposal.atomic_changes):
            return 0.75
        if any(change.change_type == "update_approval_policy" for change in proposal.atomic_changes):
            return 0.65
        return 0.9
