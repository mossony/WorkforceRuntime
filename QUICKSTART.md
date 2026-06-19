# Quickstart

This guide gets a new checkout to the public alpha demo.

## Requirements

- Python 3.11+
- Git
- `pytest` for the test suite
- Optional: official `codex` CLI for real Codex worker runs
- Optional: Claude Code CLI available as `claude` for real Claude worker runs

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

## Run The Mock Worker Demo

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite demo sample-repo-fix
```

The demo creates a small repository, assigns a no-tools planning task to the engineering manager, delegates a parser bug fix to the worker, registers test and diff artifacts through MCP, runs manager review, and prints a dashboard.

For a smaller trajectory-focused demo:

```bash
workforce-runtime --db .workforce_runtime/simple.sqlite demo simple-status
```

This demo uses a CEO and product manager routed to `openai/gpt-oss-120b:free`, a terminal worker routed to `poolside/laguna-xs.2:free`, a progress check, dashboard snapshots, event replay, and per-agent trajectories.

## Inspect The Dashboard

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --watch --iterations 5
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --replay
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --trajectories
```

The dashboard shows company context, active/completed/failed tasks, reports, artifacts, decision inbox, budget overruns, worker performance, and recent events.
When a worker is running, `dashboard --watch` also shows worker run state and recent stdout/stderr chunks streamed from the adapter.

## Inspect The Organization

```bash
workforce-runtime org print examples/simple_engineering_org/org.yaml
```

The sample org includes a CEO, VP Engineering, Engineering Manager, Codex worker, and Claude Code worker.

## Run Tests

```bash
python3 -m pytest -q
```

The public alpha path should pass without Codex or Claude Code installed because deterministic fake worker tests and a mock worker demo cover the lifecycle.

## Next Steps

- Read `MCP_TOOLS.md` before writing a worker that reports to the runtime.
- Read `WORKER_ADAPTERS.md` before connecting a new CLI agent.
- Read `docs/CODEX_AGENT_INTEGRATION.md` before using Codex with OpenRouter.
