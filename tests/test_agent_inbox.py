from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from workforce_runtime.config.runtime_config import load_runtime_config
from workforce_runtime.core import AgentProfile, Budget, Company, Organization, ReportContract, UsageCost
from workforce_runtime.inbox import RabbitMQAgentInboxQueue
from workforce_runtime.scheduler.dispatcher import AgentInboxDispatcher
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import RuntimeContext, WorkerRun


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


class FakeWorkerAdapter:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.started_task_ids: list[str] = []

    def declare_capabilities(self) -> list[str]:
        return ["fake"]

    def start_task(self, task, runtime_context: RuntimeContext) -> WorkerRun:
        self.started_task_ids.append(task.task_id)
        runtime_context.runtime.update_task_status(task.task_id, status="completed", actor_id=runtime_context.agent_id)
        run_dir = self.tmp_path / "runs" / task.task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.txt"
        stderr_path = run_dir / "stderr.txt"
        task_contract_path = run_dir / "task.json"
        stdout_path.write_text("ok")
        stderr_path.write_text("")
        task_contract_path.write_text(task.model_dump_json())
        return WorkerRun(
            run_id=f"fake_{task.task_id}",
            task_id=task.task_id,
            returncode=0,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            task_contract_path=task_contract_path,
        )

    def collect_artifacts(self, run_id: str) -> list[Path]:
        return []

    def stop_task(self, run_id: str) -> None:
        return None

    def get_usage(self, run_id: str) -> dict[str, int]:
        return {}


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


def test_check_progress_enqueues_subordinate_inbox_notice(tmp_path: Path) -> None:
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Fix parser", objective="Fix parser.", assign_to="codex_worker")

        result = runtime.check_progress(
            manager_id="engineering_manager",
            target_agent_id="codex_worker",
            task_id=task.task_id,
            message="Please report current progress.",
        )

        assert result["inbox_item_id"]
        notices = runtime.list_agent_inbox_items(agent_id="codex_worker", status="queued")
        assert any(item.kind == "system_notice" and item.payload["message"] == "Please report current progress." for item in notices)


def test_agent_inbox_dispatcher_runs_assignment_with_worker_adapter(tmp_path: Path) -> None:
    fake_worker = FakeWorkerAdapter(tmp_path)
    with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        task = runtime.create_task(title="Fix parser", objective="Fix parser.", assign_to="codex_worker")

        result = AgentInboxDispatcher(
            runtime,
            db_path=tmp_path / "runtime.sqlite",
            workspace=tmp_path / "workspace",
            adapter_factory=lambda _agent: fake_worker,
        ).run_once(agent_ids=["codex_worker"])

        assert result.claimed == 1
        assert result.completed == 1
        assert result.failed == 0
        assert fake_worker.started_task_ids == [task.task_id]
        assert runtime.require_task(task.task_id).status == "completed"
        completed = runtime.list_agent_inbox_items(agent_id="codex_worker", status="completed")
        assert len(completed) == 1
        assert completed[0].kind == "assignment"


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
