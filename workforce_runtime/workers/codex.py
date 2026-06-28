from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from workforce_runtime.config import format_model_context_note, load_runtime_config
from workforce_runtime.config.model_failover import is_unavailable_model_error
from workforce_runtime.core import Artifact, ReportContract, TaskContract, UsageCost
from workforce_runtime.core.permissions import DELEGATE_TASK, REPORT_TO_HUMAN, SUBMIT_ARTIFACT
from workforce_runtime.storage import FileStore
from workforce_runtime.workers.base import RuntimeContext, WorkerRun
from workforce_runtime.workers.env import worker_process_env
from workforce_runtime.workers.process_runner import run_process_streaming
from workforce_runtime.workers.sandbox import apply_process_sandbox, record_sandbox_application, worker_extra_args
from workforce_runtime.workers.session_resume import (
    consume_queued_steers_for_resume,
    extract_codex_session_id,
)


class CodexWorker:
    def __init__(
        self,
        *,
        codex_executable: str | None = None,
        profile: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        approval_policy: str | None = None,
        sandbox_mode: str | None = None,
    ) -> None:
        config = load_runtime_config().get("workers", {}).get("codex", {})
        self.codex_executable = codex_executable or str(config.get("executable") or "codex")
        self.profile = profile or str(config.get("profile") or "workforce-openrouter")
        self.model = model or config.get("model")
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else config.get("timeout_seconds")
        self.approval_policy = approval_policy or str(config.get("approval_policy") or "never")
        self.sandbox_mode = sandbox_mode or str(config.get("sandbox_mode") or "workspace-write")
        self._runs: dict[str, WorkerRun] = {}
        self._usage: dict[str, dict[str, int]] = {}

    def declare_capabilities(self) -> list[str]:
        return ["codex", "code_editing", "test_execution", "reporting"]

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        run_id = f"run_{uuid4().hex[:12]}"
        workspace = runtime_context.workspace.resolve()
        file_store = FileStore(workspace)
        task_dir = file_store.agent_task_run_dir(
            agent_id=runtime_context.agent_id,
            task_id=task.task_id,
            run_id=run_id,
        )
        task_contract_path = task_dir / "task_contract.json"
        final_message_path = task_dir / "codex-final.md"
        task_contract_path.write_text(task.model_dump_json(indent=2))
        runtime_context.runtime.materialize_agent_skills(
            agent_id=runtime_context.agent_id,
            worker_type="codex",
            workspace=workspace,
            task_id=task.task_id,
            run_id=run_id,
            actor_id=runtime_context.agent_id,
        )

        prompt = self._build_prompt(task, runtime_context)
        runtime_context.runtime.update_task_status(
            task.task_id,
            status="in_progress",
            actor_id=runtime_context.agent_id,
        )

        agent = runtime_context.runtime.get_agent(runtime_context.agent_id)
        command_model = self.model or (agent.model if agent is not None else None)
        # Try the assigned model, then fail over down the configured fallback
        # chain when a run fails with a model-level error (unavailable model or
        # rate limit / 429). Each replacement is persisted on the agent so later
        # runs reuse the working model.
        mcp_config_args = self._mcp_config_args(runtime_context)
        attempted_models: list[str] = []
        max_model_attempts = 6
        streamed = None
        for _ in range(max_model_attempts):
            attempted_models.append(str(command_model or ""))
            command = [
                self.codex_executable,
                *worker_extra_args("codex"),
                "--profile",
                self.profile,
                *mcp_config_args,
                *(["-m", str(command_model)] if command_model else []),
                "-a",
                self.approval_policy,
                "-s",
                self.sandbox_mode,
                "-C",
                str(workspace),
                "exec",
                "--json",
                "--output-last-message",
                str(final_message_path),
                prompt,
            ]
            sandboxed = apply_process_sandbox(command, worker_type="codex", workspace=workspace)
            record_sandbox_application(
                runtime_context.runtime,
                application=sandboxed,
                run_id=run_id,
                task_id=task.task_id,
                agent_id=runtime_context.agent_id,
            )

            streamed = run_process_streaming(
                command=sandboxed.command,
                cwd=workspace,
                env=worker_process_env(
                    runtime_context,
                    run_id=run_id,
                    task=task,
                    task_contract_path=task_contract_path,
                    run_dir=task_dir,
                ),
                timeout_seconds=self.timeout_seconds,
                runtime=runtime_context.runtime,
                file_store=file_store,
                run_id=run_id,
                task_id=task.task_id,
                agent_id=runtime_context.agent_id,
                timeout_message="codex worker timed out",
                run_dir=task_dir,
            )
            if streamed.returncode == 0:
                break
            run_error = self._extract_run_error(streamed.stdout_path)
            if not run_error or not is_unavailable_model_error(run_error):
                break
            replacement = runtime_context.runtime.auto_replace_unavailable_agent_model(
                agent_id=runtime_context.agent_id,
                failed_model=str(command_model or ""),
                error=run_error,
                task_id=task.task_id,
            )
            if replacement is None or not replacement.model or replacement.model in attempted_models:
                break
            command_model = replacement.model

        returncode = streamed.returncode
        stdout_path = streamed.stdout_path
        stderr_path = streamed.stderr_path

        usage = self._extract_usage(stdout_path)
        self._usage[run_id] = usage

        final_text = final_message_path.read_text() if final_message_path.exists() else ""
        provider_session_id = extract_codex_session_id(stdout_path.read_text())
        resume_command = f"codex exec resume {provider_session_id}" if provider_session_id else ""
        session_metadata: dict[str, object] = {
            "executable": self.codex_executable,
            "profile": self.profile,
            "model": command_model or "",
            "approval_policy": self.approval_policy,
            "sandbox_mode": self.sandbox_mode,
            "execution_mode": sandboxed.metadata.get("execution_mode", "full_access"),
            "sandbox_applied": sandboxed.applied,
            "sandbox_command_prefix": sandboxed.metadata.get("sandbox_command_prefix", []),
            "sandbox_settings_path": sandboxed.metadata.get("sandbox_settings_path", ""),
            "timeout_seconds": self.timeout_seconds or "",
        }
        if provider_session_id:
            runtime_context.runtime.record_provider_session(
                provider="codex",
                provider_session_id=provider_session_id,
                run_id=run_id,
                task_id=task.task_id,
                actor_id=runtime_context.agent_id,
                workspace=str(workspace),
                resume_command=resume_command,
                worker_type="codex",
                metadata=session_metadata,
            )
            queued_results = consume_queued_steers_for_resume(
                runtime_context.runtime,
                agent_id=runtime_context.agent_id,
                task_id=task.task_id,
                provider_session_id=provider_session_id,
                workspace=workspace,
                metadata=session_metadata,
            )
            if queued_results and queued_results[-1].final_text.strip():
                final_text = queued_results[-1].final_text
                final_message_path.write_text(final_text)
        diff_path = self._capture_git_diff(file_store, workspace, task.task_id)
        final_status = "completed" if returncode == 0 and final_text.strip() else "failed"

        if final_message_path.exists():
            runtime_context.runtime.register_artifact(
                Artifact(
                    artifact_id=f"artifact_{uuid4().hex[:12]}",
                    task_id=task.task_id,
                    agent_id=runtime_context.agent_id,
                    type="codex_final_message",
                    path=str(final_message_path),
                    description="Final Codex worker message.",
                )
            )
        if diff_path is not None:
            runtime_context.runtime.register_artifact(
                Artifact(
                    artifact_id=f"artifact_{uuid4().hex[:12]}",
                    task_id=task.task_id,
                    agent_id=runtime_context.agent_id,
                    type="git_diff",
                    path=str(diff_path),
                    description="Git diff after Codex worker run.",
                )
            )

        summary = final_text.strip() or "Codex worker did not produce a final message."
        # The root agent (no manager) reports upward to the human via
        # report_to_human(); routing a report() to the non-existent "human"
        # agent would break the manager-review flow. Everyone else reports to
        # their direct manager and triggers manager review.
        is_root = agent is not None and agent.manager_id is None
        if is_root and agent.has_permission(REPORT_TO_HUMAN):
            runtime_context.runtime.report_to_human(
                from_agent_id=runtime_context.agent_id,
                task_id=task.task_id,
                title=f"Task complete: {task.title}"[:120],
                message=summary,
                status=final_status,
                confidence=0.75 if returncode == 0 else 0.2,
                next_action="Review the result." if final_status == "completed" else "Inspect Codex logs.",
                requires_decision=False,
            )
        else:
            runtime_context.runtime.register_report(
                ReportContract(
                    report_id=f"report_{uuid4().hex[:12]}",
                    from_agent_id=runtime_context.agent_id,
                    to_agent_id=runtime_context.manager_id or "human",
                    task_id=task.task_id,
                    summary=summary,
                    status=final_status,
                    work_done=["Ran Codex CLI worker", "Captured stdout, stderr, final message, and git diff"],
                    evidence=[
                        {"type": "stdout", "path": str(stdout_path)},
                        {"type": "stderr", "path": str(stderr_path)},
                    ],
                    risks=[] if returncode == 0 else ["Codex process exited with a nonzero status."],
                    blockers=[],
                    confidence=0.75 if returncode == 0 else 0.2,
                    cost=UsageCost(
                        tokens_used=usage["input_tokens"] + usage["output_tokens"],
                        runtime_seconds=0,
                        tool_calls=0,
                    ),
                    next_action="Ready for manager review." if final_status == "completed" else "Inspect Codex logs.",
                    requires_decision=False,
                    alignment_check="Codex received the structured task contract.",
                )
            )
        runtime_context.runtime.update_task_status(
            task.task_id,
            status=final_status,
            actor_id=runtime_context.agent_id,
        )

        run = WorkerRun(
            run_id=run_id,
            task_id=task.task_id,
            returncode=returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            task_contract_path=task_contract_path,
            provider_session_id=provider_session_id,
            resume_command=resume_command,
        )
        self._runs[run_id] = run
        return run

    def collect_artifacts(self, run_id: str) -> list[Path]:
        run = self._runs[run_id]
        paths = list(run.stdout_path.parent.iterdir())
        legacy_dir = FileStore(FileStore.workspace_from_run_file(run.stdout_path)).task_artifact_dir(run.task_id)
        if legacy_dir.exists():
            paths.extend(legacy_dir.iterdir())
        return sorted(set(paths))

    def stop_task(self, run_id: str) -> None:
        if run_id not in self._runs:
            raise KeyError(f"run not found: {run_id}")

    def get_usage(self, run_id: str) -> dict[str, int]:
        return self._usage[run_id]

    def _mcp_config_args(self, runtime_context: RuntimeContext) -> list[str]:
        """Codex -c flags that connect the agent to the Workforce MCP stdio server.

        Without this, codex starts with zero MCP servers and the agent has no
        assign()/report()/get_task_dossier() tools, so it cannot delegate. The
        spawned MCP server inherits codex's environment (WORKFORCE_RUNTIME_DB,
        WORKFORCE_AGENT_ID, ...) which scopes tool calls to this agent; we also
        set them explicitly so the server is correctly scoped even if codex does
        not forward the full environment to MCP subprocesses.
        """
        mcp_command = sys.executable or "python3"
        db_path = str(runtime_context.db_path)
        agent_id = runtime_context.agent_id
        manager_id = runtime_context.manager_id or ""
        return [
            "-c",
            f'mcp_servers.workforce.command="{mcp_command}"',
            "-c",
            'mcp_servers.workforce.args=["-m", "workforce_runtime", "mcp", "serve"]',
            "-c",
            f'mcp_servers.workforce.env.WORKFORCE_RUNTIME_DB="{db_path}"',
            "-c",
            f'mcp_servers.workforce.env.WORKFORCE_AGENT_ID="{agent_id}"',
            "-c",
            f'mcp_servers.workforce.env.WORKFORCE_MANAGER_ID="{manager_id}"',
        ]

    def _role_guidance(self, runtime_context: RuntimeContext, *, is_manager: bool) -> str:
        if not is_manager:
            return (
                "You are an EXECUTION worker. Do the work yourself inside the workspace, "
                "then use submit_artifact() to register every deliverable file and report() "
                "to send structured completion evidence to your manager. Do NOT delegate."
            )
        reports = [
            a
            for a in runtime_context.runtime.store.list_agents()
            if a.manager_id == runtime_context.agent_id
        ]
        if reports:
            roster = "\n".join(f"  - {a.id} (role: {a.role})" for a in reports)
        else:
            roster = "  (no direct reports configured)"
        return (
            "You are a MANAGEMENT agent. You CANNOT submit artifacts or do the implementation "
            "yourself. You MUST break the objective into concrete work and delegate it to your "
            "direct reports using the assign() MCP tool, e.g. "
            "assign(to_agent_id=\"<report id>\", title=\"...\", objective=\"...\", "
            "parent_task_id=\"<this task id>\"). Your direct reports are:\n"
            f"{roster}\n"
            "After delegating, use report() to summarize how you divided the work. Do NOT call "
            "submit_artifact() and do NOT create deliverable files yourself."
        )

    def _build_prompt(self, task: TaskContract, runtime_context: RuntimeContext) -> str:
        agent = runtime_context.runtime.get_agent(runtime_context.agent_id)
        model = agent.model if agent is not None else ""
        permissions = list(agent.permissions) if agent is not None else []
        can_submit = SUBMIT_ARTIFACT in permissions
        can_delegate = DELEGATE_TASK in permissions
        # A management agent can delegate but cannot submit artifacts directly. It
        # must hand work down to its direct reports instead of doing it itself,
        # otherwise it will hit permission walls (e.g. submit_artifact) and fail.
        is_manager = can_delegate and not can_submit
        role_guidance = self._role_guidance(runtime_context, is_manager=is_manager)
        return f"""You are an AI worker inside Workforce Runtime.

Your agent id is: {runtime_context.agent_id}
Your manager is: {runtime_context.manager_id or "human"}
Your assigned model is: {model or "runtime default"}.
Your permissions are: {", ".join(permissions) or "none"}.
{format_model_context_note(model)}
Your assigned task is:

{task.model_dump_json(indent=2)}

You must work only within the given workspace.
You must respect all constraints.
You must only use MCP tools that match your permissions listed above.

{role_guidance}

When Workforce Runtime MCP tools are available:
- Use get_task_dossier() to fetch requirements, division of work, task documents, reports, artifacts, and recent events.
- Use upsert_task_doc() to preserve new requirements, decisions, notes, risks, or division-of-work updates.
- Use request_tool() when repeated missing capabilities make the task unnecessarily manual.
- If this is a report-review task, inspect the report and artifacts, then call review_report() with an explicit decision.

When you finish, provide a concise final report with:
- summary
- status
- work_done
- evidence
- risks
- blockers
- confidence
- cost estimate
- next_action
- whether a decision is required

Do not claim completion without producing a final report or artifact.
"""

    def _capture_git_diff(self, file_store: FileStore, workspace: Path, task_id: str) -> Path | None:
        result = subprocess.run(
            ["git", "diff", "--"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return file_store.save_git_diff(task_id, result.stdout)

    def _extract_run_error(self, stdout_path: Path) -> str:
        """Return the last error message emitted by codex exec --json, if any."""
        error = ""
        try:
            lines = stdout_path.read_text().splitlines()
        except OSError:
            return ""
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = str(event.get("type") or "")
            if event_type in ("error", "stream_error", "turn.failed"):
                err = event.get("error")
                if isinstance(err, dict):
                    error = str(err.get("message") or err)
                else:
                    error = str(event.get("message") or err or "")
            elif event_type == "item.completed":
                item = event.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "error":
                    error = str(item.get("message") or "")
        return error

    def _extract_usage(self, stdout_path: Path) -> dict[str, int]:
        usage = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        for line in stdout_path.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                event_usage = event.get("usage") or {}
                for key in usage:
                    usage[key] = int(event_usage.get(key) or 0)
        return usage
