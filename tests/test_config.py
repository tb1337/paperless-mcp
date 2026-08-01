"""Tests for the environment-driven Settings loader and the CLI."""

from __future__ import annotations

import pytest

from paperless_mcp.__main__ import build_parser, main, resolve_settings
from paperless_mcp.config import ConfigError, Settings, load_settings

_ALL_ENV = (
    "PAPERLESS_URL",
    "PAPERLESS_TOKEN",
    "PAPERLESS_MCP_TRANSPORT",
    "PAPERLESS_MCP_AUTH_TOKEN",
    "PAPERLESS_MCP_HOST",
    "PAPERLESS_MCP_PORT",
    "PAPERLESS_MCP_READONLY",
    "PAPERLESS_MCP_ENABLE_DELETE",
    "PAPERLESS_MCP_MAX_FILE_BYTES",
    "PAPERLESS_MCP_VERIFY_SSL",
    "PAPERLESS_MCP_TIMEOUT",
    "PAPERLESS_MCP_LOG_LEVEL",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)


def test_load_settings_minimal(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless:8000")
    monkeypatch.setenv("PAPERLESS_TOKEN", "abc123")

    s = load_settings()
    assert isinstance(s, Settings)
    assert s.paperless_url == "http://paperless:8000"
    assert s.paperless_token == "abc123"
    assert s.auth_token is None
    assert s.transport == "stdio"
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.readonly is False
    assert s.enable_delete is False
    assert s.max_file_bytes == 25_000_000
    assert s.verify_ssl is True
    assert s.request_timeout == 30.0
    assert s.log_level == "INFO"


def test_load_settings_requires_url(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("PAPERLESS_TOKEN", "abc")
    with pytest.raises(ConfigError, match="PAPERLESS_URL"):
        load_settings()


def test_load_settings_requires_token(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless:8000")
    with pytest.raises(ConfigError, match="PAPERLESS_TOKEN"):
        load_settings()


def test_load_settings_rejects_a_bad_boolean(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")
    monkeypatch.setenv("PAPERLESS_MCP_READONLY", "maybe")
    with pytest.raises(ConfigError, match="boolean"):
        load_settings()


def test_load_settings_rejects_an_unknown_transport(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")
    monkeypatch.setenv("PAPERLESS_MCP_TRANSPORT", "carrier-pigeon")
    with pytest.raises(ConfigError, match="transport"):
        load_settings()


def test_load_settings_rejects_a_bad_port(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")
    monkeypatch.setenv("PAPERLESS_MCP_PORT", "70000")
    with pytest.raises(ConfigError, match="0-65535"):
        load_settings()


def test_env_reads_the_new_transport_settings(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")
    monkeypatch.setenv("PAPERLESS_MCP_TRANSPORT", "http")
    monkeypatch.setenv("PAPERLESS_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("PAPERLESS_MCP_VERIFY_SSL", "false")
    monkeypatch.setenv("PAPERLESS_MCP_TIMEOUT", "5.5")
    monkeypatch.setenv("PAPERLESS_MCP_LOG_LEVEL", "debug")

    s = load_settings()
    assert s.transport == "http"
    assert s.host == "0.0.0.0"
    assert s.verify_ssl is False
    assert s.request_timeout == 5.5
    assert s.log_level == "DEBUG"


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
    clean_env: None,
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


def test_cli_flags_win_over_the_environment(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://from-env")
    monkeypatch.setenv("PAPERLESS_TOKEN", "env-token")
    monkeypatch.setenv("PAPERLESS_MCP_PORT", "9000")

    s = resolve_settings(
        ["--url", "http://from-cli", "--port", "1234", "--http", "--env-file", "/nonexistent"]
    )
    assert s.paperless_url == "http://from-cli"
    assert s.paperless_token == "env-token"  # untouched by the CLI
    assert s.port == 1234
    assert s.transport == "http"


def test_cli_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")
    assert resolve_settings(["--env-file", "/nonexistent"]).transport == "stdio"


def test_cli_no_verify_ssl(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")
    s = resolve_settings(["--no-verify-ssl", "--env-file", "/nonexistent"])
    assert s.verify_ssl is False


def test_cli_reports_missing_configuration(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--env-file", "/nonexistent"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "PAPERLESS_URL" in captured.err
    assert captured.out == ""  # stdout is the JSON-RPC channel; keep it clean


def test_cli_parser_exposes_the_documented_flags() -> None:
    options = {action.dest for action in build_parser()._actions}
    assert {
        "transport",
        "paperless_url",
        "paperless_token",
        "host",
        "port",
        "auth_token",
        "readonly",
        "enable_delete",
        "max_file_bytes",
        "verify_ssl",
        "request_timeout",
        "log_level",
        "env_file",
    } <= options
