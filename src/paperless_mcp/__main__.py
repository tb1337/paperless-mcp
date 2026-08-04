"""Command-line entry point: ``paperless-mcp`` / ``python -m paperless_mcp``."""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap

from dotenv import load_dotenv

from . import __version__
from .config import ENV_VARS, LOG_LEVELS, TRANSPORTS, ConfigError, Settings, load_settings
from .server import configure_logging, serve

# The variable list is generated: hand-listing it here is how it drifts out of
# step with the settings table it documents.
EPILOG = f"""\
By default the server speaks the stdio transport, which is what Claude Desktop
and other MCP clients use when they launch it as a subprocess. Pass
--transport http to expose it on the network instead.

Every flag has an environment-variable equivalent; flags win over the
environment, including over a variable that cannot be parsed:

{textwrap.fill(", ".join(ENV_VARS), width=76)}
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
        choices=TRANSPORTS,
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
        choices=LOG_LEVELS,
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
    # Never override a variable the MCP client passed in explicitly.
    load_dotenv(args.env_file, override=False)
    # vars() hands back the Namespace's own __dict__, so the overrides are built
    # as a copy rather than by popping env_file out of the parsed arguments.
    return load_settings({key: value for key, value in vars(args).items() if key != "env_file"})


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
    except OSError as exc:
        # uvicorn reports a taken port as OSError. A traceback tells the user
        # nothing they can act on; exit 1, since 2 already means "bad config".
        where = f" on {settings.host}:{settings.port}" if settings.transport == "http" else ""
        print(f"paperless-mcp: cannot start{where}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
