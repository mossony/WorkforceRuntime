from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from workforce_runtime.evals import build_swe_bench_comparison_cases, load_swe_bench_instance


EXAMPLE_INSTANCE = Path("examples/benchmarks/swe_bench_instance_example.json")


def test_swe_bench_adapter_builds_single_and_distributed_cases() -> None:
    instance = load_swe_bench_instance(EXAMPLE_INSTANCE)

    cases = build_swe_bench_comparison_cases(instance, model="poolside/laguna-m.1:free")

    assert set(cases) == {"single_codex", "distributed"}
    assert cases["single_codex"].worker_model == "poolside/laguna-m.1:free"
    assert cases["distributed"].worker_model == "poolside/laguna-m.1:free"
    assert cases["single_codex"].headcount_limit == 3
    assert cases["distributed"].headcount_limit == 6
    assert "SWE-bench test context" in cases["distributed"].goal


def test_swe_bench_plan_cli_writes_benchmark_cases(tmp_path: Path) -> None:
    out_dir = tmp_path / "cases"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_runtime",
            "benchmark",
            "swe-bench-plan",
            "--instance",
            str(EXAMPLE_INSTANCE),
            "--out-dir",
            str(out_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert Path(payload["cases"]["single_codex"]).exists()
    assert Path(payload["cases"]["distributed"]).exists()
