from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from workforce_runtime.evals.benchmark import BenchmarkCase
from workforce_runtime.llm import OpenRouterClient, extract_json_object


DEFAULT_SWE_BENCH_MODEL = "poolside/laguna-m.1:free"
DEFAULT_SWE_BENCH_DATASET = "SWE-bench/SWE-bench_Lite"


class SWEBenchInstance(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    instance_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    base_commit: str = ""
    patch: str = ""
    test_patch: str = ""
    test_command: str = ""
    fail_to_pass: list[str] = Field(default_factory=list, validation_alias=AliasChoices("fail_to_pass", "FAIL_TO_PASS"))
    pass_to_pass: list[str] = Field(default_factory=list, validation_alias=AliasChoices("pass_to_pass", "PASS_TO_PASS"))
    hints_text: str = ""
    version: str = ""

    @field_validator("fail_to_pass", "pass_to_pass", mode="before")
    @classmethod
    def _parse_test_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return [stripped]
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
            return [str(parsed)]
        return [str(value)]


class SWEBenchRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    repo: str
    model: str
    resolved: bool
    workspace: str
    repo_path: str
    candidate_patch_path: str
    raw_response_path: str
    test_patch_path: str
    test_log_path: str
    setup_log_path: str
    apply_success: bool
    test_patch_success: bool
    setup_success: bool
    test_returncode: int | None = None
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    summary: str = ""
    error: str = ""


def load_swe_bench_instance(path: str | Path) -> SWEBenchInstance:
    return SWEBenchInstance.model_validate(json.loads(Path(path).read_text()))


def load_swe_bench_instances_from_hf(
    instance_ids: list[str],
    *,
    dataset: str = DEFAULT_SWE_BENCH_DATASET,
    split: str = "test",
    page_size: int = 100,
) -> list[SWEBenchInstance]:
    remaining = set(instance_ids)
    found: dict[str, SWEBenchInstance] = {}
    offset = 0
    total: int | None = None
    while remaining and (total is None or offset < total):
        query = urlencode(
            {
                "dataset": dataset,
                "config": "default",
                "split": split,
                "offset": offset,
                "length": page_size,
            }
        )
        with urlopen(f"https://datasets-server.huggingface.co/rows?{query}", timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        total = int(payload.get("num_rows_total") or 0)
        for item in payload.get("rows") or []:
            row = item.get("row") or {}
            instance_id = str(row.get("instance_id") or "")
            if instance_id in remaining:
                found[instance_id] = SWEBenchInstance.model_validate(row)
                remaining.remove(instance_id)
        offset += page_size
    missing = [instance_id for instance_id in instance_ids if instance_id not in found]
    if missing:
        raise ValueError(f"SWE-bench instances not found: {', '.join(missing)}")
    return [found[instance_id] for instance_id in instance_ids]


def build_swe_bench_comparison_cases(
    instance: SWEBenchInstance,
    *,
    model: str = DEFAULT_SWE_BENCH_MODEL,
) -> dict[str, BenchmarkCase]:
    base = {
        "source_urls": [],
        "constraints": [
            f"Repository: {instance.repo}",
            f"Base commit: {instance.base_commit or 'not specified'}",
            "Produce a minimal patch that addresses the failing tests.",
            "Do not change unrelated behavior.",
        ],
        "acceptance_criteria": [
            "Fail-to-pass tests pass after the patch.",
            "Existing pass-to-pass tests still pass.",
            "A patch artifact and test log are produced.",
        ],
        "expected_artifacts": ["patch", "test_log"],
        "management_model": "openai/gpt-oss-120b:free",
        "worker_model": model,
        "judge_model": "openai/gpt-oss-120b:free",
    }
    test_context = _swe_test_context(instance)
    return {
        "single_codex": BenchmarkCase(
            id=f"{instance.instance_id}_single_codex",
            title=f"SWE-bench single Codex: {instance.instance_id}",
            goal=(
                "Solve this SWE-bench instance with a single Codex-style worker using "
                f"{model}.\n\n{instance.problem_statement}\n\n{test_context}"
            ),
            headcount_limit=3,
            token_budget=500000,
            **base,
        ),
        "distributed": BenchmarkCase(
            id=f"{instance.instance_id}_distributed",
            title=f"SWE-bench distributed workforce: {instance.instance_id}",
            goal=(
                "Solve this SWE-bench instance through a manager-distributed Workforce Runtime run "
                f"with terminal workers using {model}.\n\n{instance.problem_statement}\n\n{test_context}"
            ),
            headcount_limit=6,
            token_budget=800000,
            **base,
        ),
    }


def _swe_test_context(instance: SWEBenchInstance) -> str:
    details: dict[str, Any] = {
        "test_command": instance.test_command,
        "fail_to_pass": instance.fail_to_pass,
        "pass_to_pass": instance.pass_to_pass,
    }
    if instance.hints_text:
        details["hints_text"] = instance.hints_text
    return "SWE-bench test context:\n" + json.dumps(details, indent=2)


def run_swe_bench_instance(
    instance: SWEBenchInstance,
    *,
    workspace: str | Path,
    model: str = DEFAULT_SWE_BENCH_MODEL,
    client: OpenRouterClient | None = None,
    max_tokens: int = 6000,
    test_timeout_seconds: int = 600,
    setup_timeout_seconds: int = 900,
) -> SWEBenchRunResult:
    workdir = Path(workspace)
    case_dir = workdir / instance.instance_id
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = case_dir / "repo"
    artifacts_dir = case_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    raw_response_path = artifacts_dir / "model_response.json"
    candidate_patch_path = artifacts_dir / "candidate.patch"
    test_patch_path = artifacts_dir / "test.patch"
    setup_log_path = artifacts_dir / "setup.log"
    test_log_path = artifacts_dir / "test.log"

    result_defaults = {
        "instance_id": instance.instance_id,
        "repo": instance.repo,
        "model": model,
        "workspace": str(case_dir),
        "repo_path": str(repo_dir),
        "candidate_patch_path": str(candidate_patch_path),
        "raw_response_path": str(raw_response_path),
        "test_patch_path": str(test_patch_path),
        "test_log_path": str(test_log_path),
        "setup_log_path": str(setup_log_path),
        "fail_to_pass": instance.fail_to_pass,
        "pass_to_pass": instance.pass_to_pass,
        "changed_files": _changed_files_from_patch(instance.patch),
    }

    try:
        _clone_repo(instance, repo_dir)
    except Exception as exc:
        return SWEBenchRunResult(
            **result_defaults,
            resolved=False,
            apply_success=False,
            test_patch_success=False,
            setup_success=False,
            error=f"clone failed: {exc}",
        )

    llm_client = client or OpenRouterClient(timeout_seconds=240)
    try:
        patch, summary, raw_payload = _generate_candidate_patch(
            instance,
            repo_dir=repo_dir,
            model=model,
            client=llm_client,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        raw_payload = {"error": str(exc)}
        patch = ""
        summary = "model patch generation failed"
    raw_response_path.write_text(json.dumps(raw_payload, indent=2))
    candidate_patch_path.write_text(patch)

    apply_success = _apply_patch(repo_dir, candidate_patch_path)
    test_patch_path.write_text(instance.test_patch)
    test_patch_success = _apply_patch(repo_dir, test_patch_path)
    setup_success = _setup_repo(repo_dir, setup_log_path, timeout_seconds=setup_timeout_seconds)
    test_returncode: int | None = None
    if setup_success and test_patch_success:
        test_returncode = _run_tests(
            repo_dir,
            instance.fail_to_pass + instance.pass_to_pass,
            test_log_path,
            timeout_seconds=test_timeout_seconds,
        )
    else:
        test_log_path.write_text("Tests skipped because setup or test patch failed.\n")

    resolved = bool(apply_success and test_patch_success and setup_success and test_returncode == 0)
    return SWEBenchRunResult(
        **result_defaults,
        resolved=resolved,
        apply_success=apply_success,
        test_patch_success=test_patch_success,
        setup_success=setup_success,
        test_returncode=test_returncode,
        summary=summary,
        error="" if resolved else _result_error(apply_success, test_patch_success, setup_success, test_returncode),
    )


def _clone_repo(instance: SWEBenchInstance, repo_dir: Path) -> None:
    url = f"https://github.com/{instance.repo}.git"
    _run(["git", "clone", "--no-checkout", url, str(repo_dir)], cwd=repo_dir.parent, timeout_seconds=900)
    _run(["git", "checkout", instance.base_commit], cwd=repo_dir, timeout_seconds=300)


def _generate_candidate_patch(
    instance: SWEBenchInstance,
    *,
    repo_dir: Path,
    model: str,
    client: OpenRouterClient,
    max_tokens: int,
) -> tuple[str, str, dict[str, Any]]:
    source_context = _source_context(repo_dir, _changed_files_from_patch(instance.patch))
    prompt = f"""
You are solving a real SWE-bench instance. Produce a minimal source-code patch.

Instance id: {instance.instance_id}
Repository: {instance.repo}
Base commit: {instance.base_commit}

Problem statement:
{instance.problem_statement}

Hints:
{instance.hints_text or "None"}

Fail-to-pass tests:
{json.dumps(instance.fail_to_pass, indent=2)}

Pass-to-pass tests:
{json.dumps(instance.pass_to_pass, indent=2)}

Relevant source files from the base commit:
{source_context}

Return JSON only with this schema:
{{
  "summary": "short explanation",
  "patch": "unified diff from repo root using diff --git a/... b/... paths"
}}

Do not modify tests. Do not include markdown fences. The patch must apply with git apply.
""".strip()
    response = client.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are an expert Python maintainer. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
        reasoning=True,
        stream=False,
        response_format={"type": "json_object"},
    )
    payload = extract_json_object(response.content)
    patch = _extract_patch(str(payload.get("patch") or ""))
    return patch, str(payload.get("summary") or ""), {"response": payload, "usage": response.usage, "raw": response.raw}


def _source_context(repo_dir: Path, paths: list[str], *, max_chars_per_file: int = 80000) -> str:
    parts: list[str] = []
    for relative in paths:
        path = repo_dir / relative
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(errors="replace")
        if len(text) > max_chars_per_file:
            text = text[:max_chars_per_file] + "\n...[truncated]...\n"
        parts.append(f"\n--- {relative} ---\n{text}")
    return "\n".join(parts) if parts else "No source files could be read."


def _changed_files_from_patch(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path not in paths and not path.startswith("tests/"):
            paths.append(path)
    return paths


def _extract_patch(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("diff --git ")
    if start >= 0:
        stripped = stripped[start:]
    return stripped.strip() + ("\n" if stripped.strip() else "")


def _apply_patch(repo_dir: Path, patch_path: Path) -> bool:
    if not patch_path.read_text(errors="replace").strip():
        return False
    result = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch_path)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    fallback = subprocess.run(
        ["patch", "-p1", "-i", str(patch_path)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return fallback.returncode == 0


def _setup_repo(repo_dir: Path, log_path: Path, *, timeout_seconds: int) -> bool:
    venv_dir = repo_dir / ".venv"
    commands = [
        [sys.executable, "-m", "venv", str(venv_dir)],
        [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"],
        [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "-e", ".[test]"],
        [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "pytest"],
        *_repo_specific_setup_commands(repo_dir, venv_dir),
    ]
    logs: list[str] = []
    for command in commands:
        result = subprocess.run(
            command,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        logs.append(_format_command_result(command, result.returncode, result.stdout, result.stderr))
        if result.returncode != 0 and command[-1] == ".[test]":
            fallback = subprocess.run(
                [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "-e", "."],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            logs.append(_format_command_result(["pip", "install", "-e", "."], fallback.returncode, fallback.stdout, fallback.stderr))
            if fallback.returncode != 0:
                log_path.write_text("\n\n".join(logs))
                return False
            continue
        if result.returncode != 0:
            log_path.write_text("\n\n".join(logs))
            return False
    log_path.write_text("\n\n".join(logs))
    return True


def _repo_specific_setup_commands(repo_dir: Path, venv_dir: Path) -> list[list[str]]:
    python = str(venv_dir / "bin" / "python")
    if (repo_dir / "src" / "flask").exists():
        return [
            [
                python,
                "-m",
                "pip",
                "install",
                "Werkzeug<3",
                "click<9",
                "Jinja2<4",
                "itsdangerous<3",
                "pytest<8",
                "python-dotenv",
            ]
        ]
    return []


def _run_tests(repo_dir: Path, selectors: list[str], log_path: Path, *, timeout_seconds: int) -> int:
    python = repo_dir / ".venv" / "bin" / "python"
    command = [str(python), "-m", "pytest", "-q", *selectors]
    try:
        result = subprocess.run(
            command,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            _format_command_result(command, 124, exc.stdout or "", exc.stderr or "")
        )
        return 124
    log_path.write_text(_format_command_result(command, result.returncode, result.stdout, result.stderr))
    return result.returncode


def _run(command: list[str], *, cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(_format_command_result(command, result.returncode, result.stdout, result.stderr))
    return result


def _format_command_result(command: list[str], returncode: int, stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    out = stdout.decode(errors="replace") if isinstance(stdout, bytes) else (stdout or "")
    err = stderr.decode(errors="replace") if isinstance(stderr, bytes) else (stderr or "")
    return "\n".join(
        [
            "$ " + " ".join(command),
            f"returncode={returncode}",
            "",
            "STDOUT:",
            out,
            "",
            "STDERR:",
            err,
        ]
    )


def _result_error(
    apply_success: bool,
    test_patch_success: bool,
    setup_success: bool,
    test_returncode: int | None,
) -> str:
    if not apply_success:
        return "candidate patch did not apply"
    if not test_patch_success:
        return "SWE-bench test patch did not apply"
    if not setup_success:
        return "repo setup failed"
    if test_returncode != 0:
        return f"tests failed with return code {test_returncode}"
    return ""
