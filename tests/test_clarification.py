from __future__ import annotations

from pathlib import Path

import pytest

from workforce_runtime.mcp.server import visible_tool_specs
from workforce_runtime.server.runtime import WorkforceRuntime

EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")
# Chain in the example org: codex_worker -> engineering_manager -> vp_engineering -> ceo -> human


@pytest.fixture()
def runtime(tmp_path: Path) -> WorkforceRuntime:
    rt = WorkforceRuntime(tmp_path / "runtime.sqlite")
    rt.initialize_org(EXAMPLE_ORG)
    return rt


def _task_for_worker(runtime: WorkforceRuntime):
    return runtime.create_task(
        title="Implement feature",
        objective="Build the thing",
        assign_to="codex_worker",
        assigned_by="engineering_manager",
    )


def test_clarification_escalates_up_chain_to_human_then_resumes(runtime: WorkforceRuntime) -> None:
    task = _task_for_worker(runtime)

    clar = runtime.raise_clarification(
        from_agent_id="codex_worker",
        question="Which database backend should I use?",
        task_id=task.task_id,
    )
    # Routed to the worker's direct manager; origin task is blocked.
    assert clar.current_holder_id == "engineering_manager"
    assert clar.status == "open"
    assert runtime.require_task(task.task_id).status == "blocked"

    clar = runtime.escalate_clarification(clarification_id=clar.clarification_id, from_agent_id="engineering_manager")
    assert clar.current_holder_id == "vp_engineering"

    clar = runtime.escalate_clarification(clarification_id=clar.clarification_id, from_agent_id="vp_engineering")
    assert clar.current_holder_id == "ceo"

    # CEO cannot answer -> escalating past the top reaches the human.
    clar = runtime.escalate_clarification(clarification_id=clar.clarification_id, from_agent_id="ceo")
    assert clar.current_holder_id == "human"
    assert clar.status == "awaiting_human"
    assert clar.chain == ["codex_worker", "engineering_manager", "vp_engineering", "ceo", "human"]

    # Human answers -> resolved, answer delivered, origin task resumed for the asker.
    clar = runtime.answer_clarification(
        clarification_id=clar.clarification_id, from_agent_id="human", answer="Use sqlite."
    )
    assert clar.status == "resolved"
    assert clar.answer == "Use sqlite."
    assert clar.answered_by == "human"

    resumed = runtime.require_task(task.task_id)
    assert resumed.status == "assigned"
    assert resumed.assigned_to == "codex_worker"

    docs = runtime.list_task_documents(task.task_id)
    assert any(doc.doc_type == "clarification" for doc in docs)

    resume_items = [
        item
        for item in runtime.store.list_agent_inbox_items(agent_id="codex_worker")
        if item.kind == "assignment" and item.payload.get("clarification_answer") == "Use sqlite."
    ]
    assert resume_items, "asker should receive a resume assignment carrying the answer"


def test_manager_can_answer_clarification_directly(runtime: WorkforceRuntime) -> None:
    task = _task_for_worker(runtime)
    clar = runtime.raise_clarification(
        from_agent_id="codex_worker", question="Edge case?", task_id=task.task_id
    )
    assert clar.current_holder_id == "engineering_manager"

    clar = runtime.answer_clarification(
        clarification_id=clar.clarification_id,
        from_agent_id="engineering_manager",
        answer="Ignore empty input.",
    )
    assert clar.status == "resolved"
    assert clar.answered_by == "engineering_manager"
    assert runtime.require_task(task.task_id).assigned_to == "codex_worker"


def test_only_current_holder_can_escalate_or_answer(runtime: WorkforceRuntime) -> None:
    task = _task_for_worker(runtime)
    clar = runtime.raise_clarification(
        from_agent_id="codex_worker", question="Which API?", task_id=task.task_id
    )
    # Holder is engineering_manager; a different agent cannot act on it.
    with pytest.raises(PermissionError):
        runtime.escalate_clarification(clarification_id=clar.clarification_id, from_agent_id="vp_engineering")
    with pytest.raises(PermissionError):
        runtime.answer_clarification(
            clarification_id=clar.clarification_id, from_agent_id="codex_worker", answer="x"
        )


def test_cannot_answer_resolved_clarification(runtime: WorkforceRuntime) -> None:
    task = _task_for_worker(runtime)
    clar = runtime.raise_clarification(
        from_agent_id="codex_worker", question="Q?", task_id=task.task_id
    )
    runtime.answer_clarification(
        clarification_id=clar.clarification_id, from_agent_id="engineering_manager", answer="A"
    )
    with pytest.raises(ValueError):
        runtime.answer_clarification(
            clarification_id=clar.clarification_id, from_agent_id="engineering_manager", answer="again"
        )


def test_only_human_answers_when_awaiting_human(runtime: WorkforceRuntime) -> None:
    task = _task_for_worker(runtime)
    clar = runtime.raise_clarification(from_agent_id="ceo", question="Strategic?", task_id=task.task_id)
    # CEO has no manager -> immediately awaiting_human.
    assert clar.status == "awaiting_human"
    assert clar.current_holder_id == "human"
    clar = runtime.answer_clarification(
        clarification_id=clar.clarification_id, from_agent_id="human", answer="Do A."
    )
    assert clar.status == "resolved"


def test_clarification_tools_filtered_by_role(runtime: WorkforceRuntime) -> None:
    worker_tools = {spec["name"] for spec in visible_tool_specs(runtime, actor_id="codex_worker")}
    manager_tools = {spec["name"] for spec in visible_tool_specs(runtime, actor_id="engineering_manager")}

    # Every agent can ASK; only managers (delegate_task) escalate/answer.
    assert "ask_clarification" in worker_tools
    assert "escalate_clarification" not in worker_tools
    assert "answer_clarification" not in worker_tools

    assert {"ask_clarification", "escalate_clarification", "answer_clarification"} <= manager_tools
