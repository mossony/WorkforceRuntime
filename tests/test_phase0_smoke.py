import subprocess
import sys


def test_module_entrypoint_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "workforce_runtime"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Workforce Runtime" in result.stdout
