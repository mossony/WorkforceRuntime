# Repository Guidelines

## Project Structure

```
workforce_runtime/        # Main source package
├── core/               # Agent profiles, tasks, budgets, permissions, events
├── workers/            # CLI, Codex, and Claude Code adapters
├── mcp/                # MCP server implementation
├── server/             # Runtime engine (task assignment, reporting)
├── dashboard/          # Text and web dashboard renderers
├── evals/              # Benchmark and evaluation tools
└── config/             # Runtime configuration

tests/                  # Test files (pytest)
examples/               # Demo orgs, benchmarks, worker configs
docs/                   # Additional documentation
```

## Build, Test, and Development Commands

```bash
# Install dependencies
uv venv && source .venv/bin/activate && uv sync --extra dev

# Run all tests
pytest

# Run specific test file
pytest tests/test_runtime.py

# Run the CLI tool
workforce-runtime --db .workforce_runtime/demo.sqlite demo sample-repo-fix
```

## Coding Style & Naming Conventions

- **Language**: Python 3.11+
- **Indentation**: 4 spaces (standard Python)
- **Type hints**: Required on all public functions
- **Modules**: `snake_case` (e.g., `org_designer.py`)
- **Classes**: `PascalCase` (e.g., `AgentProfile`, `TaskContract`)
- **Tests**: Prefix with `test_` (e.g., `test_runtime.py`)

No explicit linter configured; follow standard Python conventions.

## Testing Guidelines

- **Framework**: pytest (v8.0+)
- **Location**: `tests/` directory
- **Naming**: Test files match source modules (e.g., `test_org_designer.py` for `org_designer.py`)
- **Run**: `pytest` from root directory
- Tests use `.sqlite` temporary databases for isolation

## Commit & Pull Request Guidelines

- **Messages**: Concise present-tense descriptions (e.g., "Add dashboard long RFC demo launcher")
- **Format**: Single sentence summary, no conventional prefixes required
- **PRs**: Include clear description of organizational/runtime changes, reference related issues

## Key Entry Points

- `__main__.py`: CLI entry point (`workforce-runtime` command)
- `server/runtime.py`: Core runtime logic
- `mcp/server.py`: Worker-facing MCP tools

## Configuration

- `pyproject.toml`: Project metadata and dependencies
- `workforce_runtime_config.json`: Runtime settings (models, dashboard, workers)
- `examples/workforce_runtime_config.json`: Template for customization
