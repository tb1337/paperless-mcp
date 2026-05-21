"""Module entry point: ``python -m paperless_mcp``."""

import sys

from .server import serve, serve_stdio


def main() -> None:
    if "--stdio" in sys.argv:
        serve_stdio()
    else:
        serve()


if __name__ == "__main__":
    main()
