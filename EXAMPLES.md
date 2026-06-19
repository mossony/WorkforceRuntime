# Examples

## Sample Engineering Organization

`examples/simple_engineering_org/org.yaml` defines a six-agent engineering org:

- CEO
- HR Manager
- VP Engineering
- Engineering Manager
- Codex Worker
- Claude Worker

Each agent has a manager, worker type, responsibilities, permissions, budget, and generated system prompt. The company also declares a headcount limit and token budget used by HR hiring.

Print the org chart:

```bash
workforce-runtime org print examples/simple_engineering_org/org.yaml
```

## Mock Worker Demo

Run:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite demo sample-repo-fix
```

What happens:

1. The demo creates a temporary sample repo with a failing boolean parser test.
2. A no-tools planning task is assigned to `engineering_manager`.
3. A tool task is assigned to `codex_worker`.
4. The mock worker edits `parser.py`, runs `pytest`, writes a test log, writes a git diff, and reports through MCP.
5. Manager review accepts the completed report.
6. The demo prints artifact paths, final status, worker return code, and the dashboard.

The mock worker lives at `examples/mock_worker/fix_parser_worker.py`. It is deterministic and does not require Codex or Claude Code.

## Simple Status Demo

Run:

```bash
workforce-runtime --db .workforce_runtime/simple.sqlite demo simple-status
```

What happens:

1. A tiny org is loaded from `examples/simple_status_org/org.yaml`.
2. CEO and Product Manager agents are routed to `openai/gpt-oss-120b:free`.
3. The terminal worker is routed to `poolside/laguna-xs.2:free`.
4. The human assigns a simple launch-note goal to the CEO.
5. The CEO delegates to the Product Manager.
6. The Product Manager assigns the terminal worker.
7. The Product Manager calls `check_progress`.
8. The worker writes `launch_note.md`, submits it as an artifact, and reports completion.
9. The demo prints live dashboard snapshots, event replay, and per-agent trajectories.

## Codex Worker Example

`examples/codex_worker_task/` is a placeholder workspace for real Codex runs. The current adapter can run official Codex against any isolated git workspace and captures:

- task contract JSON
- stdout JSONL
- stderr
- final message
- git diff

Use `docs/CODEX_AGENT_INTEGRATION.md` before running real Codex with OpenRouter.

## Claude Code Worker Example

`examples/claude_worker_task/` is a placeholder workspace for real Claude Code runs. The adapter expects a `claude` executable on `PATH` and captures:

- task contract JSON
- stdout JSON
- stderr
- final message
- git diff
