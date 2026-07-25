"""Container healthcheck: verify the MCP HTTP endpoint answers."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

HEALTH_PATH = "/healthz"


def main() -> int:
    """Return 0 when the configured health endpoint answers with HTTP 200."""
    host = os.environ.get("PAPERLESS_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::", "*"}:
        host = "127.0.0.1"
    try:
        port = int(os.environ.get("PAPERLESS_MCP_PORT", "8000"))
    except ValueError:
        return 1

    url = f"http://{host}:{port}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
