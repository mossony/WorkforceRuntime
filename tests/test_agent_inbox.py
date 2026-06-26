from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from workforce_runtime.config.runtime_config import load_runtime_config
from workforce_runtime.core import AgentProfile, Budget, Company, Organization, ReportContract, UsageCost
from workforce_runtime.inbox import RabbitMQAgentInboxQueue
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_assign_discuss_and_report_create_agent_inbox_items(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Fix parser", objective="Fix parser.", assign_to="codex_worker")

        assignment = runtime.list_agent_inbox_items(agent_id="codex_worker", status="queued")
        assert len(assignment) == 1
        assert assignment[0].kind == "assignment"
        assert assignment[0].payload["task_id"] == task.task_id

        runtime.send_discussion_message(
            from_agent_id="codex_worker",
            to_agent_id="claude_worker",
            task_id=task.task_id,
            message="Please sanity-check the parser edge cases.",
        )
        messages = runtime.list_agent_inbox_items(agent_id="claude_worker", status="queued")
        assert len(messages) == 1
        assert messages[0].kind == "message"
        assert messages[0].payload["message"] == "Please sanity-check the parser edge cases."

        runtime.register_report(
            ReportContract(
                report_id="report_001",
                from_agent_id="codex_worker",
                to_agent_id="engineering_manager",
                task_id=task.task_id,
                summary="Parser fixed.",
                status="completed",
                work_done=["Updated parser"],
                evidence=[{"type": "test", "path": "pytest.log"}],
                risks=[],
                blockers=[],
                confidence=0.9,
                cost=UsageCost(tokens_used=10, runtime_seconds=1, tool_calls=0),
                next_action="Review.",
                requires_decision=False,
                alignment_check="Aligned.",
            )
        )
        review_items = runtime.list_agent_inbox_items(agent_id="engineering_manager", status="queued")
        assert any(item.kind == "report_review" and item.payload["report_id"] == "report_001" for item in review_items)
        event_types = [event.event_type for event in runtime.store.list_events()]
        assert "manager_review_decided" not in event_types


def test_rabbitmq_agent_inbox_publish_claim_and_complete(tmp_path: Path) -> None:
    if not _rabbitmq_available():
        pytest.skip("RabbitMQ is not available")

    agent_id = f"rabbit_worker_{uuid4().hex[:8]}"
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_organization(
            Organization(
                company=Company(name="Rabbit Test Co", mission="Test RabbitMQ inbox", headcount_limit=1),
                agents=[
                    AgentProfile(
                        id=agent_id,
                        name="Rabbit Worker",
                        role="Worker",
                        department="QA",
                        manager_id=None,
                        worker_type="generic_cli",
                        permissions=["report"],
                        budget=Budget(max_tokens=1000, max_runtime_seconds=60, max_tool_calls=5),
                    )
                ],
            )
        )
        task = runtime.create_task(title="Rabbit inbox task", objective="Claim this task.", assign_to=agent_id)

        claimed = runtime.claim_agent_inbox_items(agent_id=agent_id, lease_owner=agent_id, actor_id=agent_id)
        assert len(claimed) == 1
        assert claimed[0].kind == "assignment"
        assert claimed[0].task_id == task.task_id
        assert claimed[0].status == "leased"

        completed = runtime.complete_agent_inbox_item(
            claimed[0].inbox_item_id,
            actor_id=agent_id,
            result={"handled": True},
        )
        assert completed.status == "completed"
        assert completed.payload["result"] == {"handled": True}


def _rabbitmq_available() -> bool:
    config = load_runtime_config().get("agent_inbox", {}).get("rabbitmq", {})
    try:
        RabbitMQAgentInboxQueue(config).ensure_agent_queues([f"probe_{uuid4().hex[:8]}"])
    except Exception:
        return False
    return True
