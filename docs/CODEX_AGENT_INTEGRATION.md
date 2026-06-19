# Codex Agent Integration

This note records the intended Workforce Runtime boundary for Codex workers.

## Decision

Use the official `codex` CLI as the worker process. Workforce Runtime should not implement a Codex-like agent loop. The Codex adapter should spawn `codex exec` noninteractively, pass a generated task prompt, capture process output, collect artifacts, and receive structured status through Workforce Runtime MCP tools once the MCP phase exists.

## Invocation Shape

The Codex adapter should prefer stdin for long prompts:

```bash
codex \
  --profile workforce-openrouter \
  -a never \
  -s workspace-write \
  exec \
  --json \
  --output-last-message artifacts/<task_id>/codex-final.md \
  --cd <isolated_workspace> \
  -
```

Recommended adapter behavior:

- Generate a prompt from `TaskContract`, agent profile, manager id, acceptance criteria, budget, permissions, and MCP reporting instructions.
- Run Codex in an isolated workspace or git worktree.
- Capture stdout as JSONL when `--json` is enabled.
- Capture stderr as the human progress log.
- Persist the final answer from `--output-last-message`.
- Collect `git diff`, test logs, and any declared artifact paths.
- Treat nonzero exit status, missing report, timeout, or missing artifact as runtime-level failures.

## Model Provider

Codex ignores provider/auth settings in project-local `.codex/config.toml`, so
the OpenRouter configuration lives in a user-level Codex profile:

`~/.codex/workforce-openrouter.config.toml`

```toml
model = "openai/gpt-oss-120b:free"
model_provider = "openrouter"

[model_providers.openrouter]
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
```

The adapter should invoke Codex with `--profile workforce-openrouter` before
the `exec` subcommand. Approval and sandbox flags are also safest before
`exec`, for example `codex --profile workforce-openrouter -a never -s
workspace-write exec ...`.
`OPENROUTER_API_KEY` is expected to come from the shell environment, such as
`.zshrc`. Do not write the API key into repository files.

Codex supports custom model providers and can talk to providers compatible with OpenAI model APIs. OpenRouter's documented example uses Chat Completions with `reasoning: {"enabled": true}`. Codex's public config documents provider URL, env key, headers, query params, model, and Codex reasoning settings, but does not document a provider-specific arbitrary JSON body override for that OpenRouter `reasoning` field. The initial adapter should verify the model in a smoke run before relying on reasoning details continuity.

## Model Context Limits

OpenRouter model context windows are model metadata, not Workforce Runtime
budgets. OpenRouter request `max_tokens` / `max_completion_tokens` only limits
how many output tokens a response may generate; it does not enlarge the model
context window.

Workforce Runtime keeps known OpenRouter model limits in
`examples/openrouter_models.json` and the matching default registry in
`workforce_runtime/config/model_registry.py`. Generated agent prompts include a
short model-limit note when the assigned model is known, for example
`openai/gpt-oss-120b:free` has a 131,072-token context window and
`poolside/laguna-m.1:free` has a 262,144-token context window with up to 32,768
output tokens.

Codex itself should still manage its own compaction and request shaping. The
runtime-provided model-limit note is an operating hint for planning context,
large artifacts, and delegation. It should not be treated as a hard budget;
agent/task/company `max_tokens` fields remain spending and allocation controls.

## Later MCP Wiring

After Phase 5, Codex should be launched with the Workforce Runtime MCP server configured in Codex config for the task workspace. The task prompt should require the worker to call:

- `update_status` when work starts or blocks
- `submit_artifact` for diffs, logs, and generated files
- `report` before claiming completion

Until MCP exists, the adapter can rely on captured final output and collected files only.
