from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from workforce_runtime.config import load_runtime_config
from workforce_runtime.core.agent_inbox import AgentInboxItem
from workforce_runtime.core.agent_profile import AgentProfile
from workforce_runtime.core.task import TaskContract
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import (
    ClaudeCodeInteractiveWorker,
    ClaudeCodeWorker,
    CodexWorker,
    GenericCLIWorker,
    RuntimeContext,
)
from workforce_runtime.workers.base import WorkerAdapter


WorkerAdapterFactory = Callable[[AgentProfile], WorkerAdapter]


@dataclass(frozen=True)
class DispatchResult:
    claimed: int = 0
    completed: int = 0
    failed: int = 0


class Dispatcher:
    def can_dispatch(self, task: TaskContract) -> bool:
        return task.assigned_to is not None and task.status in {"assigned", "in_progress"}


class AgentInboxDispatcher:
    def __init__(
        self,
        runtime: WorkforceRuntime,
        *,
        db_path: str | Path,
        workspace: str | Path,
        lease_owner: str = "agent-inbox-dispatcher",
        adapter_factory: WorkerAdapterFactory | None = None,
    ) -> None:
        self.runtime = runtime
        self.db_path = Path(db_path)
        self.workspace = Path(workspace)
        self.lease_owner = lease_owner
        self.adapter_factory = adapter_factory or self._default_adapter_for
        self._adapter_cache: dict[str, WorkerAdapter] = {}

    def run_once(self, *, agent_ids: list[str] | None = None, limit_per_agent: int = 1) -> DispatchResult:
        claimed = 0
        completed = 0
        failed = 0
        agents = [self.runtime.get_agent(agent_id) for agent_id in agent_ids] if agent_ids else self.runtime.store.list_agents()
        for agent in [item for item in agents if item is not None]:
            for item in self.runtime.claim_agent_inbox_items(
                agent_id=agent.id,
                lease_owner=self.lease_owner,
                limit=limit_per_agent,
                actor_id="runtime",
            ):
                claimed += 1
                try:
                    self._dispatch_item(agent, item)
                except Exception as exc:  # noqa: BLE001 - item failure must be persisted instead of crashing the loop.
                    failed += 1
                    self.runtime.fail_agent_inbox_item(
                        item.inbox_item_id,
                        actor_id="runtime",
                        error=str(exc),
                        retry=item.attempts < item.max_attempts,
                    )
                else:
                    completed += 1
                    self.runtime.complete_agent_inbox_item(
                        item.inbox_item_id,
                        actor_id="runtime",
                        result={"kind": item.kind, "task_id": item.task_id or ""},
                    )
        return DispatchResult(claimed=claimed, completed=completed, failed=failed)

    def run_until_idle(
        self,
        *,
        max_cycles: int = 100,
        agent_ids: list[str] | None = None,
        limit_per_agent: int = 1,
    ) -> DispatchResult:
        total = DispatchResult()
        for _ in range(max(1, max_cycles)):
            result = self.run_once(agent_ids=agent_ids, limit_per_agent=limit_per_agent)
            total = DispatchResult(
                claimed=total.claimed + result.claimed,
                completed=total.completed + result.completed,
                failed=total.failed + result.failed,
            )
            if result.claimed == 0:
                break
        return total

    def _dispatch_item(self, agent: AgentProfile, item: AgentInboxItem) -> None:
        if item.kind in {"assignment", "report_review"}:
            task_id = item.task_id or str(item.payload.get("task_id") or item.payload.get("review_task_id") or "")
            if not task_id:
                raise ValueError(f"inbox item {item.inbox_item_id} has no task_id")
            task = self.runtime.require_task(task_id)
            self._run_agent_task(agent, task)
            return
        if item.kind in {"message", "human_steer", "system_notice"}:
            task = self._message_task_for(agent, item)
            self._run_agent_task(agent, task)
            return
        raise ValueError(f"unsupported inbox item kind: {item.kind}")

    def _message_task_for(self, agent: AgentProfile, item: AgentInboxItem) -> TaskContract:
        message = str(item.payload.get("message") or item.payload.get("summary") or item.kind)
        if item.task_id:
            source = self.runtime.require_task(item.task_id)
            return self.runtime.create_task(
                title=f"Handle {item.kind} for {source.title}",
                objective=(
                    f"Handle this incoming {item.kind} from {item.from_agent_id}:\n\n{message}\n\n"
                    "Use MCP tools to answer, report progress, or preserve any relevant task notes."
                ),
                assign_to=agent.id,
                assigned_by="runtime",
                parent_task_id=source.task_id,
                root_goal_id=source.root_goal_id or source.task_id,
                context_refs=[f"task:{source.task_id}", f"inbox:{item.inbox_item_id}"],
                constraints=["Handle the inbox item without losing the original task context."],
                acceptance_criteria=["The message or notice is processed and any needed follow-up is recorded."],
                required_artifacts=[],
            )
        return self.runtime.create_task(
            title=f"Handle {item.kind}",
            objective=(
                f"Handle this incoming {item.kind} from {item.from_agent_id}:\n\n{message}\n\n"
                "Use MCP tools to answer, report progress, or preserve any relevant notes."
            ),
            assign_to=agent.id,
            assigned_by="runtime",
            context_refs=[f"inbox:{item.inbox_item_id}"],
            constraints=[],
            acceptance_criteria=["The message or notice is processed."],
            required_artifacts=[],
        )

    def _run_agent_task(self, agent: AgentProfile, task: TaskContract) -> None:
        adapter = self._adapter_cache.get(agent.id)
        if adapter is None:
            adapter = self.adapter_factory(agent)
            self._adapter_cache[agent.id] = adapter
        adapter.start_task(
            task,
            RuntimeContext(
                runtime=self.runtime,
                db_path=self.db_path,
                workspace=self.workspace,
                agent_id=agent.id,
                manager_id=agent.manager_id,
            ),
        )

    def _default_adapter_for(self, agent: AgentProfile) -> WorkerAdapter:
        worker_type = agent.worker_type.strip()
        if worker_type == "codex":
            return CodexWorker()
        if worker_type == "claude_code":
            return ClaudeCodeWorker()
        if worker_type == "claude_code_interactive":
            return ClaudeCodeInteractiveWorker()
        if worker_type == "generic_cli":
            config = load_runtime_config().get("workers", {}).get("generic_cli", {})
            command = config.get("command")
            if not isinstance(command, list) or not command:
                raise ValueError("generic_cli worker requires workers.generic_cli.command")
            return GenericCLIWorker([str(part) for part in command])
        if worker_type.startswith("openrouter_"):
            raise ValueError(
                f"{worker_type} is disabled for decision agents; configure agent {agent.id} as codex or claude_code"
            )
        raise ValueError(f"unsupported worker_type for agent {agent.id}: {worker_type}")
