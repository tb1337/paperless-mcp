"""Environment-driven configuration for paperless-mcp.

Every setting can be supplied through an environment variable (the canonical
way when the server is launched by an MCP client such as Claude Desktop) and
overridden by an explicit command-line flag.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Transport = Literal["stdio", "http"]

#: Accepted spellings mapped to their literal. Looking a raw string up here is
#: what narrows it to ``Transport``; ``TRANSPORTS`` is derived from the same
#: mapping so the accepted set and the error message cannot drift apart.
_TRANSPORT_LITERALS: Mapping[str, Transport] = {"stdio": "stdio", "http": "http"}

TRANSPORTS: frozenset[str] = frozenset(_TRANSPORT_LITERALS)

LOG_LEVELS: frozenset[str] = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _raw(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool_env(name: str, *, default: bool) -> bool:
    raw = _raw(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _int_env(name: str, *, default: int) -> int:
    raw = _raw(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float_env(name: str, *, default: float) -> float:
    raw = _raw(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved runtime configuration."""

    paperless_url: str
    paperless_token: str
    transport: Transport = "stdio"
    auth_token: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    readonly: bool = False
    enable_delete: bool = False
    max_file_bytes: int = 25_000_000
    verify_ssl: bool = True
    request_timeout: float = 30.0
    name_cache_ttl: float = 300.0
    log_level: str = "INFO"

    @property
    def expose_writes(self) -> bool:
        """Whether create/update tools should be registered."""
        return not self.readonly

    @property
    def expose_deletes(self) -> bool:
        """Whether delete tools should be registered."""
        return not self.readonly and self.enable_delete


def _pick(overrides: Mapping[str, Any], key: str, fallback: Any) -> Any:
    """Return the CLI override for *key*, or *fallback* when it was not supplied."""
    value = overrides.get(key)
    return fallback if value is None else value


def load_settings(overrides: Mapping[str, Any] | None = None) -> Settings:
    """Load configuration from the environment, applying CLI overrides on top.

    Args:
        overrides: Parsed command-line values. Keys map 1:1 to :class:`Settings`
            field names; a ``None`` value means "not supplied on the command
            line" and leaves the environment value (or default) in place.

    Raises:
        ConfigError: When a required value is missing or a value cannot be parsed.
    """
    over: Mapping[str, Any] = overrides or {}

    url = _pick(over, "paperless_url", _raw("PAPERLESS_URL"))
    if not url:
        raise ConfigError(
            "PAPERLESS_URL is required (or pass --url). "
            "Example: PAPERLESS_URL=https://paperless.example.com"
        )
    token = _pick(over, "paperless_token", _raw("PAPERLESS_TOKEN"))
    if not token:
        raise ConfigError(
            "PAPERLESS_TOKEN is required (or pass --token). "
            "Create one in Paperless-ngx under Settings -> API tokens."
        )

    raw_transport = str(_pick(over, "transport", _raw("PAPERLESS_MCP_TRANSPORT") or "stdio"))
    transport = _TRANSPORT_LITERALS.get(raw_transport.lower())
    if transport is None:
        raise ConfigError(
            f"Unknown transport {raw_transport.lower()!r}; expected one of {sorted(TRANSPORTS)}."
        )

    log_level = str(_pick(over, "log_level", _raw("PAPERLESS_MCP_LOG_LEVEL") or "INFO")).upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"Unknown log level {log_level!r}; expected one of {sorted(LOG_LEVELS)}.")

    settings = Settings(
        paperless_url=str(url),
        paperless_token=str(token),
        transport=transport,
        auth_token=_pick(over, "auth_token", _raw("PAPERLESS_MCP_AUTH_TOKEN")),
        # Binding to every interface is right inside a container but a poor
        # default for a locally spawned helper, so it stays opt-in.
        host=str(_pick(over, "host", _raw("PAPERLESS_MCP_HOST") or "127.0.0.1")),
        port=int(_pick(over, "port", _int_env("PAPERLESS_MCP_PORT", default=8000))),
        readonly=bool(_pick(over, "readonly", _bool_env("PAPERLESS_MCP_READONLY", default=False))),
        enable_delete=bool(
            _pick(over, "enable_delete", _bool_env("PAPERLESS_MCP_ENABLE_DELETE", default=False))
        ),
        max_file_bytes=int(
            _pick(
                over,
                "max_file_bytes",
                _int_env("PAPERLESS_MCP_MAX_FILE_BYTES", default=25_000_000),
            )
        ),
        verify_ssl=bool(
            _pick(over, "verify_ssl", _bool_env("PAPERLESS_MCP_VERIFY_SSL", default=True))
        ),
        request_timeout=float(
            _pick(over, "request_timeout", _float_env("PAPERLESS_MCP_TIMEOUT", default=30.0))
        ),
        name_cache_ttl=float(
            _pick(
                over,
                "name_cache_ttl",
                _float_env("PAPERLESS_MCP_NAME_CACHE_TTL", default=300.0),
            )
        ),
        log_level=log_level,
    )

    if not 0 <= settings.port <= 65535:
        raise ConfigError(f"PAPERLESS_MCP_PORT must be 0-65535, got {settings.port}")
    if settings.max_file_bytes <= 0:
        raise ConfigError("PAPERLESS_MCP_MAX_FILE_BYTES must be positive")
    if settings.request_timeout <= 0:
        raise ConfigError("PAPERLESS_MCP_TIMEOUT must be positive")
    if settings.name_cache_ttl < 0:
        raise ConfigError("PAPERLESS_MCP_NAME_CACHE_TTL must not be negative")

    return settings
