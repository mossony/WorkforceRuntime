"""Helpers for connecting external worker CLIs to the Workforce MCP server.

Without this wiring a worker CLI (codex / claude) starts with zero Workforce
MCP tools, so the agent cannot call assign()/report()/get_task_dossier() and
therefore cannot delegate or report through the runtime. The spawned MCP server
is scoped to the calling agent via the WORKFORCE_* environment variables.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from workforce_runtime.workers.base import RuntimeContext

WORKFORCE_MCP_SERVER_NAME = "workforce"


def workforce_mcp_env(runtime_context: RuntimeContext) -> dict[str, str]:
    return {
        # Absolute: the MCP server runs with cwd=workspace, so a relative path
        # would open a phantom empty DB instead of the real runtime DB.
        "WORKFORCE_RUNTIME_DB": str(Path(runtime_context.db_path).resolve()),
        "WORKFORCE_AGENT_ID": runtime_context.agent_id,
        "WORKFORCE_MANAGER_ID": runtime_context.manager_id or "",
    }


def workforce_mcp_server_spec(runtime_context: RuntimeContext) -> dict[str, object]:
    """Standard MCP server spec (command/args/env) for the Workforce server."""
    return {
        "command": sys.executable or "python3",
        "args": ["-m", "workforce_runtime", "mcp", "serve"],
        "env": workforce_mcp_env(runtime_context),
    }


def claude_mcp_config_json(runtime_context: RuntimeContext) -> str:
    """JSON string for the claude CLI `--mcp-config` flag."""
    return json.dumps({"mcpServers": {WORKFORCE_MCP_SERVER_NAME: workforce_mcp_server_spec(runtime_context)}})
