from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.core import AgentProfile, Budget, Company, Organization
from workforce_runtime.evals.swe_bench import SWEBenchInstance, load_swe_bench_instances_from_hf
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.v2.v1_bridge import V1V2AnalysisResult, analyze_v1_runtime
from workforce_runtime.workers import CodexWorker, RuntimeContext


DEFAULT_INSTANCE_ID = "sympy__sympy-20590"
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"


class SympyBenchmarkPreparation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_dir: str
    instance_path: str
    repo_path: str
    task_input_path: str
    instance_id: str
    repo: str
    base_commit: str


class SympyBenchmarkRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preparation: SympyBenchmarkPreparation
    runtime_db_path: str
    task_id: str
    worker_run_id: str
    worker_returncode: int | None
    patch_path: str
    patch_nonempty: bool
    modified_test_files: list[str] = Field(default_factory=list)
    analysis_export_dir: str
    v2_analysis: V1V2AnalysisResult
    prediction_path: str | None = None
    swebench_result: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


def prepare_sympy_20590_case(
    *,
    experiment_dir: str | Path,
    instance_id: str = DEFAULT_INSTANCE_ID,
    dataset: str = DEFAULT_DATASET,
) -> SympyBenchmarkPreparation:
    root = Path(experiment_dir).expanduser().resolve()
    _ensure_layout(root)
    instance_path = root / "dataset" / "instance.json"
    if instance_path.exists():
        instance = SWEBenchInstance.model_validate(json.loads(instance_path.read_text()))
    else:
        instance = load_swe_bench_instances_from_hf([instance_id], dataset=dataset)[0]
        instance_path.write_text(instance.model_dump_json(indent=2))

    repo_path = root / "workspace" / "sympy"
    _materialize_repo(instance, repo_path)
    task_input_path = root / "task_input.json"
    task_input_path.write_text(json.dumps(_safe_task_input(instance, repo_path), indent=2))
    return SympyBenchmarkPreparation(
        experiment_dir=str(root),
        instance_path=str(instance_path),
        repo_path=str(repo_path),
        task_input_path=str(task_input_path),
        instance_id=instance.instance_id,
        repo=instance.repo,
        base_commit=instance.base_commit,
    )


def run_sympy_20590_fixed_org_with_v2_review(
    *,
    experiment_dir: str | Path,
    runtime_db_path: str | Path | None = None,
    worker_timeout_seconds: int | None = None,
    codex_model: str | None = None,
    codex_sandbox_mode: str | None = None,
    codex_profile: str | None = None,
    reset_workspace: bool = True,
) -> SympyBenchmarkRunResult:
    preparation = prepare_sympy_20590_case(experiment_dir=experiment_dir)
    root = Path(preparation.experiment_dir)
    repo_path = Path(preparation.repo_path)
    instance = SWEBenchInstance.model_validate(json.loads(Path(preparation.instance_path).read_text()))
    if reset_workspace:
        reset_sympy_workspace(repo_path)

    db_path = Path(runtime_db_path) if runtime_db_path is not None else root / "workforce-runs" / "fixed-org-runtime.sqlite"
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_codex_model = codex_model or "openai/gpt-oss-120b:free"
    org = _fixed_org(worker_model=resolved_codex_model)
    task_input = json.loads(Path(preparation.task_input_path).read_text())
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_organization(org, source="v2_sympy_benchmark_fixed_org")
        task = runtime.create_task(
            title=f"SWE-bench {instance.instance_id}",
            objective=_worker_objective(task_input),
            assign_to="implementer",
            assigned_by="engineering_manager",
            constraints=[
                "Do not read or use the gold patch.",
                "Do not read or use hidden test patch details.",
                "Do not modify tests unless explicitly required by the issue; normally this invalidates the run.",
                "Produce the smallest source-code patch that addresses the issue.",
                "Run relevant validation commands when feasible.",
            ],
            acceptance_criteria=[
                "A non-empty git diff is produced from the base commit.",
                "The final report explains root cause, files changed, validation commands, risks, and next action.",
            ],
            required_artifacts=["model.patch", "final_report"],
        )
        worker = CodexWorker(
            profile=codex_profile,
            model=resolved_codex_model,
            timeout_seconds=worker_timeout_seconds,
            sandbox_mode=codex_sandbox_mode,
        )
        run = worker.start_task(
            task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=repo_path,
                agent_id="implementer",
                manager_id="engineering_manager",
            ),
        )

    artifact_dir = root / "artifacts" / "fixed-org"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    patch_path = artifact_dir / "model.patch"
    patch_path.write_text(_git_diff(repo_path))
    modified_tests = _modified_test_files(repo_path)
    prediction_path = _write_prediction(root, "fixed-org", resolved_codex_model, patch_path) if patch_path.read_text().strip() else None
    analysis_dir = root / "artifacts" / "fixed-org" / "v2-analysis"
    analysis = analyze_v1_runtime(
        v1_db_path=db_path,
        task_id=task.task_id,
        export_dir=analysis_dir,
    )
    warnings: list[str] = []
    if modified_tests:
        warnings.append("Run modified test files; this normally invalidates the benchmark run.")
    if not patch_path.read_text().strip():
        warnings.append("Codex worker did not produce a non-empty patch.")
    return SympyBenchmarkRunResult(
        preparation=preparation,
        runtime_db_path=str(db_path),
        task_id=task.task_id,
        worker_run_id=run.run_id,
        worker_returncode=run.returncode,
        patch_path=str(patch_path),
        patch_nonempty=bool(patch_path.read_text().strip()),
        modified_test_files=modified_tests,
        analysis_export_dir=str(analysis_dir),
        v2_analysis=analysis,
        prediction_path=str(prediction_path) if prediction_path is not None else None,
        warnings=warnings,
    )


def reset_sympy_workspace(repo_path: Path) -> None:
    _run(["git", "reset", "--hard", "workforce-test-base"], cwd=repo_path, timeout_seconds=300)
    _run(["git", "clean", "-fdx"], cwd=repo_path, timeout_seconds=300)
    _run(["git", "switch", "-C", "workforce/sympy-20590", "workforce-test-base"], cwd=repo_path, timeout_seconds=300)


def _ensure_layout(root: Path) -> None:
    for relative in [
        "workspace",
        "dataset",
        "predictions",
        "artifacts/single-agent",
        "artifacts/fixed-org",
        "artifacts/governor-org",
        "workforce-runs",
        "results",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)


def _materialize_repo(instance: SWEBenchInstance, repo_path: Path) -> None:
    if not repo_path.exists():
        _run(["git", "clone", f"https://github.com/{instance.repo}.git", str(repo_path)], cwd=repo_path.parent, timeout_seconds=900)
    _run(["git", "fetch", "--tags", "--force"], cwd=repo_path, timeout_seconds=300)
    _run(["git", "checkout", instance.base_commit], cwd=repo_path, timeout_seconds=300)
    _run(["git", "switch", "-C", "workforce/sympy-20590"], cwd=repo_path, timeout_seconds=300)
    existing = subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/tags/workforce-test-base"], cwd=repo_path, capture_output=True, text=True)
    if existing.returncode != 0:
        _run(["git", "tag", "workforce-test-base"], cwd=repo_path, timeout_seconds=60)


def _safe_task_input(instance: SWEBenchInstance, repo_path: Path) -> dict[str, Any]:
    return {
        "task_id": instance.instance_id,
        "project_id": "sympy_20590_experiment",
        "repository": instance.repo,
        "base_commit": instance.base_commit,
        "problem_statement": instance.problem_statement,
        "workspace_path": str(repo_path),
        "success_contract": {
            "must_produce_git_diff": True,
            "must_not_modify_tests": True,
            "must_explain_root_cause": True,
            "must_report_validation_commands": True,
            "final_score_source": "swebench_harness",
        },
        "constraints": {
            "no_gold_patch_access": True,
            "no_test_patch_access": True,
            "max_worker_runs": 8,
            "max_organization_changes": 2,
        },
    }


def _worker_objective(task_input: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Solve this SWE-bench task in the checked-out repository.",
            "",
            f"Instance: {task_input['task_id']}",
            f"Repository: {task_input['repository']}",
            f"Base commit: {task_input['base_commit']}",
            f"Workspace: {task_input['workspace_path']}",
            "",
            "Problem statement:",
            str(task_input["problem_statement"]),
            "",
            "Do not use gold patch or hidden test patch fields. Work from the problem statement and repository only.",
            "Use focused repository inspection: prefer rg for Symbol, Basic, Printable, and __slots__; avoid printing broad package exports or generated cache files.",
            "Tool constraints: use python3, not python. ripgrep syntax is `rg PATTERN PATH`; do not use `rg -R`.",
            "Keep exploration bounded: after identifying the relevant Symbol MRO classes, edit a candidate fix instead of continuing broad searches.",
            "Before editing, identify which class in Symbol's MRO introduces instance dictionaries, then make the smallest source change that restores slot-only Symbol instances.",
            "If a parent or mixin in the Symbol/Basic MRO lacks __slots__, add `__slots__ = ()` to that class as the first candidate fix, then validate.",
            "Validate with a direct Python check that sympy.Symbol('s') has no __dict__ and run the most relevant focused tests you can find.",
            "When finished, leave the source changes in the git working tree and explain validation performed.",
        ]
    )


def _fixed_org(*, worker_model: str) -> Organization:
    return Organization(
        company=Company(
            name="SymPy 20590 Fixed Organization",
            mission="Solve one SWE-bench task and record enough execution evidence for V2 review.",
            headcount_limit=4,
            token_budget=1_000_000,
        ),
        agents=[
            AgentProfile(
                id="engineering_manager",
                name="Engineering Manager",
                role="Engineering Manager",
                department="Engineering",
                worker_type="generic_cli",
                responsibilities=["assign implementation", "review worker report", "preserve benchmark constraints"],
                permissions=["delegate_task", "report", "report_to_human"],
                budget=Budget(max_tokens=100000, max_runtime_seconds=7200, max_tool_calls=80),
            ),
            AgentProfile(
                id="implementer",
                name="Codex Implementer",
                role="Implementer",
                department="Engineering",
                manager_id="engineering_manager",
                worker_type="codex",
                model=worker_model,
                responsibilities=["investigate issue", "implement patch", "run validation", "report evidence"],
                permissions=["read_repo", "write_branch", "run_tests", "submit_artifact", "report"],
                budget=Budget(max_tokens=250000, max_runtime_seconds=7200, max_tool_calls=200),
            ),
        ],
    )


def _git_diff(repo_path: Path) -> str:
    result = _run(["git", "diff", "workforce-test-base", "--"], cwd=repo_path, timeout_seconds=120)
    return result.stdout


def _modified_test_files(repo_path: Path) -> list[str]:
    result = _run(["git", "diff", "--name-only", "workforce-test-base"], cwd=repo_path, timeout_seconds=120)
    return [
        line
        for line in result.stdout.splitlines()
        if line.startswith("test") or "/test" in line or "/tests/" in line or line.startswith("sympy/testing")
    ]


def _write_prediction(root: Path, run_name: str, model_name: str, patch_path: Path) -> Path:
    prediction_path = root / "predictions" / f"{run_name}.jsonl"
    prediction = {
        "instance_id": DEFAULT_INSTANCE_ID,
        "model_name_or_path": model_name,
        "model_patch": patch_path.read_text(),
    }
    prediction_path.write_text(json.dumps(prediction) + "\n")
    return prediction_path


def _run(command: list[str], *, cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "\n".join(
                [
                    "$ " + " ".join(command),
                    f"cwd={cwd}",
                    f"returncode={result.returncode}",
                    "STDOUT:",
                    result.stdout,
                    "STDERR:",
                    result.stderr,
                ]
            )
        )
    return result
