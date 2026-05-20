"""Tests for the environment-driven Settings loader."""

from __future__ import annotations

import pytest

from paperless_mcp.config import ConfigError, Settings, load_settings


def test_load_settings_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless:8000")
    monkeypatch.setenv("PAPERLESS_TOKEN", "abc123")
    for k in (
        "PAPERLESS_MCP_AUTH_TOKEN",
        "PAPERLESS_MCP_READONLY",
        "PAPERLESS_MCP_ENABLE_DELETE",
        "PAPERLESS_MCP_PORT",
        "PAPERLESS_MCP_MAX_FILE_BYTES",
    ):
        monkeypatch.delenv(k, raising=False)

    s = load_settings()
    assert isinstance(s, Settings)
    assert s.paperless_url == "http://paperless:8000"
    assert s.paperless_token == "abc123"
    assert s.auth_token is None
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.readonly is False
    assert s.enable_delete is False
    assert s.max_file_bytes == 25_000_000


def test_load_settings_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAPERLESS_URL", raising=False)
    monkeypatch.setenv("PAPERLESS_TOKEN", "abc")
    with pytest.raises(ConfigError, match="PAPERLESS_URL"):
        load_settings()


def test_load_settings_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless:8000")
    monkeypatch.delenv("PAPERLESS_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="PAPERLESS_TOKEN"):
        load_settings()


@pytest.mark.parametrize(
    ("readonly", "enable_delete", "expect_writes", "expect_deletes"),
    [
        ("false", "false", True, False),
        ("false", "true", True, True),
        ("true", "false", False, False),
        ("true", "true", False, False),  # readonly wins
    ],
)
def test_visibility_flags(
    monkeypatch: pytest.MonkeyPatch,
    readonly: str,
    enable_delete: str,
    expect_writes: bool,
    expect_deletes: bool,
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")
    monkeypatch.setenv("PAPERLESS_MCP_READONLY", readonly)
    monkeypatch.setenv("PAPERLESS_MCP_ENABLE_DELETE", enable_delete)
    s = load_settings()
    assert s.expose_writes is expect_writes
    assert s.expose_deletes is expect_deletes
