"""Environment-driven configuration for paperless-mcp.

Every setting can be supplied through an environment variable (the canonical
way when the server is launched by an MCP client such as Claude Desktop) and
overridden by an explicit command-line flag.

Each setting is declared once, in :data:`_ENV_SETTINGS`: the field it fills, the
variable it reads and the parser that narrows it. Only the source that wins is
parsed, so a malformed variable cannot fail a run that overrides it on the
command line.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any, Final, Literal

Transport = Literal["stdio", "http"]

#: Accepted spellings mapped to their literal. Looking a raw string up here is
#: what narrows it to ``Transport``; ``TRANSPORTS`` is derived from the same
#: mapping so the accepted set and the error message cannot drift apart.
_TRANSPORT_LITERALS: Final[Mapping[str, Transport]] = {"stdio": "stdio", "http": "http"}

#: Ordered by severity rather than alphabetically, so ``--help`` reads sensibly.
LOG_LEVELS: Final[tuple[str, ...]] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

TRANSPORTS: Final[tuple[str, ...]] = tuple(_TRANSPORT_LITERALS)

#: Referenced by both the :class:`Settings` field and its :data:`_ENV_SETTINGS`
#: entry, so the default exists once. ``healthcheck.py`` reads the same two.
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8000

#: Unauthenticated liveness endpoint. Declared here because ``server.py`` routes
#: it and ``healthcheck.py`` probes it: the two must agree, and a settings module
#: is the one place both already import (``healthcheck.py`` needs it to stay
#: stdlib-only on import, which this module is).
HEALTH_PATH: Final = "/healthz"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _raw(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_str(name: str, value: object) -> str:
    del name
    return str(value).strip()


def _parse_optional_str(name: str, value: object) -> str | None:
    return _parse_str(name, value) or None


def _parse_bool(name: str, value: object) -> bool:
    # A CLI flag already arrives as a bool; only the environment (and an
    # in-process caller passing strings) needs the spellings below.
    if isinstance(value, bool):
        return value
    lowered = _parse_str(name, value).lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {value!r}")


def _parse_int(name: str, value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(_parse_str(name, value))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def _parse_float(name: str, value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    try:
        return float(_parse_str(name, value))
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def _parse_transport(name: str, value: object) -> Transport:
    del name
    raw = _parse_str("", value).lower()
    transport = _TRANSPORT_LITERALS.get(raw)
    if transport is None:
        raise ConfigError(f"Unknown transport {raw!r}; expected one of {sorted(TRANSPORTS)}.")
    return transport


def _parse_log_level(name: str, value: object) -> str:
    del name
    level = _parse_str("", value).upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"Unknown log level {level!r}; expected one of {sorted(LOG_LEVELS)}.")
    return level


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved runtime configuration."""

    paperless_url: str
    paperless_token: str
    transport: Transport = "stdio"
    auth_token: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
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


@dataclass(frozen=True, slots=True)
class _EnvSetting[T]:
    """A :class:`Settings` field that can also be supplied through the environment.

    Args:
        field: The :class:`Settings` field this fills, and the override key.
        env: The variable read when no override was supplied.
        parse: Narrows a raw value, raising :class:`ConfigError` on a bad one.
        default: Used when neither source supplied anything.
    """

    field: str
    env: str
    parse: Callable[[str, object], T]
    default: T

    def value(self, overrides: Mapping[str, object]) -> T:
        """Return the winning value: CLI override, else environment, else default.

        Only the winning source is parsed. Parsing all of them eagerly would let
        a malformed variable abort a run that overrode it on the command line.
        """
        override = overrides.get(self.field)
        if override is not None:
            return self.parse(self.env, override)
        raw = _raw(self.env)
        return self.default if raw is None else self.parse(self.env, raw)


#: Explicitly parameterized: inferring the type variable from the default would
#: widen it to ``str`` and lose the ``Transport`` narrowing the parser performs.
_TRANSPORT: Final = _EnvSetting[Transport](
    "transport", "PAPERLESS_MCP_TRANSPORT", _parse_transport, "stdio"
)
_AUTH_TOKEN: Final = _EnvSetting(
    "auth_token", "PAPERLESS_MCP_AUTH_TOKEN", _parse_optional_str, None
)
# Binding to every interface is right inside a container but a poor default for
# a locally spawned helper, so it stays opt-in.
_HOST: Final = _EnvSetting("host", "PAPERLESS_MCP_HOST", _parse_str, DEFAULT_HOST)
_PORT: Final = _EnvSetting("port", "PAPERLESS_MCP_PORT", _parse_int, DEFAULT_PORT)
_READONLY: Final = _EnvSetting("readonly", "PAPERLESS_MCP_READONLY", _parse_bool, False)
_ENABLE_DELETE: Final = _EnvSetting(
    "enable_delete", "PAPERLESS_MCP_ENABLE_DELETE", _parse_bool, False
)
_MAX_FILE_BYTES: Final = _EnvSetting(
    "max_file_bytes", "PAPERLESS_MCP_MAX_FILE_BYTES", _parse_int, 25_000_000
)
_VERIFY_SSL: Final = _EnvSetting("verify_ssl", "PAPERLESS_MCP_VERIFY_SSL", _parse_bool, True)
_REQUEST_TIMEOUT: Final = _EnvSetting(
    "request_timeout", "PAPERLESS_MCP_TIMEOUT", _parse_float, 30.0
)
_NAME_CACHE_TTL: Final = _EnvSetting(
    "name_cache_ttl", "PAPERLESS_MCP_NAME_CACHE_TTL", _parse_float, 300.0
)
_LOG_LEVEL: Final = _EnvSetting("log_level", "PAPERLESS_MCP_LOG_LEVEL", _parse_log_level, "INFO")

#: Every optional setting. Only enumerated here for ``ENV_VARS`` and the test
#: that pins the list against :class:`Settings`; resolution goes through the
#: individually typed constants above.
_ENV_SETTINGS: Final[tuple[_EnvSetting[Any], ...]] = (
    _TRANSPORT,
    _AUTH_TOKEN,
    _HOST,
    _PORT,
    _READONLY,
    _ENABLE_DELETE,
    _MAX_FILE_BYTES,
    _VERIFY_SSL,
    _REQUEST_TIMEOUT,
    _NAME_CACHE_TTL,
    _LOG_LEVEL,
)

#: Every variable this server reads, required ones first. ``--help`` lists these.
ENV_VARS: Final[tuple[str, ...]] = (
    "PAPERLESS_URL",
    "PAPERLESS_TOKEN",
    *(setting.env for setting in _ENV_SETTINGS),
)

_URL_HINT: Final = (
    "PAPERLESS_URL is required (or pass --url). "
    "Example: PAPERLESS_URL=https://paperless.example.com"
)
_CREDENTIAL_HINT: Final = (
    "PAPERLESS_TOKEN is required (or pass --token). "
    "Create one in Paperless-ngx under Settings -> API tokens."
)


def _required(overrides: Mapping[str, object], field: str, env: str, hint: str) -> str:
    value = overrides.get(field) or _raw(env)
    if not value:
        raise ConfigError(hint)
    return _parse_str(env, value)


def _validate(settings: Settings) -> None:
    if not 0 <= settings.port <= 65535:
        raise ConfigError(f"PAPERLESS_MCP_PORT must be 0-65535, got {settings.port}")
    if settings.max_file_bytes <= 0:
        raise ConfigError("PAPERLESS_MCP_MAX_FILE_BYTES must be positive")
    if settings.request_timeout <= 0:
        raise ConfigError("PAPERLESS_MCP_TIMEOUT must be positive")
    if settings.name_cache_ttl < 0:
        raise ConfigError("PAPERLESS_MCP_NAME_CACHE_TTL must not be negative")


def load_settings(overrides: Mapping[str, object] | None = None) -> Settings:
    """Load configuration from the environment, applying CLI overrides on top.

    Args:
        overrides: Parsed command-line values. Keys map 1:1 to :class:`Settings`
            field names; a ``None`` value means "not supplied on the command
            line" and leaves the environment value (or default) in place.

    Raises:
        ConfigError: When a required value is missing, a value cannot be parsed,
            or a key does not name a setting.
    """
    over: Mapping[str, object] = overrides or {}
    if unknown := sorted(set(over) - {field.name for field in fields(Settings)}):
        raise ConfigError(f"Not a setting: {', '.join(unknown)}")

    settings = Settings(
        paperless_url=_required(over, "paperless_url", "PAPERLESS_URL", _URL_HINT),
        paperless_token=_required(over, "paperless_token", "PAPERLESS_TOKEN", _CREDENTIAL_HINT),
        transport=_TRANSPORT.value(over),
        auth_token=_AUTH_TOKEN.value(over),
        host=_HOST.value(over),
        port=_PORT.value(over),
        readonly=_READONLY.value(over),
        enable_delete=_ENABLE_DELETE.value(over),
        max_file_bytes=_MAX_FILE_BYTES.value(over),
        verify_ssl=_VERIFY_SSL.value(over),
        request_timeout=_REQUEST_TIMEOUT.value(over),
        name_cache_ttl=_NAME_CACHE_TTL.value(over),
        log_level=_LOG_LEVEL.value(over),
    )
    _validate(settings)
    return settings
