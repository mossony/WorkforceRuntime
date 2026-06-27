from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from workforce_runtime.mcp.oauth import (
    discover_oauth_metadata,
    probe_mcp_auth,
    start_oauth_login,
    start_oauth_login_for_callback,
)
from workforce_runtime.mcp.server import MCPServer
from workforce_runtime.server.runtime import WorkforceRuntime


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")


def test_oauth_discovery_uses_mcp_well_known_path() -> None:
    fake_server = _FakeOAuthMCPHTTPServer()
    fake_server.start()
    try:
        metadata = discover_oauth_metadata(fake_server.url)
    finally:
        fake_server.stop()

    assert metadata is not None
    assert metadata.authorization_endpoint == fake_server.authorization_endpoint
    assert metadata.token_endpoint == fake_server.token_endpoint
    assert fake_server.discovery_paths[0] == "/.well-known/oauth-authorization-server/mcp"


def test_probe_reports_none_for_unauthenticated_mcp() -> None:
    fake_server = _FakeMCPHTTPServer()
    fake_server.start()
    try:
        result = probe_mcp_auth(fake_server.url)
    finally:
        fake_server.stop()

    assert result.auth_status == "none"


def test_oauth_login_stores_token_and_external_mcp_uses_it(tmp_path: Path, monkeypatch) -> None:
    token_store = tmp_path / "oauth_tokens.json"
    monkeypatch.setenv("WORKFORCE_EXTERNAL_MCP_OAUTH_STORE", str(token_store))
    fake_server = _FakeOAuthMCPHTTPServer()
    fake_server.start()
    try:
        handle = start_oauth_login(
            server_id="oauth_demo",
            url=fake_server.url,
            scopes=["tools.read"],
            open_browser=False,
        )
        with urlopen(handle.authorization_url, timeout=5) as response:
            assert response.status == 200
        login = handle.wait()

        config = {
            "external_mcp": {
                "enabled": True,
                "queue_calls": False,
                "servers": [
                    {
                        "id": "oauth_demo",
                        "enabled": True,
                        "transport": "http",
                        "url": fake_server.url,
                        "tool_prefix": "oauth_demo",
                        "auth": {"type": "oauth"},
                        "allowed_agent_ids": ["codex_worker"],
                        "allowed_tools": ["echo"],
                        "queue": {"enabled": False},
                        "tools": [{"name": "echo", "inputSchema": {"type": "object"}}],
                    }
                ],
            },
        }
        with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
            runtime.initialize_org(EXAMPLE_ORG)
            task = runtime.create_task(
                title="OAuth MCP test",
                objective="Use an OAuth-authenticated external MCP tool.",
                assign_to="codex_worker",
            )
            server = MCPServer(runtime, config=config, default_actor_id="codex_worker")
            response = _mcp_request(
                server,
                "tools/call",
                {
                    "name": "oauth_demo__echo",
                    "arguments": {
                        "from_agent_id": "codex_worker",
                        "task_id": task.task_id,
                        "message": "hello oauth",
                    },
                },
            )
    finally:
        fake_server.stop()

    assert login.client_id == "registered-client"
    assert token_store.exists()
    assert response["structuredContent"]["ok"] is True
    assert any(call["auth"] == "Bearer stored-access-token" for call in fake_server.calls if call["method"] == "tools/call")


def test_oauth_login_for_callback_uses_supplied_redirect_uri(tmp_path: Path, monkeypatch) -> None:
    token_store = tmp_path / "oauth_tokens.json"
    monkeypatch.setenv("WORKFORCE_EXTERNAL_MCP_OAUTH_STORE", str(token_store))
    fake_server = _FakeOAuthMCPHTTPServer()
    fake_server.start()
    try:
        handle = start_oauth_login_for_callback(
            server_id="oauth_dashboard",
            url=fake_server.url,
            callback_url="http://127.0.0.1:8765/api/settings/mcp/oauth/callback",
            scopes=["tools.read"],
        )
        login = handle.complete(code="auth-code", state=handle.state)
    finally:
        fake_server.stop()

    assert handle.redirect_uri.startswith("http://127.0.0.1:8765/api/settings/mcp/oauth/callback/")
    assert login.client_id == "registered-client"
    assert token_store.exists()


def test_external_mcp_clone_tool_uses_central_queue_and_bearer_auth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_server = _FakeMCPHTTPServer()
    fake_server.start()
    monkeypatch.setenv("EXTERNAL_MCP_TOKEN", "secret-token")
    config = {
        "external_mcp": {
            "enabled": True,
            "queue_calls": True,
            "servers": [
                {
                    "id": "demo",
                    "enabled": True,
                    "transport": "http",
                    "url": fake_server.url,
                    "tool_prefix": "demo",
                    "auth": {"type": "bearer", "token_env": "EXTERNAL_MCP_TOKEN"},
                    "allowed_agent_ids": ["codex_worker"],
                    "allowed_tools": ["echo"],
                    "queue": {"enabled": True},
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo a message.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        }
                    ],
                }
            ],
        },
        "queue": {
            "max_active_agents": 2,
            "lease_seconds": 30,
            "per_kind_limits": {"tool_call": 1},
            "per_tool_limits": {"demo__echo": 1},
        },
    }

    try:
        with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
            runtime.initialize_org(EXAMPLE_ORG)
            task = runtime.create_task(
                title="External MCP test",
                objective="Use a centrally proxied external MCP tool.",
                assign_to="codex_worker",
            )
            server = MCPServer(runtime, config=config, default_actor_id="codex_worker")

            tools = _mcp_request(server, "tools/list", {})
            tool_names = {tool["name"] for tool in tools["tools"]}
            assert "demo__echo" in tool_names

            response = _mcp_request(
                server,
                "tools/call",
                {
                    "name": "demo__echo",
                    "arguments": {
                        "from_agent_id": "codex_worker",
                        "task_id": task.task_id,
                        "message": "hello from worker",
                    },
                },
            )
            structured = response["structuredContent"]
            work_items = runtime.store.list_work_items()
            event_types = [event.event_type for event in runtime.store.list_events()]

        assert structured["ok"] is True
        assert structured["external_mcp"]["server_id"] == "demo"
        assert structured["remote_result"]["structuredContent"]["arguments"] == {"message": "hello from worker"}
        assert any(call["auth"] == "Bearer secret-token" for call in fake_server.calls if call["method"] == "tools/call")
        assert len(work_items) == 1
        assert work_items[0].tool_name == "demo__echo"
        assert work_items[0].status == "completed"
        assert "mcp_tool_call_queued" in event_types
        assert "external_mcp_tool_call_started" in event_types
        assert "external_mcp_tool_call_finished" in event_types
    finally:
        fake_server.stop()


def test_external_mcp_clone_tool_enforces_agent_allowlist(tmp_path: Path) -> None:
    fake_server = _FakeMCPHTTPServer()
    fake_server.start()
    config = {
        "external_mcp": {
            "enabled": True,
            "queue_calls": False,
            "servers": [
                {
                    "id": "demo",
                    "enabled": True,
                    "transport": "http",
                    "url": fake_server.url,
                    "tool_prefix": "demo",
                    "allowed_agent_ids": ["codex_worker"],
                    "allowed_tools": ["echo"],
                    "queue": {"enabled": False},
                    "tools": [{"name": "echo", "inputSchema": {"type": "object"}}],
                }
            ],
        },
    }
    try:
        with WorkforceRuntime(tmp_path / "runtime.sqlite") as runtime:
            runtime.initialize_org(EXAMPLE_ORG)
            server = MCPServer(runtime, config=config, default_actor_id="claude_worker")

            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "demo__echo",
                        "arguments": {"from_agent_id": "claude_worker", "message": "denied"},
                    },
                }
            )

        assert response is not None
        assert "error" in response
        assert "cannot use external MCP server demo" in response["error"]["message"]
        assert fake_server.calls == []
    finally:
        fake_server.stop()


def _mcp_request(server: MCPServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert response is not None
    assert "error" not in response, response.get("error")
    return response["result"]


class _FakeMCPHTTPServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                method = str(payload.get("method") or "")
                owner.calls.append(
                    {
                        "method": method,
                        "auth": self.headers.get("Authorization", ""),
                        "payload": payload,
                    }
                )
                if "id" not in payload:
                    self.send_response(200)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-external-mcp", "version": "0.1.0"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo a message.",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                elif method == "tools/call":
                    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                    result = {
                        "content": [{"type": "text", "text": "ok"}],
                        "structuredContent": {
                            "tool": params.get("name"),
                            "arguments": params.get("arguments") or {},
                        },
                        "isError": False,
                    }
                else:
                    self._write({"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32601, "message": method}})
                    return
                self._write({"jsonrpc": "2.0", "id": payload["id"], "result": result})

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _write(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Mcp-Session-Id", "fake-session")
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/mcp"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


class _FakeOAuthMCPHTTPServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.discovery_paths: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path.startswith("/.well-known/oauth-authorization-server"):
                    owner.discovery_paths.append(parsed.path)
                    self._write(
                        {
                            "issuer": owner.base_url,
                            "authorization_endpoint": owner.authorization_endpoint,
                            "token_endpoint": owner.token_endpoint,
                            "registration_endpoint": owner.registration_endpoint,
                            "scopes_supported": ["tools.read"],
                        }
                    )
                    return
                if parsed.path == "/authorize":
                    query = parse_qs(parsed.query)
                    redirect_uri = str((query.get("redirect_uri") or [""])[0])
                    state = str((query.get("state") or [""])[0])
                    location = f"{redirect_uri}?{urlencode({'code': 'auth-code', 'state': state})}"
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.end_headers()
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                parsed = urlparse(self.path)
                if parsed.path == "/register":
                    self._write({"client_id": "registered-client"})
                    return
                if parsed.path == "/token":
                    body = parse_qs(raw)
                    grant_type = str((body.get("grant_type") or [""])[0])
                    token = "stored-access-token" if grant_type == "authorization_code" else "refreshed-access-token"
                    self._write(
                        {
                            "access_token": token,
                            "refresh_token": "refresh-token",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                            "scope": "tools.read",
                        }
                    )
                    return
                if parsed.path != "/mcp":
                    self.send_response(404)
                    self.end_headers()
                    return
                auth = self.headers.get("Authorization", "")
                if auth != "Bearer stored-access-token":
                    body = json.dumps({"error": "missing bearer"}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", "Bearer")
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                payload = json.loads(raw)
                method = str(payload.get("method") or "")
                owner.calls.append({"method": method, "auth": auth, "payload": payload})
                if "id" not in payload:
                    self.send_response(200)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "oauth-mcp", "version": "0.1.0"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo a message.",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                elif method == "tools/call":
                    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                    result = {
                        "content": [{"type": "text", "text": "ok"}],
                        "structuredContent": {
                            "tool": params.get("name"),
                            "arguments": params.get("arguments") or {},
                            "ok": True,
                        },
                        "isError": False,
                    }
                else:
                    self._write({"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32601, "message": method}})
                    return
                self._write({"jsonrpc": "2.0", "id": payload["id"], "result": result})

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _write(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Mcp-Session-Id", "fake-oauth-session")
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def url(self) -> str:
        return f"{self.base_url}/mcp"

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.base_url}/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.base_url}/token"

    @property
    def registration_endpoint(self) -> str:
        return f"{self.base_url}/register"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
