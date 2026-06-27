from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


OAUTH_DISCOVERY_HEADER = "MCP-Protocol-Version"
OAUTH_DISCOVERY_VERSION = "2024-11-05"
DEFAULT_OAUTH_TIMEOUT_SECONDS = 300
WORKFORCE_HTTP_USER_AGENT = "Workforce Runtime/0.1.0"


@dataclass(frozen=True)
class OAuthMetadata:
    authorization_endpoint: str
    token_endpoint: str
    issuer: str = ""
    registration_endpoint: str = ""
    scopes_supported: tuple[str, ...] = ()


@dataclass(frozen=True)
class MCPAuthProbeResult:
    url: str
    auth_status: str
    oauth_metadata: OAuthMetadata | None = None
    www_authenticate: str = ""
    error: str = ""


@dataclass(frozen=True)
class OAuthLoginResult:
    server_id: str
    url: str
    client_id: str
    token_path: Path
    scopes: tuple[str, ...]
    expires_at: float | None


class OAuthLoginHandle:
    def __init__(
        self,
        *,
        authorization_url: str,
        callback_server: _CallbackServer,
        exchange: _TokenExchange,
    ) -> None:
        self.authorization_url = authorization_url
        self._callback_server = callback_server
        self._exchange = exchange

    def wait(self) -> OAuthLoginResult:
        try:
            callback = self._callback_server.wait()
            if callback.state != self._exchange.state:
                raise RuntimeError("OAuth callback state did not match the expected state")
            token_payload = exchange_authorization_code(self._exchange, callback.code)
            token_record = _stored_token_record(self._exchange, token_payload)
            save_oauth_tokens(self._exchange.server_id, self._exchange.url, token_record)
            return OAuthLoginResult(
                server_id=self._exchange.server_id,
                url=self._exchange.url,
                client_id=self._exchange.client_id,
                token_path=oauth_token_store_path(),
                scopes=tuple(str(item) for item in token_record.get("scope", "").split() if item),
                expires_at=token_record.get("expires_at") if isinstance(token_record.get("expires_at"), float | int) else None,
            )
        finally:
            self._callback_server.close()


class OAuthCallbackLoginHandle:
    def __init__(
        self,
        *,
        authorization_url: str,
        callback_id: str,
        exchange: _TokenExchange,
    ) -> None:
        self.authorization_url = authorization_url
        self.callback_id = callback_id
        self.state = exchange.state
        self.redirect_uri = exchange.redirect_uri
        self._exchange = exchange

    def complete(self, *, code: str, state: str) -> OAuthLoginResult:
        if state != self._exchange.state:
            raise RuntimeError("OAuth callback state did not match the expected state")
        if not code:
            raise RuntimeError("OAuth callback was missing code")
        token_payload = exchange_authorization_code(self._exchange, code)
        token_record = _stored_token_record(self._exchange, token_payload)
        save_oauth_tokens(self._exchange.server_id, self._exchange.url, token_record)
        return OAuthLoginResult(
            server_id=self._exchange.server_id,
            url=self._exchange.url,
            client_id=self._exchange.client_id,
            token_path=oauth_token_store_path(),
            scopes=tuple(str(item) for item in token_record.get("scope", "").split() if item),
            expires_at=token_record.get("expires_at") if isinstance(token_record.get("expires_at"), float | int) else None,
        )


@dataclass(frozen=True)
class _OAuthCallback:
    code: str
    state: str


@dataclass(frozen=True)
class _TokenExchange:
    server_id: str
    url: str
    metadata: OAuthMetadata
    redirect_uri: str
    code_verifier: str
    state: str
    client_id: str
    client_secret: str = ""
    scopes: tuple[str, ...] = ()
    resource: str = ""
    timeout_seconds: float = 30.0


class _CallbackServer:
    def __init__(self, *, bind_host: str, callback_port: int | None, callback_path: str, timeout_seconds: int) -> None:
        address = (bind_host, callback_port or 0)
        self._server = HTTPServer(address, _OAuthCallbackHandler)
        self._server.callback_path = callback_path  # type: ignore[attr-defined]
        self._server.callback_result = None  # type: ignore[attr-defined]
        self._server.callback_event = threading.Event()  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._timeout_seconds = timeout_seconds
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def wait(self) -> _OAuthCallback:
        event = self._server.callback_event  # type: ignore[attr-defined]
        if not event.wait(self._timeout_seconds):
            raise TimeoutError("timed out waiting for OAuth callback")
        result = self._server.callback_result  # type: ignore[attr-defined]
        if not isinstance(result, dict):
            raise RuntimeError("OAuth callback did not produce a result")
        if result.get("error"):
            description = result.get("error_description") or result.get("error")
            raise RuntimeError(f"OAuth provider returned an error: {description}")
        code = str(result.get("code") or "")
        state = str(result.get("state") or "")
        if not code or not state:
            raise RuntimeError("OAuth callback was missing code or state")
        return _OAuthCallback(code=code, state=state)

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        expected_path = self.server.callback_path  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        if parsed.path != expected_path:
            self._write(404, "Invalid OAuth callback path.")
            return
        query = parse_qs(parsed.query)
        result = {
            "code": _first_query_value(query, "code"),
            "state": _first_query_value(query, "state"),
            "error": _first_query_value(query, "error"),
            "error_description": _first_query_value(query, "error_description"),
        }
        self.server.callback_result = result  # type: ignore[attr-defined]
        self.server.callback_event.set()  # type: ignore[attr-defined]
        if result.get("error"):
            self._write(400, "OAuth login failed. You can close this window.")
        else:
            self._write(200, "Authentication complete. You can close this window.")

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def probe_mcp_auth(url: str, *, timeout_seconds: float = 5.0) -> MCPAuthProbeResult:
    metadata = discover_oauth_metadata(url, timeout_seconds=timeout_seconds)
    if metadata is not None:
        return MCPAuthProbeResult(url=url, auth_status="oauth", oauth_metadata=metadata)
    try:
        _mcp_initialize_without_auth(url, timeout_seconds=timeout_seconds)
        return MCPAuthProbeResult(url=url, auth_status="none")
    except HTTPError as exc:
        www_authenticate = exc.headers.get("WWW-Authenticate", "")
        if exc.code in {401, 403}:
            auth_status = "bearer_required" if "bearer" in www_authenticate.lower() else "unknown_auth"
            return MCPAuthProbeResult(
                url=url,
                auth_status=auth_status,
                www_authenticate=www_authenticate,
                error=f"HTTP {exc.code}",
            )
        return MCPAuthProbeResult(url=url, auth_status="error", www_authenticate=www_authenticate, error=f"HTTP {exc.code}")
    except (OSError, URLError) as exc:
        return MCPAuthProbeResult(url=url, auth_status="error", error=str(exc))


def discover_oauth_metadata(url: str, *, timeout_seconds: float = 5.0) -> OAuthMetadata | None:
    for discovery_url in oauth_discovery_urls(url):
        request = Request(
            discovery_url,
            headers={
                OAUTH_DISCOVERY_HEADER: OAUTH_DISCOVERY_VERSION,
                "Accept": "application/json",
                "User-Agent": WORKFORCE_HTTP_USER_AGENT,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    continue
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        authorization_endpoint = str(payload.get("authorization_endpoint") or "")
        token_endpoint = str(payload.get("token_endpoint") or "")
        if not authorization_endpoint or not token_endpoint:
            continue
        scopes = payload.get("scopes_supported") or []
        return OAuthMetadata(
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            issuer=str(payload.get("issuer") or ""),
            registration_endpoint=str(payload.get("registration_endpoint") or ""),
            scopes_supported=tuple(str(item).strip() for item in scopes if str(item).strip()),
        )
    return None


def oauth_discovery_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    base_path = parsed.path.strip("/")
    canonical_path = "/.well-known/oauth-authorization-server"
    paths = [canonical_path]
    if base_path:
        paths = [
            f"{canonical_path}/{base_path}",
            f"/{base_path}/.well-known/oauth-authorization-server",
            canonical_path,
        ]
    urls: list[str] = []
    for path in paths:
        candidate = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
        if candidate not in urls:
            urls.append(candidate)
    return urls


def start_oauth_login(
    *,
    server_id: str,
    url: str,
    metadata: OAuthMetadata | None = None,
    scopes: list[str] | None = None,
    client_id: str = "",
    client_secret: str = "",
    resource: str = "",
    callback_port: int | None = None,
    callback_url: str = "",
    timeout_seconds: int = DEFAULT_OAUTH_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> OAuthLoginHandle:
    resolved_metadata = metadata or discover_oauth_metadata(url)
    if resolved_metadata is None:
        raise RuntimeError(f"MCP server {url} does not advertise OAuth metadata")
    callback_id = _callback_id_from_url(url)
    bind_host = _callback_bind_host(callback_url)
    callback_path = _callback_path(callback_url, callback_id)
    callback_server = _CallbackServer(
        bind_host=bind_host,
        callback_port=callback_port,
        callback_path=callback_path,
        timeout_seconds=timeout_seconds,
    )
    redirect_uri = _redirect_uri(callback_url, bind_host, callback_server.port, callback_id)
    registered_client = _resolve_oauth_client(
        metadata=resolved_metadata,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        timeout_seconds=timeout_seconds,
    )
    code_verifier = _code_verifier()
    code_challenge = _code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)
    resolved_scopes = tuple(scopes or resolved_metadata.scopes_supported or ())
    authorization_url = _authorization_url(
        resolved_metadata,
        client_id=registered_client["client_id"],
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
        scopes=resolved_scopes,
        resource=resource,
    )
    exchange = _TokenExchange(
        server_id=server_id,
        url=url,
        metadata=resolved_metadata,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        state=state,
        client_id=registered_client["client_id"],
        client_secret=registered_client.get("client_secret", ""),
        scopes=resolved_scopes,
        resource=resource,
        timeout_seconds=float(timeout_seconds),
    )
    if open_browser:
        if not webbrowser.open(authorization_url):
            print(f"Browser launch failed. Open this URL manually:\n{authorization_url}")
    return OAuthLoginHandle(
        authorization_url=authorization_url,
        callback_server=callback_server,
        exchange=exchange,
    )


def start_oauth_login_for_callback(
    *,
    server_id: str,
    url: str,
    callback_url: str,
    metadata: OAuthMetadata | None = None,
    scopes: list[str] | None = None,
    client_id: str = "",
    client_secret: str = "",
    resource: str = "",
    timeout_seconds: int = DEFAULT_OAUTH_TIMEOUT_SECONDS,
) -> OAuthCallbackLoginHandle:
    if not callback_url:
        raise ValueError("callback_url is required for dashboard OAuth login")
    resolved_metadata = metadata or discover_oauth_metadata(url)
    if resolved_metadata is None:
        raise RuntimeError(f"MCP server {url} does not advertise OAuth metadata")
    callback_id = _callback_id_from_url(url)
    redirect_uri = _redirect_uri(callback_url, "127.0.0.1", 0, callback_id)
    registered_client = _resolve_oauth_client(
        metadata=resolved_metadata,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        timeout_seconds=timeout_seconds,
    )
    code_verifier = _code_verifier()
    code_challenge = _code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)
    resolved_scopes = tuple(scopes or resolved_metadata.scopes_supported or ())
    authorization_url = _authorization_url(
        resolved_metadata,
        client_id=registered_client["client_id"],
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
        scopes=resolved_scopes,
        resource=resource,
    )
    exchange = _TokenExchange(
        server_id=server_id,
        url=url,
        metadata=resolved_metadata,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        state=state,
        client_id=registered_client["client_id"],
        client_secret=registered_client.get("client_secret", ""),
        scopes=resolved_scopes,
        resource=resource,
        timeout_seconds=float(timeout_seconds),
    )
    return OAuthCallbackLoginHandle(
        authorization_url=authorization_url,
        callback_id=callback_id,
        exchange=exchange,
    )


def perform_oauth_login(
    *,
    server_id: str,
    url: str,
    metadata: OAuthMetadata | None = None,
    scopes: list[str] | None = None,
    client_id: str = "",
    client_secret: str = "",
    resource: str = "",
    callback_port: int | None = None,
    callback_url: str = "",
    timeout_seconds: int = DEFAULT_OAUTH_TIMEOUT_SECONDS,
    open_browser: bool = True,
) -> OAuthLoginResult:
    handle = start_oauth_login(
        server_id=server_id,
        url=url,
        metadata=metadata,
        scopes=scopes,
        client_id=client_id,
        client_secret=client_secret,
        resource=resource,
        callback_port=callback_port,
        callback_url=callback_url,
        timeout_seconds=timeout_seconds,
        open_browser=open_browser,
    )
    print(f"Authorize `{server_id}` by opening this URL in your browser:\n{handle.authorization_url}\n")
    return handle.wait()


def exchange_authorization_code(exchange: _TokenExchange, code: str) -> dict[str, Any]:
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": exchange.redirect_uri,
        "client_id": exchange.client_id,
        "code_verifier": exchange.code_verifier,
    }
    if exchange.client_secret:
        body["client_secret"] = exchange.client_secret
    return _post_form(exchange.metadata.token_endpoint, body, timeout_seconds=exchange.timeout_seconds)


def oauth_access_token(
    server_id: str,
    url: str,
    auth: dict[str, Any] | None,
    *,
    timeout_seconds: float = 30.0,
) -> str:
    auth = auth or {}
    if auth.get("access_token_env"):
        return _required_env(auth, "access_token_env", server_id)
    token_url = str(auth.get("token_url") or "")
    if token_url and auth.get("client_id_env") and auth.get("client_secret_env"):
        return _client_credentials_token(server_id, auth, timeout_seconds=timeout_seconds)
    stored = load_oauth_tokens(server_id, url)
    if stored is None:
        raise ValueError(
            f"no OAuth token stored for external MCP server {server_id}; run `workforce-runtime mcp external login {server_id}`"
        )
    expires_at = stored.get("expires_at")
    access_token = str(stored.get("access_token") or "")
    if access_token and (not isinstance(expires_at, float | int) or expires_at > time.time() + 30):
        return access_token
    refreshed = refresh_oauth_tokens(server_id, url, stored, auth, timeout_seconds=timeout_seconds)
    return str(refreshed.get("access_token") or "")


def refresh_oauth_tokens(
    server_id: str,
    url: str,
    stored: dict[str, Any],
    auth: dict[str, Any] | None,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    refresh_token = str(stored.get("refresh_token") or "")
    token_endpoint = str(stored.get("token_endpoint") or "")
    if not refresh_token or not token_endpoint:
        raise ValueError(f"OAuth token for external MCP server {server_id} is expired and cannot be refreshed")
    auth = auth or {}
    client_id = str(auth.get("client_id") or stored.get("client_id") or "")
    if auth.get("client_id_env"):
        client_id = _required_env(auth, "client_id_env", server_id)
    client_secret = str(stored.get("client_secret") or "")
    if auth.get("client_secret_env"):
        client_secret = _required_env(auth, "client_secret_env", server_id)
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        body["client_secret"] = client_secret
    refreshed = _post_form(token_endpoint, body, timeout_seconds=timeout_seconds)
    merged = {**stored, **refreshed}
    if "refresh_token" not in refreshed:
        merged["refresh_token"] = refresh_token
    token_record = _token_record_from_payload(
        server_id=server_id,
        url=url,
        metadata_token_endpoint=token_endpoint,
        client_id=client_id,
        client_secret=client_secret,
        token_payload=merged,
    )
    save_oauth_tokens(server_id, url, token_record)
    return token_record


def load_oauth_tokens(server_id: str, url: str) -> dict[str, Any] | None:
    store = _read_token_store()
    value = store.get(_token_store_key(server_id, url))
    return value if isinstance(value, dict) else None


def save_oauth_tokens(server_id: str, url: str, token_record: dict[str, Any]) -> None:
    path = oauth_token_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store = _read_token_store()
    store[_token_store_key(server_id, url)] = token_record
    path.write_text(json.dumps(store, indent=2, sort_keys=True))
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def oauth_token_store_path() -> Path:
    configured = os.environ.get("WORKFORCE_EXTERNAL_MCP_OAUTH_STORE")
    if configured:
        return Path(configured)
    return Path(".workforce_runtime") / "secrets" / "external_mcp_oauth.json"


def _mcp_initialize_without_auth(url: str, *, timeout_seconds: float) -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": OAUTH_DISCOVERY_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "workforce-runtime", "version": "0.1.0"},
            },
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": WORKFORCE_HTTP_USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        response.read()


def _resolve_oauth_client(
    *,
    metadata: OAuthMetadata,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    timeout_seconds: float,
) -> dict[str, str]:
    if client_id:
        return {"client_id": client_id, **({"client_secret": client_secret} if client_secret else {})}
    if not metadata.registration_endpoint:
        raise ValueError("OAuth server does not provide dynamic registration; configure a client_id")
    registration = {
        "client_name": "Workforce Runtime",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    request = Request(
        metadata.registration_endpoint,
        data=json.dumps(registration).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": WORKFORCE_HTTP_USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    resolved_client_id = str(payload.get("client_id") or "")
    if not resolved_client_id:
        raise ValueError("OAuth dynamic registration did not return client_id")
    resolved = {"client_id": resolved_client_id}
    if payload.get("client_secret"):
        resolved["client_secret"] = str(payload["client_secret"])
    return resolved


def _authorization_url(
    metadata: OAuthMetadata,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scopes: tuple[str, ...],
    resource: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    if resource:
        params["resource"] = resource
    separator = "&" if urlparse(metadata.authorization_endpoint).query else "?"
    return metadata.authorization_endpoint + separator + urlencode(params)


def _stored_token_record(exchange: _TokenExchange, token_payload: dict[str, Any]) -> dict[str, Any]:
    return _token_record_from_payload(
        server_id=exchange.server_id,
        url=exchange.url,
        metadata_token_endpoint=exchange.metadata.token_endpoint,
        client_id=exchange.client_id,
        client_secret=exchange.client_secret,
        token_payload={
            **token_payload,
            **({"scope": " ".join(exchange.scopes)} if exchange.scopes and not token_payload.get("scope") else {}),
        },
    )


def _token_record_from_payload(
    *,
    server_id: str,
    url: str,
    metadata_token_endpoint: str,
    client_id: str,
    client_secret: str,
    token_payload: dict[str, Any],
) -> dict[str, Any]:
    access_token = str(token_payload.get("access_token") or "")
    if not access_token:
        raise ValueError(f"OAuth token response for {server_id} did not include access_token")
    expires_in = _float(token_payload.get("expires_in"), default=0.0)
    record = {
        "server_id": server_id,
        "url": url,
        "client_id": client_id,
        "token_endpoint": metadata_token_endpoint,
        "access_token": access_token,
        "token_type": str(token_payload.get("token_type") or "Bearer"),
        "refresh_token": str(token_payload.get("refresh_token") or ""),
        "scope": str(token_payload.get("scope") or ""),
        "expires_at": time.time() + expires_in if expires_in else None,
        "created_at": time.time(),
    }
    if client_secret:
        record["client_secret"] = client_secret
    return record


def _client_credentials_token(server_id: str, auth: dict[str, Any], *, timeout_seconds: float) -> str:
    token_url = str(auth.get("token_url") or "")
    client_id = _required_env(auth, "client_id_env", server_id)
    client_secret = _required_env(auth, "client_secret_env", server_id)
    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if auth.get("scope"):
        body["scope"] = " ".join(str(item) for item in auth.get("scope", []))
    payload = _post_form(token_url, body, timeout_seconds=timeout_seconds)
    token = str(payload.get("access_token") or "")
    if not token:
        raise ValueError(f"OAuth client credentials response for {server_id} did not include access_token")
    return token


def _post_form(url: str, body: dict[str, str], *, timeout_seconds: float) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(body).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": WORKFORCE_HTTP_USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_token_store() -> dict[str, Any]:
    path = oauth_token_store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _token_store_key(server_id: str, url: str) -> str:
    return hashlib.sha256(f"{server_id}\n{url}".encode("utf-8")).hexdigest()


def _redirect_uri(callback_url: str, bind_host: str, port: int, callback_id: str) -> str:
    base = callback_url or f"http://{bind_host}:{port}/callback"
    parsed = urlparse(base)
    path = parsed.path or "/callback"
    if path.endswith("/"):
        path = f"{path}{callback_id}"
    else:
        path = f"{path}/{callback_id}"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _callback_path(callback_url: str, callback_id: str) -> str:
    parsed = urlparse(callback_url or "http://127.0.0.1/callback")
    path = parsed.path or "/callback"
    return f"{path.rstrip('/')}/{callback_id}"


def _callback_bind_host(callback_url: str) -> str:
    if not callback_url:
        return "127.0.0.1"
    host = urlparse(callback_url).hostname
    return "127.0.0.1" if host in {None, "localhost", "127.0.0.1", "::1"} else "0.0.0.0"


def _callback_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()[:9]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _first_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""


def _required_env(auth: dict[str, Any], key: str, server_id: str) -> str:
    env_name = str(auth.get(key) or "")
    if not env_name:
        raise ValueError(f"external MCP server {server_id} auth.{key} is required")
    value = os.environ.get(env_name)
    if not value:
        raise ValueError(f"environment variable {env_name} is required for external MCP server {server_id}")
    return value


def _float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
