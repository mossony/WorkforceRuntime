from __future__ import annotations

import subprocess
import sys

from workforce_runtime.v2.pipeline import run_v2_shadow_demo
from workforce_runtime.v2.store import V2SQLiteStore


def test_v2_shadow_demo_runs_full_governance_loop(tmp_path) -> None:
    db_path = tmp_path / "v2.sqlite"

    result = run_v2_shadow_demo(db_path=db_path)

    assert result.imported_event_count == 12
    assert result.work_graph.edges
    assert any(metric.name == "median_approval_latency" for metric in result.metrics)
    assert result.findings
    assert len(result.proposals) >= 2
    assert all(proposal.validation_errors == [] for proposal in result.proposals)
    assert result.simulations
    assert result.decision.status == "decided"
    assert result.selected_proposal_id
    assert result.audit_record_ids
    assert result.experiment.conclusion == "retained"
    assert any(event.metadata.get("backend") == "codex" for event in result.treatment_events)

    with V2SQLiteStore(db_path) as store:
        assert store.list_events(result.organization_id)
        assert store.list_snapshots(result.organization_id)
        assert store.list_findings(result.organization_id)
        assert store.list_proposals()
        assert store.list_decisions()
        assert store.list_experiments()
        assert store.list_audit_records(result.organization_id)


def test_v2_cli_demo_exports_result_and_dashboard_chain(tmp_path) -> None:
    db_path = tmp_path / "v2.sqlite"
    out_path = tmp_path / "result.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "--db",
            str(db_path),
            "v2",
            "demo",
            "--out",
            str(out_path),
        ],
        cwd="/Users/boyangwan/Desktop/WorkforceRuntime",
        check=True,
        capture_output=True,
        text=True,
    )

    assert "event -> metric -> finding -> proposal -> simulation -> decision -> applied change -> experiment result" in completed.stdout
    assert "conclusion=retained" in completed.stdout
    assert out_path.exists()
