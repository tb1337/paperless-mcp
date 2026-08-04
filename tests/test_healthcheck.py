"""The container healthcheck.

Never imported by the rest of the suite, which is how it sat at 0 % covered: it is
reached only through the image's ``HEALTHCHECK`` line, where a wrong answer either
restarts a healthy container or hides a dead one.
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from typing import Any

import pytest

from paperless_mcp import healthcheck
from paperless_mcp.config import DEFAULT_HOST, DEFAULT_PORT


class _Response:
    """The context manager ``urlopen`` returns, narrowed to what is read."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the URLs probed and answer 200, unless a test says otherwise."""
    urls: list[str] = []

    def urlopen(url: str, timeout: float = 0) -> Any:
        urls.append(url)
        return _Response(200)

    monkeypatch.delenv("PAPERLESS_MCP_HOST", raising=False)
    monkeypatch.delenv("PAPERLESS_MCP_PORT", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return urls


def test_the_default_target_matches_the_server_defaults(probe: list[str]) -> None:
    """The probe and the server have to agree, or a healthy container looks dead."""
    assert healthcheck.main() == 0
    assert probe == [f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/healthz"]


def test_the_configured_host_and_port_are_used(
    monkeypatch: pytest.MonkeyPatch, probe: list[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HOST", " paperless-mcp ")
    monkeypatch.setenv("PAPERLESS_MCP_PORT", "9123")

    assert healthcheck.main() == 0
    assert probe == ["http://paperless-mcp:9123/healthz"]


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "*"])
def test_a_bind_all_host_is_probed_on_loopback(
    monkeypatch: pytest.MonkeyPatch, probe: list[str], host: str
) -> None:
    """A bind-all address is not connectable, so probing it would always fail."""
    monkeypatch.setenv("PAPERLESS_MCP_HOST", host)

    assert healthcheck.main() == 0
    assert probe == [f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/healthz"]


def test_an_empty_host_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, probe: list[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HOST", "   ")

    assert healthcheck.main() == 0
    assert probe == [f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/healthz"]


def test_an_unusable_port_is_unhealthy_rather_than_a_traceback(
    monkeypatch: pytest.MonkeyPatch, probe: list[str]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_PORT", "not-a-port")

    assert healthcheck.main() == 1
    assert probe == []


def test_a_non_200_answer_is_unhealthy(monkeypatch: pytest.MonkeyPatch, probe: list[str]) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: _Response(503))

    assert healthcheck.main() == 1


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("connection refused"),
        OSError("no route to host"),
        # The two that used to escape as a traceback into `docker inspect`.
        http.client.HTTPException("truncated response"),
        ValueError("unknown url type"),
    ],
    ids=["url-error", "os-error", "http-exception", "value-error"],
)
def test_every_probe_failure_is_unhealthy_rather_than_a_traceback(
    monkeypatch: pytest.MonkeyPatch, probe: list[str], error: Exception
) -> None:
    def raising(url: str, timeout: float = 0) -> Any:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", raising)

    assert healthcheck.main() == 1
