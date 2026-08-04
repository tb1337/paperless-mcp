"""Bearer-token middleware for the MCP HTTP endpoint."""

from __future__ import annotations

import hmac
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Reject requests that don't present the configured Bearer token.

    Plain ASGI rather than :class:`~starlette.middleware.base.BaseHTTPMiddleware`:
    Starlette documents the latter as unsuitable in front of a streaming
    response, and both transports this server offers stream. The check is four
    stateless lines, so it needs none of what the base class provides.

    The token is compared with :func:`hmac.compare_digest` to avoid timing
    side-channels.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        exempt_paths: Iterable[str] = (),
    ) -> None:
        """Store the expected token and the paths that skip authentication."""
        if not token:
            raise ValueError("BearerAuthMiddleware requires a non-empty token")
        self._app = app
        # Held as bytes: compare_digest refuses a str outside ASCII, and a
        # garbled header has to answer 401, never raise into a 500.
        self._token = token.encode()
        self._exempt = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass authenticated HTTP requests through; reject the rest with 401."""
        # The lifespan scope traverses the middleware stack too, and swallowing
        # it would leave the app unable to start.
        if scope["type"] != "http" or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": "unauthorized", "detail": "Missing or invalid bearer token."},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="paperless-mcp"'},
        )
        await response(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        if scope["path"] in self._exempt:
            return True
        header = Headers(scope=scope).get("authorization", "")
        scheme, _, value = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(
            value.strip().encode(), self._token
        )
