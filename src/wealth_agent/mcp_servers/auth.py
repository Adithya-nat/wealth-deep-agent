"""OAuth 2.1 for the Robinhood MCP servers, from a headless Python process.

Robinhood's MCP endpoints are built for interactive agent *clients* — Claude
Code, ChatGPT, Cursor — which handle OAuth for you. A standalone agent process
has to do it itself. Probing the endpoint tells you exactly what it wants::

    $ curl -i https://agent.robinhood.com/mcp/trading
    HTTP/2 401
    www-authenticate: Bearer resource_metadata="https://agent.robinhood.com/
                      .well-known/oauth-protected-resource/mcp/trading"

    $ curl .../.well-known/oauth-authorization-server
    {"authorization_endpoint": "https://robinhood.com/oauth",
     "registration_endpoint": "https://agent.robinhood.com/oauth/trading/register",
     "token_endpoint": "https://api.robinhood.com/oauth2/token/",
     "grant_types_supported": ["authorization_code", "refresh_token"],
     "code_challenge_methods_supported": ["S256"],
     "token_endpoint_auth_methods_supported": ["none"]}

That is textbook OAuth 2.1: a public client, PKCE required, and Dynamic Client
Registration so there is no client ID to obtain by hand. The MCP SDK's
:class:`~mcp.client.auth.OAuthClientProvider` implements the whole dance and is
itself an ``httpx.Auth``, which is exactly what
``StreamableHttpConnection(auth=...)`` accepts. So all this module has to supply
is the two pieces the SDK deliberately leaves to the application: somewhere to
persist tokens, and a way to complete the browser round-trip.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socket
import urllib.parse
import webbrowser
from pathlib import Path

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from wealth_agent.config import AUTH_DIR

#: Fixed port so the redirect URI stays stable across runs. Dynamic Client
#: Registration binds the redirect URI at registration time, so a random port
#: would force a re-registration on every login.
CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"

_SUCCESS_PAGE = b"""<!doctype html>
<title>Connected</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h2>Robinhood connected</h2>
<p>You can close this tab and return to the terminal.</p>
</body>"""


class FileTokenStorage(TokenStorage):
    """Persist tokens and DCR client credentials to a 0600 file.

    Stored outside the repository (``~/.wealth-agent/``) rather than in a
    gitignored directory: a ``.gitignore`` typo should not be able to publish a
    refresh token that can read someone's brokerage account.
    """

    def __init__(self, server_name: str) -> None:
        AUTH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path: Path = AUTH_DIR / f"{server_name}.json"

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            # A truncated cache is not worth crashing over; re-authenticating
            # is cheap and the alternative is an unrecoverable error state.
            return {}

    def _write(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, indent=2))
        self._path.chmod(0o600)

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = info.model_dump(mode="json", exclude_none=True)
        self._write(data)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)

    @property
    def path(self) -> Path:
        return self._path


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """One-shot handler that captures ``?code=...&state=...`` and stops."""

    result: dict[str, str | None] = {}

    def do_GET(self) -> None:  # noqa: N802 — name fixed by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return
        params = urllib.parse.parse_qs(parsed.query)
        if "error" in params:
            _CallbackHandler.result = {
                "error": params["error"][0],
                "error_description": params.get("error_description", [""])[0],
            }
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authorization failed. Check the terminal.")
            return
        _CallbackHandler.result = {
            "code": params.get("code", [""])[0],
            "state": params.get("state", [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_SUCCESS_PAGE)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log."""


def _serve_once(timeout: float) -> dict[str, str | None]:
    """Block until the browser hits the callback, or ``timeout`` elapses."""
    _CallbackHandler.result = {}
    try:
        server = http.server.HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    except OSError as exc:  # port already bound
        msg = (
            f"Cannot bind localhost:{CALLBACK_PORT} for the OAuth callback "
            f"({exc}). Close whatever is using that port and retry — the port "
            f"is fixed because it is baked into the registered redirect URI."
        )
        raise RuntimeError(msg) from exc

    server.timeout = timeout
    with server:
        # handle_request() honours server.timeout and returns either way, so a
        # user who abandons the browser tab gets an error instead of a hang.
        server.handle_request()
    return _CallbackHandler.result


def build_oauth_provider(server_name: str, server_url: str) -> OAuthClientProvider:
    """Build an ``httpx.Auth`` that transparently authenticates MCP requests.

    Args:
        server_name: Short slug used to name the token cache file.
        server_url: Base MCP endpoint, e.g. the trading server URL.

    Returns:
        A provider that registers the client on first use, runs the browser
        authorization-code flow, caches the result, and silently refreshes
        thereafter.
    """
    storage = FileTokenStorage(server_name)

    async def redirect_handler(authorization_url: str) -> None:
        print(f"\n  Opening browser to authorize `{server_name}`...")
        print(f"  If nothing opens, visit:\n    {authorization_url}\n")
        # Robinhood requires a desktop browser for this step.
        webbrowser.open(authorization_url)

    async def callback_handler() -> tuple[str, str | None]:
        result = await asyncio.to_thread(_serve_once, 300.0)
        if not result:
            msg = "Timed out waiting for the OAuth callback (5 minutes)."
            raise RuntimeError(msg)
        if "error" in result:
            msg = (
                f"Authorization was denied: {result['error']} "
                f"{result.get('error_description', '')}".strip()
            )
            raise RuntimeError(msg)
        return result["code"] or "", result.get("state")

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name="wealth-deep-agent",
            redirect_uris=[REDIRECT_URI],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


def logout(server_name: str) -> Path:
    """Delete the cached tokens for one server. Returns the path removed."""
    storage = FileTokenStorage(server_name)
    storage.clear()
    return storage.path


def callback_port_is_free() -> bool:
    """True if the fixed OAuth callback port can currently be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("localhost", CALLBACK_PORT))
        except OSError:
            return False
    return True


__all__ = [
    "REDIRECT_URI",
    "FileTokenStorage",
    "build_oauth_provider",
    "callback_port_is_free",
    "logout",
]
