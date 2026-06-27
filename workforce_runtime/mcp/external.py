from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workforce_runtime.mcp.oauth import WORKFORCE_HTTP_USER_AGENT, oauth_access_token
from workforce_runtime.server.runtime import WorkforceRuntime


RESERVED_WORKFORCE_ARGUMENTS = {
    "_workforce",
    "from_agent_id",
    "agent_id",
    "caller_id",
    "requested_by",
    "task_id",
    "thread_id",
}

@dataclass(frozen=True)
class ExternalMCPServerConfig:
    id: str
    url: str
    enabled: bool = True
    transport: str = "http"
    tool_prefix: str = ""
    auth: dict[str, Any] | None = None
    allowed_agent_ids: tuple[str, ...] = ()
    allowed_roles: tuple[str, ...] = ()
    allowed_departments: tuple[str, ...] = ()
    allowed_worker_types: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ("*",)
    timeout_seconds: float = 30.0
    queue_enabled: bool = True
    static_tools: tuple[dict[str, Any], ...] = ()
    headers: dict[str, str] | None = None

    @property
    def exposed_prefix(self) -> str:
        return self.tool_prefix or self.id


@dataclass(frozen=True)
class ResolvedExternalMCPTool:
    server: ExternalMCPServerConfig
    remote_tool_name: str
    exposed_tool_name: str


class ExternalMCPRegistry:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.servers = _load_external_mcp_servers(config)

    def resolve(self, exposed_tool_name: str) -> ResolvedExternalMCPTool | None:
        for server in self.servers:
            prefix = f"{server.exposed_prefix}__"
            if exposed_tool_name.startswith(prefix):
                remote_name = exposed_tool_name[len(prefix) :]
                if _tool_is_allowed(server, remote_name):
                    return ResolvedExternalMCPTool(
                        server=server,
                        remote_tool_name=remote_name,
                        exposed_tool_name=exposed_tool_name,
                    )
        return None

    def tool_specs(self, runtime: WorkforceRuntime, *, actor_id: str = "") -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for server in self.servers:
            if not _actor_is_allowed(runtime, server, actor_id):
                continue
            try:
                tools = list(server.static_tools) or HTTPMCPClient(server).list_tools()
            except Exception as exc:
                runtime.record_event(
                    event_type="external_mcp_tools_list_failed",
                    actor_id=actor_id or "runtime",
                    payload={"server_id": server.id, "error": _clip(str(exc))},
                )
                continue
            for tool in tools:
                remote_name = str(tool.get("name") or "")
                if not remote_name or not _tool_is_allowed(server, remote_name):
                    continue
                schema = tool.get("inputSchema") or tool.get("input_schema") or {"type": "object"}
                if not isinstance(schema, dict):
                    schema = {"type": "object"}
                specs.append(
                    {
                        "name": f"{server.exposed_prefix}__{remote_name}",
                        "description": _external_tool_description(server, tool),
                        "inputSchema": schema,
                    }
                )
        return specs

    def execute(
        self,
        runtime: WorkforceRuntime,
        resolved: ResolvedExternalMCPTool,
        arguments: dict[str, object],
        *,
        actor_id: str,
        task_id: str | None,
    ) -> dict[str, object]:
        if not _actor_is_allowed(runtime, resolved.server, actor_id):
            raise PermissionError(
                f"agent {actor_id} cannot use external MCP server {resolved.server.id}"
            )
        runtime.record_event(
            event_type="external_mcp_tool_call_started",
            actor_id=actor_id,
            task_id=task_id,
            payload={
                "server_id": resolved.server.id,
                "tool_name": resolved.remote_tool_name,
                "exposed_tool_name": resolved.exposed_tool_name,
            },
        )
        forwarded_arguments = _forwarded_arguments(arguments)
        result = HTTPMCPClient(resolved.server).call_tool(resolved.remote_tool_name, forwarded_arguments)
        structured: dict[str, object] = {
            "ok": True,
            "external_mcp": {
                "server_id": resolved.server.id,
                "tool_name": resolved.remote_tool_name,
                "exposed_tool_name": resolved.exposed_tool_name,
            },
            "remote_result": result,
        }
        runtime.record_event(
            event_type="external_mcp_tool_call_finished",
            actor_id=actor_id,
            task_id=task_id,
            payload={
                "server_id": resolved.server.id,
                "tool_name": resolved.remote_tool_name,
                "exposed_tool_name": resolved.exposed_tool_name,
            },
        )
        return structured


class HTTPMCPClient:
    def __init__(self, server: ExternalMCPServerConfig) -> None:
        if server.transport != "http":
            raise ValueError(f"unsupported external MCP transport for {server.id}: {server.transport}")
        self.server = server
        self._session_id = ""
        self._request_id = 0

    def list_tools(self) -> list[dict[str, Any]]:
        self._initialize()
        result = self._post_jsonrpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self._initialize()
        result = self._post_jsonrpc("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict):
            return result
        return {"result": result}

    def _initialize(self) -> None:
        if self._session_id:
            return
        try:
            self._post_jsonrpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "workforce-runtime", "version": "0.1.0"},
                },
            )
        except Exception:
            self._session_id = ""
            raise
        try:
            self._post_notification("notifications/initialized", {})
        except Exception:
            pass

    def _post_jsonrpc(self, method: str, params: dict[str, object]) -> Any:
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        response = self._post(payload, expect_response=True)
        if "error" in response:
            error = response["error"]
            raise RuntimeError(f"external MCP {self.server.id} {method} failed: {error}")
        return response.get("result", {})

    def _post_notification(self, method: str, params: dict[str, object]) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params}, expect_response=False)

    def _post(self, payload: dict[str, object], *, expect_response: bool) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": WORKFORCE_HTTP_USER_AGENT,
            **(self.server.headers or {}),
            **_auth_headers(self.server),
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = Request(self.server.url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.server.timeout_seconds) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                raw = response.read().decode("utf-8")
                if not raw.strip() and not expect_response:
                    return {}
                return _parse_http_mcp_response(raw, response.headers.get("Content-Type", ""))
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {_clip(error_body, 1000)}") from exc


def _load_external_mcp_servers(config: dict[str, Any]) -> list[ExternalMCPServerConfig]:
    external = config.get("external_mcp") if isinstance(config.get("external_mcp"), dict) else {}
    if not bool(external.get("enabled", True)):
        return []
    default_queue_enabled = bool(external.get("default_queue_enabled", True))
    servers: list[ExternalMCPServerConfig] = []
    for item in external.get("servers") or []:
        if not isinstance(item, dict):
            continue
        server_id = str(item.get("id") or "").strip()
        url = str(item.get("url") or "").strip()
        if not server_id or not url:
            continue
        queue = item.get("queue") if isinstance(item.get("queue"), dict) else {}
        servers.append(
            ExternalMCPServerConfig(
                id=server_id,
                url=url,
                enabled=bool(item.get("enabled", True)),
                transport=str(item.get("transport") or "http"),
                tool_prefix=str(item.get("tool_prefix") or server_id),
                auth=item.get("auth") if isinstance(item.get("auth"), dict) else None,
                allowed_agent_ids=_string_tuple(item.get("allowed_agent_ids"), default=("*",)),
                allowed_roles=_string_tuple(item.get("allowed_roles")),
                allowed_departments=_string_tuple(item.get("allowed_departments")),
                allowed_worker_types=_string_tuple(item.get("allowed_worker_types")),
                allowed_tools=_string_tuple(item.get("allowed_tools"), default=("*",)),
                timeout_seconds=_float(item.get("timeout_seconds"), default=30.0),
                queue_enabled=bool(queue.get("enabled", default_queue_enabled)),
                static_tools=tuple(tool for tool in item.get("tools", []) if isinstance(tool, dict)),
                headers={str(k): str(v) for k, v in dict(item.get("headers") or {}).items()},
            )
        )
    return [server for server in servers if server.enabled]


def _actor_is_allowed(runtime: WorkforceRuntime, server: ExternalMCPServerConfig, actor_id: str) -> bool:
    if actor_id in {"", "human", "system", "runtime"}:
        unrestricted_ids = not server.allowed_agent_ids or "*" in server.allowed_agent_ids
        return unrestricted_ids and not (
            server.allowed_roles or server.allowed_departments or server.allowed_worker_types
        )
    agent = runtime.store.get_agent(actor_id)
    if agent is None:
        return False
    if server.allowed_agent_ids and "*" not in server.allowed_agent_ids and actor_id not in server.allowed_agent_ids:
        return False
    if server.allowed_roles and agent.role not in server.allowed_roles:
        return False
    if server.allowed_departments and agent.department not in server.allowed_departments:
        return False
    if server.allowed_worker_types and agent.worker_type not in server.allowed_worker_types:
        return False
    return True


def _has_actor_restrictions(server: ExternalMCPServerConfig) -> bool:
    unrestricted_agents = not server.allowed_agent_ids or "*" in server.allowed_agent_ids
    return not unrestricted_agents or bool(server.allowed_roles or server.allowed_departments or server.allowed_worker_types)


def _tool_is_allowed(server: ExternalMCPServerConfig, tool_name: str) -> bool:
    return "*" in server.allowed_tools or tool_name in server.allowed_tools


def _auth_headers(server: ExternalMCPServerConfig) -> dict[str, str]:
    auth = server.auth or {}
    auth_type = str(auth.get("type") or "none").lower()
    if auth_type in {"", "none"}:
        return {}
    if auth_type == "bearer":
        token = _required_env(auth, "token_env", server.id)
        return {"Authorization": f"Bearer {token}"}
    if auth_type in {"oauth", "oauth2"}:
        token = _oauth_access_token(server)
        token_type = str(auth.get("token_type") or "Bearer")
        return {"Authorization": f"{token_type} {token}"}
    if auth_type == "header":
        header_name = str(auth.get("header") or "")
        token = _required_env(auth, "value_env", server.id)
        if not header_name:
            raise ValueError(f"external MCP server {server.id} auth.header is required")
        return {header_name: token}
    raise ValueError(f"unsupported auth type for external MCP server {server.id}: {auth_type}")


def _oauth_access_token(server: ExternalMCPServerConfig) -> str:
    return oauth_access_token(
        server.id,
        server.url,
        server.auth,
        timeout_seconds=server.timeout_seconds,
    )


def _required_env(auth: dict[str, Any], key: str, server_id: str) -> str:
    env_name = str(auth.get(key) or "")
    if not env_name:
        raise ValueError(f"external MCP server {server_id} auth.{key} is required")
    value = os.environ.get(env_name)
    if not value:
        raise ValueError(f"environment variable {env_name} is required for external MCP server {server_id}")
    return value


def _forwarded_arguments(arguments: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in arguments.items() if key not in RESERVED_WORKFORCE_ARGUMENTS}


def _parse_http_mcp_response(raw: str, content_type: str) -> dict[str, Any]:
    compact = raw.strip()
    if not compact:
        return {}
    if "text/event-stream" in content_type or compact.startswith("event:") or compact.startswith("data:"):
        for block in compact.split("\n\n"):
            data_lines = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
            if not data_lines:
                continue
            data = "\n".join(data_lines)
            if data == "[DONE]":
                continue
            return json.loads(data)
        return {}
    return json.loads(compact)


def _external_tool_description(server: ExternalMCPServerConfig, tool: dict[str, Any]) -> str:
    description = str(tool.get("description") or "External MCP tool.")
    return f"[External MCP: {server.id}] {description}"


def _string_tuple(value: object, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    return default


def _float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(text: str, limit: int = 500) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
