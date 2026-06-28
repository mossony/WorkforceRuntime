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

- The web dashboard is available, but dashboard UX and long-running task
  controls are still evolving.
- Dynamic hiring exists through runtime tools, but automated org restructuring
  remains limited.
- Scheduler and queue controls are still local-first and need more production
  hardening.
- No remote artifact store.
- No GitHub PR automation.
- Real Codex and Claude Code runs depend on local CLI installation and credentials.
- Provider-specific reasoning-detail continuity should be verified in smoke
  runs.

## Next Milestones

1. Harden scheduler behavior for long-running worker, model, and tool queues.
2. Add credential-aware Codex and Claude Code smoke demos.
3. Add richer manager review policies for retries, escalation, and human decision gates.
4. Add GitHub issue and pull request artifacts.
5. Add remote artifact storage.
6. Improve persistent run summaries for long-running work.
7. Expand dashboard controls for org changes and task steering.
