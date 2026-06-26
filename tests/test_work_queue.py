from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from workforce_runtime.core import WorkQueuePolicy
from workforce_runtime.mcp.server import MCPServer
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_work_queue_claims_by_priority_under_max_active_agents(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Queue test", objective="Test queue", assign_to="ceo")
        low = runtime.enqueue_work_item(
            actor_id="system",
            agent_id="codex_worker",
            kind="worker_run",
            task_id=task.task_id,
            priority=1,
        )
        high = runtime.enqueue_work_item(
            actor_id="system",
            agent_id="claude_worker",
            kind="worker_run",
            task_id=task.task_id,
            priority=10,
        )
        mid = runtime.enqueue_work_item(
            actor_id="system",
            agent_id="engineering_manager",
            kind="worker_run",
            task_id=task.task_id,
            priority=5,
        )

        claimed = runtime.claim_work_items(
            lease_owner="dispatcher",
            limit=10,
            policy=WorkQueuePolicy(max_active_agents=2, per_kind_limits={"worker_run": 10}),
        )

    assert [item.work_item_id for item in claimed] == [high.work_item_id, mid.work_item_id]
    assert low.work_item_id not in {item.work_item_id for item in claimed}
    assert len({item.agent_id for item in claimed}) == 2


def test_work_queue_enforces_model_and_tool_limits(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Queue limits", objective="Test queue limits", assign_to="ceo")
        runtime.enqueue_work_item(
            actor_id="system",
            agent_id="codex_worker",
            kind="llm_request",
            task_id=task.task_id,
            model="shared-model",
        )
        runtime.enqueue_work_item(
            actor_id="system",
            agent_id="claude_worker",
            kind="llm_request",
            task_id=task.task_id,
            model="shared-model",
        )
        runtime.enqueue_work_item(
            actor_id="system",
            agent_id="engineering_manager",
            kind="tool_call",
            task_id=task.task_id,
            tool_name="web_fetch",
        )
        runtime.enqueue_work_item(
            actor_id="system",
            agent_id="vp_engineering",
            kind="tool_call",
            task_id=task.task_id,
            tool_name="web_fetch",
        )

        claimed = runtime.claim_work_items(
            lease_owner="dispatcher",
            limit=10,
            policy=WorkQueuePolicy(
                max_active_agents=10,
                per_kind_limits={"llm_request": 10, "tool_call": 10},
                per_model_limits={"shared-model": 1},
                per_tool_limits={"web_fetch": 1},
            ),
        )

    assert len(claimed) == 2
    assert sum(1 for item in claimed if item.kind == "llm_request") == 1
    assert sum(1 for item in claimed if item.kind == "tool_call") == 1


def test_work_queue_requeues_failures_until_max_attempts(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        item = runtime.enqueue_work_item(
            actor_id="system",
            agent_id="codex_worker",
            kind="worker_run",
            max_attempts=2,
        )
        first = runtime.claim_work_items(lease_owner="dispatcher", policy=WorkQueuePolicy())[0]
        requeued = runtime.fail_work_item(first.work_item_id, actor_id="dispatcher", error="temporary", retry=True)
        second = runtime.claim_work_items(lease_owner="dispatcher", policy=WorkQueuePolicy())[0]
        failed = runtime.fail_work_item(second.work_item_id, actor_id="dispatcher", error="permanent", retry=True)

    assert first.work_item_id == item.work_item_id
    assert requeued.status == "queued"
    assert requeued.attempts == 1
    assert failed.status == "failed"
    assert failed.attempts == 2


def test_work_queue_releases_expired_leases(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        runtime.enqueue_work_item(actor_id="system", agent_id="codex_worker", kind="worker_run")
        claimed = runtime.claim_work_items(
            lease_owner="dispatcher",
            now=now,
            policy=WorkQueuePolicy(lease_seconds=1),
        )[0]

        released = runtime.release_expired_work_item_leases(now=now + timedelta(seconds=2))
        reclaimed = runtime.claim_work_items(
            lease_owner="dispatcher",
            now=now + timedelta(seconds=3),
            policy=WorkQueuePolicy(lease_seconds=1),
        )[0]

    assert released[0].work_item_id == claimed.work_item_id
    assert released[0].status == "queued"
    assert reclaimed.work_item_id == claimed.work_item_id
    assert reclaimed.attempts == 2


def test_mcp_work_queue_tools_end_to_end(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        server = MCPServer(runtime)

        enqueued = _mcp_tool_call(
            server,
            "enqueue_work",
            {
                "from_agent_id": "system",
                "agent_id": "codex_worker",
                "kind": "tool_call",
                "tool_name": "web_fetch",
                "payload": {"url": "https://example.com"},
            },
        )
        claimed = _mcp_tool_call(
            server,
            "claim_work",
            {"lease_owner": "dispatcher", "limit": 1, "policy": {"max_active_agents": 1}},
        )
        completed = _mcp_tool_call(
            server,
            "complete_work",
            {"from_agent_id": "dispatcher", "work_item_id": enqueued["work_item_id"], "result": {"ok": True}},
        )
        queue = _mcp_tool_call(server, "get_work_queue", {})

    assert claimed["claimed_count"] == 1
    assert completed["work_item"]["status"] == "completed"
    assert queue["queue"]["status_counts"]["completed"] == 1


def test_mcp_tool_calls_are_queued_in_sandbox_mode(tmp_path: Path) -> None:
    config = {
        "execution": {
            "mode": "sandbox",
            "sandbox": {
                "queue_mcp_tools": True,
                "mcp_tool_queue_timeout_seconds": 1,
                "mcp_tool_queue_excluded_tools": ["enqueue_work", "claim_work", "complete_work", "fail_work", "get_work_queue"],
            },
        },
        "queue": {
            "max_active_agents": 1,
            "lease_seconds": 30,
            "per_kind_limits": {"tool_call": 1},
            "per_tool_limits": {"report": 1},
        },
    }
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Queued report", objective="Report through queue.", assign_to="codex_worker")
        server = MCPServer(runtime, config=config)

        reported = _mcp_tool_call(
            server,
            "report",
            {
                "from_agent_id": "codex_worker",
                "task_id": task.task_id,
                "summary": "Queued report completed.",
                "status": "completed",
                "confidence": 0.9,
                "work_done": ["reported through queue"],
                "evidence": [],
                "risks": [],
                "blockers": [],
                "cost": {"tokens_used": 0, "runtime_seconds": 0, "tool_calls": 1},
            },
        )
        items = runtime.store.list_work_items()
        event_types = [event.event_type for event in runtime.store.list_events()]

    assert reported["ok"] is True
    assert len(items) == 1
    assert items[0].kind == "tool_call"
    assert items[0].tool_name == "report"
    assert items[0].status == "completed"
    assert "mcp_tool_call_queued" in event_types
    assert "work_item_completed" in event_types


def test_mcp_report_normalizes_out_of_range_confidence(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Confidence report", objective="Report with model scale drift.", assign_to="codex_worker")
        server = MCPServer(runtime)

        reported = _mcp_tool_call(
            server,
            "report",
            {
                "from_agent_id": "codex_worker",
                "task_id": task.task_id,
                "summary": "Completed with a 7.5 out of 10 confidence scale.",
                "status": "completed",
                "confidence": 7.5,
                "work_done": ["reported through MCP"],
            },
        )
        report = runtime.store.list_reports_by_task(task.task_id)[0]
        events = runtime.store.list_events()

    assert reported["ok"] is True
    assert reported["confidence"] == 0.75
    assert report.confidence == 0.75
    assert any(event.event_type == "mcp_tool_input_normalized" for event in events)


def _mcp_tool_call(server: MCPServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    assert "error" not in response
    return response["result"]["structuredContent"]  # type: ignore[return-value]
