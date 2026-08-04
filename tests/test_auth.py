"""Bearer-token middleware.

Driven against a bare ASGI app rather than through ``build_app``: the middleware
knows nothing about Paperless, and testing it in isolation keeps these cases from
depending on the connection fakes.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from paperless_mcp.auth import BearerAuthMiddleware

_TOKEN = "secret"


def _client(token: str = _TOKEN) -> TestClient:
    async def ok(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app: Starlette = Starlette(routes=[Route("/mcp", ok), Route("/healthz", ok)])
    return TestClient(BearerAuthMiddleware(app, token, exempt_paths=("/healthz",)))


def test_a_valid_token_is_passed_through() -> None:
    with _client() as client:
        response = client.get("/mcp", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("headers", "case"),
    [
        ({}, "no header at all"),
        ({"Authorization": "Bearer nope"}, "wrong token"),
        ({"Authorization": _TOKEN}, "no scheme"),
        ({"Authorization": f"Basic {_TOKEN}"}, "wrong scheme"),
        ({"Authorization": "Bearer "}, "empty token"),
        # Sent as raw bytes because that is the only way it can reach a server:
        # Starlette then decodes the header as latin-1, and compare_digest
        # refuses to compare a str outside ASCII. So a str comparison raised
        # TypeError out of the middleware - a 500 with a traceback, from an
        # unauthenticated request.
        ({"Authorization": "Bearer ü".encode()}, "non-ASCII token"),
    ],
)
def test_an_unusable_authorization_header_is_a_401(
    headers: dict[str, str | bytes], case: str
) -> None:
    with _client() as client:
        response = client.get("/mcp", headers=headers)
    assert response.status_code == 401, case
    assert response.json()["error"] == "unauthorized"
    assert response.headers["WWW-Authenticate"].startswith("Bearer ")


def test_an_exempt_path_needs_no_token() -> None:
    with _client() as client:
        assert client.get("/healthz").status_code == 200


def test_an_empty_token_is_refused_at_construction() -> None:
    """An unauthenticated deployment must not advertise authentication."""
    with pytest.raises(ValueError, match="non-empty token"):
        BearerAuthMiddleware(Starlette(), "")
