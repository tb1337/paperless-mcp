"""Container healthcheck: verify the MCP HTTP endpoint is listening."""

from __future__ import annotations

import os
import socket
import sys


def main() -> int:
    """Return 0 if the configured TCP port accepts a connection."""
    host = os.environ.get("PAPERLESS_MCP_HOST", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    try:
        port = int(os.environ.get("PAPERLESS_MCP_PORT", "8000"))
    except ValueError:
        return 1
    try:
        with socket.create_connection((host, port), timeout=3):
            return 0
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
