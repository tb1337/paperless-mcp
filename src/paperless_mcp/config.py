"""Environment-driven configuration for paperless-mcp."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved runtime configuration."""

    paperless_url: str
    paperless_token: str
    auth_token: str | None
    host: str
    port: int
    readonly: bool
    enable_delete: bool
    max_file_bytes: int

    @property
    def expose_writes(self) -> bool:
        """Whether create/update tools should be registered."""
        return not self.readonly

    @property
    def expose_deletes(self) -> bool:
        """Whether delete tools should be registered."""
        return not self.readonly and self.enable_delete


def load_settings() -> Settings:
    """Load configuration from the environment, raising on missing required keys."""
    url = os.environ.get("PAPERLESS_URL", "").strip()
    if not url:
        raise ConfigError("PAPERLESS_URL is required")
    token = os.environ.get("PAPERLESS_TOKEN", "").strip()
    if not token:
        raise ConfigError("PAPERLESS_TOKEN is required")

    return Settings(
        paperless_url=url,
        paperless_token=token,
        auth_token=(os.environ.get("PAPERLESS_MCP_AUTH_TOKEN", "").strip() or None),
        host=os.environ.get("PAPERLESS_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_int_env("PAPERLESS_MCP_PORT", default=8000),
        readonly=_bool_env("PAPERLESS_MCP_READONLY", default=False),
        enable_delete=_bool_env("PAPERLESS_MCP_ENABLE_DELETE", default=False),
        max_file_bytes=_int_env("PAPERLESS_MCP_MAX_FILE_BYTES", default=25_000_000),
    )
