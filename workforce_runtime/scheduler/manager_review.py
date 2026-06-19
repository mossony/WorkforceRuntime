from __future__ import annotations

from dataclasses import dataclass

from workforce_runtime.core import ReportContract, TaskContract


@dataclass(frozen=True)
class ManagerReviewDecision:
    action: str
    reason: str
    accepted: bool


class ManagerReviewPolicy:
    def decide(self, *, task: TaskContract, report: ReportContract) -> ManagerReviewDecision:
        if report.blockers:
            return ManagerReviewDecision(
                action="request_retry",
                reason="worker report contains blockers",
                accepted=False,
            )
        if report.status != "completed":
            return ManagerReviewDecision(
                action="reject",
                reason=f"worker report status is {report.status}",
                accepted=False,
            )
        if report.confidence < 0.7:
            return ManagerReviewDecision(
                action="request_retry",
                reason="worker report confidence is below 0.70",
                accepted=False,
            )
        if task.required_artifacts and not report.evidence:
            return ManagerReviewDecision(
                action="request_retry",
                reason="task requires artifacts but report has no evidence",
                accepted=False,
            )
        return ManagerReviewDecision(
            action="accept",
            reason="report satisfies initial manager review checks",
            accepted=True,
        )
