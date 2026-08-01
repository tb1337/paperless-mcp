"""Command-line entry point: ``paperless-mcp`` / ``python -m paperless_mcp``."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from dotenv import load_dotenv

from . import __version__
from .config import ConfigError, Settings, load_settings
from .server import configure_logging, serve

EPILOG = """\
By default the server speaks the stdio transport, which is what Claude Desktop
and other MCP clients use when they launch it as a subprocess. Pass
--transport http to expose it on the network instead.

Every flag has an environment-variable equivalent (PAPERLESS_URL,
PAPERLESS_TOKEN, PAPERLESS_MCP_TRANSPORT, PAPERLESS_MCP_HOST,
PAPERLESS_MCP_PORT, PAPERLESS_MCP_AUTH_TOKEN, PAPERLESS_MCP_READONLY,
PAPERLESS_MCP_ENABLE_DELETE, PAPERLESS_MCP_MAX_FILE_BYTES,
PAPERLESS_MCP_VERIFY_SSL, PAPERLESS_MCP_TIMEOUT, PAPERLESS_MCP_NAME_CACHE_TTL,
PAPERLESS_MCP_LOG_LEVEL); flags win over the environment.
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the console script."""
    parser = argparse.ArgumentParser(
        prog="paperless-mcp",
        description="Model Context Protocol server for Paperless-ngx.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"paperless-mcp {__version__}")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=None,
        help="Transport to serve (default: stdio).",
    )
    parser.add_argument(
        "--stdio",
        dest="transport",
        action="store_const",
        const="stdio",
        help="Shorthand for --transport stdio.",
    )
    parser.add_argument(
        "--http",
        dest="transport",
        action="store_const",
        const="http",
        help="Shorthand for --transport http.",
    )
    parser.add_argument("--url", dest="paperless_url", help="Paperless-ngx base URL.")
    parser.add_argument("--token", dest="paperless_token", help="Paperless-ngx API token.")
    parser.add_argument("--host", help="Bind address for --transport http.")
    parser.add_argument("--port", type=int, help="Bind port for --transport http.")
    parser.add_argument(
        "--auth-token",
        help="Bearer token clients must present on the HTTP endpoint.",
    )
    parser.add_argument(
        "--readonly",
        action="store_true",
        default=None,
        help="Hide every write and delete tool.",
    )
    parser.add_argument(
        "--enable-delete",
        action="store_true",
        default=None,
        help="Expose the delete tools (off by default).",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        help="Cap for base64 file payloads returned by download tools.",
    )
    parser.add_argument(
        "--no-verify-ssl",
        dest="verify_ssl",
        action="store_false",
        default=None,
        help="Skip TLS certificate verification (self-signed Paperless instances).",
    )
    parser.add_argument("--timeout", dest="request_timeout", type=float, help="HTTP timeout (s).")
    parser.add_argument(
        "--name-cache-ttl",
        type=float,
        help="Seconds the resolved tag/correspondent names stay cached (0: forever).",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="Log verbosity (logs always go to stderr).",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to a dotenv file to load before reading the environment.",
    )
    return parser


def resolve_settings(argv: list[str] | None = None) -> Settings:
    """Parse *argv*, load the dotenv file, and resolve the effective settings."""
    args = build_parser().parse_args(argv)
    overrides: dict[str, Any] = vars(args)
    env_file = overrides.pop("env_file", None)
    # Never override a variable the MCP client passed in explicitly.
    load_dotenv(env_file, override=False)
    return load_settings(overrides)


def main(argv: list[str] | None = None) -> int:
    """Run the server; return a process exit code."""
    try:
        settings = resolve_settings(argv)
    except ConfigError as exc:
        print(f"paperless-mcp: configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings)
    try:
        serve(settings)
    except KeyboardInterrupt:  # pragma: no cover - interactive interrupt
        logging.getLogger("paperless_mcp").info("Interrupted, shutting down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
