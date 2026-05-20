"""Bearer-token middleware for the MCP HTTP endpoint."""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't present the configured Bearer token.

    The token is compared with :func:`hmac.compare_digest` to avoid timing
    side-channels.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:  # noqa: D107
        super().__init__(app)
        if not token:
            raise ValueError("BearerAuthMiddleware requires a non-empty token")
        self._token = token

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        """Validate the Bearer token; reject with 401 if missing or wrong."""
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip(), self._token):
            return JSONResponse(
                {"error": "unauthorized", "detail": "Missing or invalid bearer token."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="paperless-mcp"'},
            )
        return await call_next(request)
