# Roadmap

## Public Alpha Scope

The alpha proves the lifecycle:

- structured agent profiles, task contracts, reports, budgets, permissions, artifacts, and events
- YAML organization loading
- SQLite storage
- runtime task assignment and status updates
- MCP tools for reporting, assignment, discussion, hiring, prompt updates, and worker artifacts
- generic CLI worker adapter
- Codex and Claude Code adapter surfaces
- automatic manager review
- budget and permission enforcement
- HR hiring checks for headcount and token budget
- generated role-specific system prompts with manager edits
- text dashboard
- deterministic end-to-end demo

## Known Limitations

- No web dashboard yet.
- No dynamic hiring or org restructuring.
- No distributed scheduler.
- No remote artifact store.
- No GitHub PR automation.
- Real Codex and Claude Code runs depend on local CLI installation and credentials.
- Codex OpenRouter reasoning-detail continuity is provider-specific and should be verified in smoke runs.

## Next Milestones

1. Add a small scheduler that can dispatch queued tasks to matching adapters.
2. Add a real Codex demo that runs only when `codex` and `OPENROUTER_API_KEY` are available.
3. Add a real Claude Code demo once `claude` availability can be detected cleanly.
4. Add richer manager review policies for retries, escalation, and human decision gates.
5. Add GitHub issue and pull request artifacts.
6. Add persistent run summaries for long-running work.
7. Add a minimal web dashboard after the protocol is stable.
