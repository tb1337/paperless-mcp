"""Container healthcheck: verify the MCP HTTP endpoint answers.

Deliberately does not call :func:`~paperless_mcp.config.load_settings`: that
would fail on a missing ``PAPERLESS_TOKEN``, which typically lives only in the
dotenv file the server process itself loaded — reporting a healthy container as
unhealthy. Only the two variables that locate the endpoint are read, defaulting
to the same constants the server does. A host or port supplied to the server as
a command-line flag is therefore invisible here; inside a container both come
from the environment.
"""

from __future__ import annotations

import http.client
import os
import sys
import urllib.error
import urllib.request
from typing import Final

from .config import DEFAULT_HOST, DEFAULT_PORT, HEALTH_PATH

#: A bind-all address is not connectable; probe loopback instead.
_BIND_ALL: Final = frozenset({"0.0.0.0", "::", "*"})


def main() -> int:
    """Return 0 when the configured health endpoint answers with HTTP 200."""
    host = os.environ.get("PAPERLESS_MCP_HOST", "").strip() or DEFAULT_HOST
    if host in _BIND_ALL:
        host = DEFAULT_HOST
    try:
        port = int(os.environ.get("PAPERLESS_MCP_PORT", "").strip() or DEFAULT_PORT)
    except ValueError:
        return 1

    url = f"http://{host}:{port}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return 0 if response.status == 200 else 1
    # URLError is an OSError, so it needs no separate arm. HTTPException and the
    # ValueError urllib raises for an unusable URL are the two that escaped as a
    # traceback into `docker inspect`.
    except (OSError, http.client.HTTPException, ValueError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
