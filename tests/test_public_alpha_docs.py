from __future__ import annotations

from pathlib import Path


REQUIRED_DOCS = [
    "README.md",
    "docs/WORKFORCE_RUNTIME_GUIDE.md",
    "PRODUCT.md",
    "DESIGN_V2.md",
    "IMPLEMENTATION_PLAN_V2.md",
    "ROADMAP.md",
]


def test_public_alpha_docs_exist() -> None:
    for filename in REQUIRED_DOCS:
        assert Path(filename).exists(), filename


def test_readme_covers_public_alpha_entrypoints() -> None:
    readme = Path("README.md").read_text()

    required_phrases = [
        "what Workforce Runtime is",
        "What Workforce Runtime Is Not",
        "How It Differs From Ordinary Agent Frameworks",
        "demo sample-repo-fix",
        "Defining An Org Chart",
        "Adding A Worker Adapter",
        "MCP Reporting",
        "Codex And Claude Code",
    ]

    for phrase in required_phrases:
        assert phrase in readme


def test_alpha_docs_reference_runnable_demo_and_dashboard() -> None:
    guide = Path("docs/WORKFORCE_RUNTIME_GUIDE.md").read_text()

    assert "workforce-runtime --db .workforce_runtime/demo.sqlite demo sample-repo-fix" in guide
    assert "workforce-runtime --db .workforce_runtime/demo.sqlite dashboard" in guide
    assert "examples/mock_worker/fix_parser_worker.py" in guide
