from __future__ import annotations

import subprocess
from pathlib import Path

from workforce_runtime.core import SkillDefinition, SkillFile
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import CodexWorker, RuntimeContext


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_runtime_materializes_codex_skill_to_native_agents_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        skill = runtime.create_skill(
            name="Repo Helper",
            description="Use for repository helper smoke tests.",
            instructions="Return CODEX_NATIVE_SKILL_OK when asked for the helper marker.",
            provider_targets=["codex"],
        )
        runtime.assign_skill(skill_id=skill.skill_id, target_type="worker_type", target_id="codex")

        materializations = runtime.materialize_agent_skills(
            agent_id="codex_worker",
            worker_type="codex",
            workspace=workspace,
            task_id="task_skill_smoke",
            run_id="run_skill_smoke",
        )

        skill_file = workspace / ".agents" / "skills" / "repo-helper" / "SKILL.md"
        assert skill_file.exists()
        assert "CODEX_NATIVE_SKILL_OK" in skill_file.read_text()
        assert len(materializations) == 1
        assert materializations[0].target_dir == str(skill_file.parent)
        assert runtime.list_skill_materializations()[0].skill_id == skill.skill_id
        assert "skill_materialized" in [event.event_type for event in runtime.store.list_events()]


def test_runtime_materializes_claude_role_skill_to_native_claude_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        skill = runtime.create_skill(
            name="Plan Reviewer",
            description="Use for software engineer planning reviews.",
            instructions="Return CLAUDE_NATIVE_SKILL_OK when asked for the review marker.",
            provider_targets=["claude_code"],
        )
        runtime.assign_skill(skill_id=skill.skill_id, target_type="role", target_id="Software Engineer")

        materializations = runtime.materialize_agent_skills(
            agent_id="claude_worker",
            worker_type="claude_code",
            workspace=workspace,
            task_id="task_skill_smoke",
            run_id="run_skill_smoke",
        )

        skill_file = workspace / ".claude" / "skills" / "plan-reviewer" / "SKILL.md"
        assert skill_file.exists()
        assert "CLAUDE_NATIVE_SKILL_OK" in skill_file.read_text()
        assert len(materializations) == 1


def test_archived_or_wrong_provider_skill_is_not_materialized(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        archived = runtime.create_skill(
            name="Archived Skill",
            description="Should not be used.",
            instructions="Do not write this.",
            status="archived",
            provider_targets=["codex"],
        )
        claude_only = runtime.create_skill(
            name="Claude Only",
            description="Should not be materialized for Codex.",
            instructions="Do not write this either.",
            provider_targets=["claude_code"],
        )
        runtime.assign_skill(skill_id=archived.skill_id, target_type="global")
        runtime.assign_skill(skill_id=claude_only.skill_id, target_type="global")

        materializations = runtime.materialize_agent_skills(
            agent_id="codex_worker",
            worker_type="codex",
            workspace=workspace,
        )

        assert materializations == []
        assert not (workspace / ".agents" / "skills" / "archived-skill").exists()
        assert not (workspace / ".agents" / "skills" / "claude-only").exists()


def test_worker_start_materializes_assigned_codex_skill(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    (workspace / "README.md").write_text("# Sample\n")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True, text=True)

    fake_codex = tmp_path / "fake_codex.py"
    _write_fake_codex(fake_codex)

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        skill = runtime.create_skill(
            name="Worker Boot Skill",
            description="Use for worker boot smoke tests.",
            instructions="This marker proves worker startup materialized the skill: WORKER_BOOT_SKILL_OK.",
            provider_targets=["codex"],
        )
        runtime.assign_skill(skill_id=skill.skill_id, target_type="agent", target_id="codex_worker")
        task = runtime.create_task(
            title="Skill boot smoke",
            objective="Run fake Codex after skill materialization.",
            assign_to="codex_worker",
        )
        worker = CodexWorker(codex_executable=str(fake_codex), profile="test", model="test-model", timeout_seconds=10)

        worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

        skill_file = workspace / ".agents" / "skills" / "worker-boot-skill" / "SKILL.md"
        assert skill_file.exists()
        assert "WORKER_BOOT_SKILL_OK" in skill_file.read_text()


def test_unsafe_skill_file_path_is_rejected_and_recorded(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        skill = SkillDefinition(
            skill_id="skill_bad_path",
            name="Bad Path",
            description="Unsafe path test.",
            provider_targets=["codex"],
            status="approved",
            files=[
                SkillFile(relative_path="SKILL.md", content="---\nname: bad-path\ndescription: Unsafe path.\n---\n"),
                SkillFile(relative_path="../escape.txt", content="bad"),
            ],
        )
        runtime.save_skill_definition(skill, actor_id="human")
        runtime.assign_skill(skill_id=skill.skill_id, target_type="agent", target_id="codex_worker")

        materializations = runtime.materialize_agent_skills(
            agent_id="codex_worker",
            worker_type="codex",
            workspace=workspace,
        )

        assert materializations == []
        assert "skill_materialization_failed" in [event.event_type for event in runtime.store.list_events()]
        assert not (workspace.parent / "escape.txt").exists()


def _write_fake_codex(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
from pathlib import Path
import json
import sys

final_path = None
args = sys.argv[1:]
for index, arg in enumerate(args):
    if arg == "--output-last-message":
        final_path = Path(args[index + 1])

Path("README.md").write_text("# Sample\\n\\nUpdated by fake Codex.\\n")
if final_path is not None:
    final_path.write_text("Fake Codex completed the task.")
print(json.dumps({"type": "thread.started", "thread_id": "fake"}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
"""
    )
    path.chmod(path.stat().st_mode | 0o111)
