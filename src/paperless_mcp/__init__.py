"""paperless-mcp: a Model Context Protocol server for Paperless-ngx."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Read from the installed distribution rather than a literal: pyproject
    # carries a 0.0.0 placeholder and the release workflow stamps the real
    # version from the git tag at build time, so a hardcoded value here would
    # report something different from the package the user actually installed.
    __version__ = version("paperless-mcp")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
    __version__ = "0.0.0"
