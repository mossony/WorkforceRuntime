from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DistortionAnalysis:
    source_report_id: str
    derived_report_id: str
    fact_retention_score: float
    risk_retention_score: float
    dissent_retention_score: float
    confidence_inflation: float
    missing_provenance: bool
    unsupported_confidence_increase: bool


def analyze_report_distortion(
    *,
    source_report_id: str,
    derived_report_id: str,
    source_claims: list[str],
    derived_claims: list[str],
    source_risks: list[str],
    derived_risks: list[str],
    source_dissent: list[str],
    derived_dissent: list[str],
    source_confidence: float,
    derived_confidence: float,
    derived_evidence_ids: list[str],
) -> DistortionAnalysis:
    fact_score = _retention_score(source_claims, derived_claims)
    risk_score = _retention_score(source_risks, derived_risks)
    dissent_score = _retention_score(source_dissent, derived_dissent)
    confidence_inflation = max(derived_confidence - source_confidence, 0.0)
    missing_provenance = bool(source_claims or source_risks or source_dissent) and not derived_evidence_ids
    return DistortionAnalysis(
        source_report_id=source_report_id,
        derived_report_id=derived_report_id,
        fact_retention_score=fact_score,
        risk_retention_score=risk_score,
        dissent_retention_score=dissent_score,
        confidence_inflation=confidence_inflation,
        missing_provenance=missing_provenance,
        unsupported_confidence_increase=confidence_inflation > 0.05 and missing_provenance,
    )


def _retention_score(source: list[str], derived: list[str]) -> float:
    if not source:
        return 1.0
    derived_text = "\n".join(item.lower() for item in derived)
    retained = sum(1 for item in source if item.lower() in derived_text)
    return retained / len(source)
