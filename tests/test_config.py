"""Tests for the environment-driven Settings loader and the CLI."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from paperless_mcp.__main__ import build_parser, main, resolve_settings
from paperless_mcp.config import (
    _ENV_SETTINGS,
    ENV_VARS,
    ConfigError,
    Settings,
    load_settings,
)

#: Derived, so a new setting cannot be added without this fixture clearing it.
#: Hand-listing it here is how PAPERLESS_MCP_NAME_CACHE_TTL came to be missed.
_ALL_ENV = ENV_VARS


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://x")
    monkeypatch.setenv("PAPERLESS_TOKEN", "y")


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


def test_a_cli_flag_wins_over_an_unparsable_environment_variable(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    """The flag overrides the variable, so the variable is never parsed.

    Parsing every source eagerly made this abort with "must be an integer",
    contradicting --help's promise that flags win over the environment.
    """
    monkeypatch.setenv("PAPERLESS_MCP_PORT", "abc")
    assert resolve_settings(["--port", "9000", "--env-file", "/nonexistent"]).port == 9000


def test_an_unparsable_variable_still_fails_when_nothing_overrides_it(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_PORT", "abc")
    with pytest.raises(ConfigError, match="must be an integer"):
        load_settings()


@pytest.mark.parametrize(
    ("overrides", "field", "expected"),
    [
        ({"readonly": "false"}, "readonly", False),
        ({"readonly": "no"}, "readonly", False),
        ({"readonly": True}, "readonly", True),
        ({"verify_ssl": "off"}, "verify_ssl", False),
        ({"port": "1234"}, "port", 1234),
        ({"request_timeout": "2.5"}, "request_timeout", 2.5),
        ({"log_level": "debug"}, "log_level", "DEBUG"),
        ({"transport": "HTTP"}, "transport", "http"),
        # An already-typed value passes through rather than round-tripping
        # through str(), which is what a CLI flag always supplies.
        ({"port": 1234}, "port", 1234),
        ({"request_timeout": 2.5}, "request_timeout", 2.5),
        ({"name_cache_ttl": 15}, "name_cache_ttl", 15.0),
        ({"auth_token": "  shared-secret  "}, "auth_token", "shared-secret"),
        # An empty token means "no authentication", the same as an unset one.
        ({"auth_token": "   "}, "auth_token", None),
    ],
)
def test_an_override_is_parsed_like_an_environment_value(
    configured: None, overrides: dict[str, object], field: str, expected: object
) -> None:
    """``bool("false")`` is True, so re-coercing an override inverted it."""
    assert getattr(load_settings(overrides), field) == expected


def test_an_empty_auth_token_variable_means_no_authentication(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    """.env ships PAPERLESS_MCP_AUTH_TOKEN= as the documented way to opt out."""
    monkeypatch.setenv("PAPERLESS_MCP_AUTH_TOKEN", "")
    assert load_settings().auth_token is None
    monkeypatch.setenv("PAPERLESS_MCP_AUTH_TOKEN", " shared-secret ")
    assert load_settings().auth_token == "shared-secret"


def test_a_key_that_is_not_a_setting_is_refused(configured: None) -> None:
    """A typo used to be dropped silently, leaving the default in place."""
    with pytest.raises(ConfigError, match="Not a setting: reedonly"):
        load_settings({"reedonly": True})


def test_vars_aliases_the_namespace_so_the_overrides_must_be_a_copy() -> None:
    """Pins why resolve_settings builds a dict instead of popping env_file.

    ``vars(args)`` *is* ``args.__dict__``, so removing a key from it deletes the
    parsed attribute. ``resolve_settings`` reads ``args.env_file`` after building
    the overrides, which a pop would have made impossible.
    """
    args = build_parser().parse_args(["--env-file", "/nonexistent"])
    assert vars(args) is args.__dict__


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("PAPERLESS_MCP_PORT", "abc", "must be an integer"),
        ("PAPERLESS_MCP_PORT", "-1", "0-65535"),
        ("PAPERLESS_MCP_TIMEOUT", "fast", "must be a number"),
        ("PAPERLESS_MCP_TIMEOUT", "0", "TIMEOUT must be positive"),
        ("PAPERLESS_MCP_MAX_FILE_BYTES", "0", "MAX_FILE_BYTES must be positive"),
        ("PAPERLESS_MCP_NAME_CACHE_TTL", "-1", "must not be negative"),
        ("PAPERLESS_MCP_LOG_LEVEL", "chatty", "Unknown log level"),
        ("PAPERLESS_MCP_READONLY", "maybe", "must be a boolean"),
    ],
)
def test_a_bad_value_names_the_variable_and_the_expectation(
    monkeypatch: pytest.MonkeyPatch, configured: None, variable: str, value: str, message: str
) -> None:
    """These messages are what an operator sees for a broken .env."""
    monkeypatch.setenv(variable, value)
    with pytest.raises(ConfigError, match=message):
        load_settings()


def test_every_setting_is_reachable_from_the_environment() -> None:
    """The settings table and the dataclass are one list, or the env misses a knob."""
    required = {"paperless_url", "paperless_token"}
    assert {setting.field for setting in _ENV_SETTINGS} | required == {
        field.name for field in fields(Settings)
    }


def test_a_settings_default_is_declared_once() -> None:
    """Both the dataclass field and the settings table must read one constant."""
    defaults = {
        field.name: field.default for field in fields(Settings) if field.name not in _REQUIRED
    }
    assert {setting.field: setting.default for setting in _ENV_SETTINGS} == defaults


_REQUIRED = ("paperless_url", "paperless_token")


def test_env_example_documents_every_variable() -> None:
    """CLAUDE.md: anything user-facing lands in .env.example in the same change."""
    text = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
    assert [name for name in ENV_VARS if f"{name}=" not in text] == []


def test_cli_reports_a_taken_port_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, configured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(_settings: Settings) -> None:
        raise OSError(98, "Address already in use")

    monkeypatch.setattr("paperless_mcp.__main__.serve", refuse)
    exit_code = main(["--http", "--port", "8123", "--env-file", "/nonexistent"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "cannot start on 127.0.0.1:8123" in captured.err
    assert "Address already in use" in captured.err
    assert captured.out == ""
