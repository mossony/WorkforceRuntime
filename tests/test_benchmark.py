from __future__ import annotations

import json
from pathlib import Path

import workforce_runtime.evals.benchmark as benchmark_module
from workforce_runtime.evals import BenchmarkCase, run_benchmark_case
from workforce_runtime.llm import OpenRouterResponse
from workforce_runtime.server.runtime import WorkforceRuntime


class FakeBenchmarkClient:
    def is_configured(self) -> bool:
        return True

    def chat(self, **kwargs: object) -> OpenRouterResponse:
        messages = kwargs.get("messages")
        assert isinstance(messages, list)
        system = str(messages[0].get("content", ""))
        if "design small AI company org charts" in system:
            content = _org_payload()
        elif "strict benchmark judge" in system:
            content = json.dumps(
                {
                    "summary": "Run completed with a reasonable organization and evidence trail.",
                    "scores": {
                        "overall": {"score": 0.86, "reason": "The run completed and produced the expected artifact."}
                    },
                }
            )
        elif "terminal Workforce Runtime worker" in system:
            content = json.dumps(
                {
                    "artifact_markdown": "# Fixture research\n\nThe source fixture was inspected.",
                    "summary": "Inspected the source fixture and produced the artifact.",
                    "findings": ["Fetched source", "Produced artifact"],
                    "risks": [],
                    "confidence": 0.91,
                    "next_action": "Ready for review.",
                }
            )
        else:
            content = json.dumps(
                {
                    "title": "Delegate benchmark step",
                    "message": "Preserve the goal, source URL, expected artifact, and report requirements.",
                    "acceptance_criteria": ["Submit the expected artifact", "Report evidence"],
                }
            )
        on_delta = kwargs.get("on_delta")
        if callable(on_delta):
            midpoint = max(1, len(content) // 2)
            on_delta(content[:midpoint])
            on_delta(content[midpoint:])
        return OpenRouterResponse(content=content, raw={}, usage={"total_tokens": 10})


class FlakyJsonBenchmarkClient(FakeBenchmarkClient):
    def __init__(self) -> None:
        self.assignment_calls = 0

    def chat(self, **kwargs: object) -> OpenRouterResponse:
        messages = kwargs.get("messages")
        assert isinstance(messages, list)
        system = str(messages[0].get("content", ""))
        if (
            "design small AI company org charts" not in system
            and "strict benchmark judge" not in system
            and "terminal Workforce Runtime worker" not in system
        ):
            self.assignment_calls += 1
            if self.assignment_calls == 1:
                content = '```jsoncjsonc{"title\\":\\"Malformed assignment\\",'
                on_delta = kwargs.get("on_delta")
                if callable(on_delta):
                    on_delta(content)
                return OpenRouterResponse(content=content, raw={"fixture": "malformed"}, usage={"total_tokens": 3})
        return super().chat(**kwargs)


def test_benchmark_case_runs_with_fake_llm_and_scores(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("Fixture source text about example domains.")
    case = BenchmarkCase(
        id="fixture_case",
        title="Fixture Web Research",
        goal="Research the provided fixture source and write a short artifact.",
        source_urls=[source.as_uri()],
        acceptance_criteria=["Expected artifact is submitted"],
        expected_artifacts=["benchmark_research_summary"],
        headcount_limit=5,
        token_budget=300000,
    )

    result = run_benchmark_case(
        tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
        case=case,
        use_llm=True,
        judge="llm",
        client=FakeBenchmarkClient(),
    )

    assert result.ok is True
    assert result.designed_agent_count == 4
    assert result.metrics["artifact_coverage"] == 1.0
    assert any(score.name == "overall" and score.score == 0.86 for score in result.scores)
    assert result.artifacts[0]["type"] == "benchmark_research_summary"

    with WorkforceRuntime(result.db_path) as runtime:
        events = runtime.store.list_events()
        reports = runtime.store.list_reports()

    assert "benchmark_run_started" in [event.event_type for event in events]
    assert "benchmark_run_finished" in [event.event_type for event in events]
    assert "human_report_registered" in [event.event_type for event in events]
    assert any(event.event_type == "tool_call_started" and event.payload.get("tool_name") == "web_fetch" for event in events)
    assert any(event.event_type == "mcp_tool_call_started" and event.payload.get("tool_name") == "discuss" for event in events)
    assert any(event.event_type == "agent_run_finished" and event.payload.get("usage", {}).get("total_tokens") == 10 for event in events)
    assert any(report.cost.tokens_used == 10 for report in reports)


def test_benchmark_retries_malformed_json_and_records_attempt_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        benchmark_module,
        "_agent_json_retry_config",
        lambda _overrides=None: {
            "max_retries": 2,
            "retry_initial_delay_seconds": 0.0,
            "retry_backoff_multiplier": 1.0,
            "retry_max_delay_seconds": 0.0,
        },
    )
    source = tmp_path / "source.txt"
    source.write_text("Fixture source text about example domains.")
    client = FlakyJsonBenchmarkClient()
    case = BenchmarkCase(
        id="retry_case",
        title="Retry Fixture",
        goal="Research the provided fixture source and write a short artifact.",
        source_urls=[source.as_uri()],
        acceptance_criteria=["Expected artifact is submitted"],
        expected_artifacts=["benchmark_research_summary"],
        headcount_limit=5,
        token_budget=300000,
    )

    result = run_benchmark_case(
        tmp_path / "runtime.sqlite",
        workspace=tmp_path / "workspace",
        case=case,
        use_llm=True,
        judge="heuristic",
        client=client,
    )

    with WorkforceRuntime(result.db_path) as runtime:
        events = runtime.store.list_events()

    retry_events = [event for event in events if event.event_type == "agent_run_retrying"]
    failed_attempts = [event for event in events if event.event_type == "agent_run_attempt_failed"]
    path_events = [
        event
        for event in events
        if event.event_type == "agent_run_path_registered" and event.payload.get("attempts") == 2
    ]

    assert result.ok is True
    assert client.assignment_calls >= 2
    assert retry_events
    assert failed_attempts
    assert path_events
    run_dir = Path(str(path_events[0].payload["run_dir"]))
    assert (run_dir / "attempt_01_response.txt").read_text().startswith("```jsoncjsonc")
    assert (run_dir / "attempt_01_error.txt").read_text()
    assert (run_dir / "attempt_02_response.txt").exists()
    assert Path(str(path_events[0].payload["raw_response_path"])).exists()


def _org_payload() -> str:
    return json.dumps(
        {
            "company": {
                "name": "Benchmark Workforce",
                "mission": "Run benchmark case.",
                "headcount_limit": 5,
                "token_budget": 300000,
            },
            "agents": [
                {
                    "id": "ceo",
                    "name": "CEO Agent",
                    "role": "CEO",
                    "department": "Executive",
                    "manager_id": None,
                    "worker_type": "openrouter_manager",
                    "model": "openai/gpt-oss-120b:free",
                    "responsibilities": ["Own benchmark goal"],
                    "permissions": ["delegate_task", "report", "hire_agent"],
                    "budget": {"max_tokens": 60000, "max_runtime_seconds": 3600, "max_tool_calls": 40},
                },
                {
                    "id": "research_manager",
                    "name": "Research Manager Agent",
                    "role": "Research Manager",
                    "department": "Research",
                    "manager_id": "ceo",
                    "worker_type": "openrouter_manager",
                    "model": "openai/gpt-oss-120b:free",
                    "responsibilities": ["Assign benchmark work"],
                    "permissions": ["delegate_task", "report", "request_budget"],
                    "budget": {"max_tokens": 60000, "max_runtime_seconds": 3600, "max_tool_calls": 40},
                },
                {
                    "id": "primary_worker",
                    "name": "Primary Worker Agent",
                    "role": "Research Worker",
                    "department": "Research",
                    "manager_id": "research_manager",
                    "worker_type": "openrouter_worker",
                    "model": "poolside/laguna-m.1:free",
                    "responsibilities": ["Produce artifact"],
                    "permissions": ["read_repo", "submit_artifact", "report"],
                    "budget": {"max_tokens": 60000, "max_runtime_seconds": 3600, "max_tool_calls": 40},
                },
                {
                    "id": "peer_reviewer",
                    "name": "Peer Reviewer Agent",
                    "role": "Peer Reviewer",
                    "department": "Research",
                    "manager_id": "research_manager",
                    "worker_type": "openrouter_worker",
                    "model": "poolside/laguna-m.1:free",
                    "responsibilities": ["Review artifact"],
                    "permissions": ["read_repo", "report"],
                    "budget": {"max_tokens": 30000, "max_runtime_seconds": 3600, "max_tool_calls": 20},
                },
            ],
        }
    )
