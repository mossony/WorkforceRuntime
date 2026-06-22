from __future__ import annotations

from workforce_runtime.v2.models import Experiment, Metric, OrganizationChangeProposal, OrganizationSnapshot


class ExperimentRunner:
    def create_experiment(
        self,
        *,
        experiment_id: str,
        baseline_snapshot: OrganizationSnapshot,
        applied_proposal: OrganizationChangeProposal,
        target_metrics: list[str],
        guardrail_metrics: list[str],
        expected_effect: dict[str, float],
        rollback_thresholds: dict[str, float],
    ) -> Experiment:
        return Experiment(
            id=experiment_id,
            organization_id=baseline_snapshot.organization_id,
            hypothesis=f"{applied_proposal.id} improves {', '.join(target_metrics)}",
            baseline_snapshot_id=baseline_snapshot.id,
            applied_change_set_id=applied_proposal.id,
            baseline_time_window=(None, None),
            treatment_time_window=(None, None),
            target_metrics=target_metrics,
            guardrail_metrics=guardrail_metrics,
            rollback_thresholds=rollback_thresholds,
            expected_effect=expected_effect,
            status="active",
        )

    def evaluate(
        self,
        *,
        experiment: Experiment,
        baseline_metrics: list[Metric],
        treatment_metrics: list[Metric],
        confounding_events: list[str] | None = None,
    ) -> Experiment:
        baseline = {metric.name: metric.value for metric in baseline_metrics}
        treatment = {metric.name: metric.value for metric in treatment_metrics}
        observed_effect: dict[str, float] = {}
        sample_size = 0
        for metric_name in experiment.target_metrics + experiment.guardrail_metrics:
            before = baseline.get(metric_name)
            after = treatment.get(metric_name)
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                observed_effect[metric_name] = float(after) - float(before)
                sample_size += 1
        rollback = False
        for metric_name, threshold in experiment.rollback_thresholds.items():
            effect = observed_effect.get(metric_name)
            if effect is not None and effect > threshold:
                rollback = True
        if rollback:
            conclusion = "rolled_back"
        elif not observed_effect:
            conclusion = "inconclusive"
        else:
            target_improved = any(observed_effect.get(metric_name, 0.0) < 0 for metric_name in experiment.target_metrics)
            conclusion = "retained" if target_improved else "inconclusive"
        return experiment.model_copy(
            update={
                "status": conclusion,
                "observed_effect": observed_effect,
                "conclusion": conclusion,
                "sample_size": sample_size,
                "confounding_events": confounding_events or [],
            }
        )
