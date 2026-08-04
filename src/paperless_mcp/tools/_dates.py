"""ISO date parsing for the tool arguments that take one.

Both parsers report a bad value as :class:`ToolInputError`, naming the argument it
came from, because that is what the model needs in order to correct itself.
"""

from __future__ import annotations

import datetime as dt

from ._errors import ToolInputError


def parse_date(value: str, *, field: str) -> dt.date:
    """Parse an ISO date (``YYYY-MM-DD``) or datetime, keeping only the date part.

    Raises:
        ToolInputError: When *value* is not ISO 8601.
    """
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        pass
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ToolInputError(
            f"{field} must be an ISO date (YYYY-MM-DD) or datetime, got {value!r}"
        ) from exc


def parse_datetime(value: str, *, field: str) -> dt.datetime:
    """Parse an ISO datetime, widening a bare date to midnight.

    Raises:
        ToolInputError: When *value* is not ISO 8601.
    """
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        pass
    try:
        return dt.datetime.combine(dt.date.fromisoformat(value), dt.time.min)
    except ValueError as exc:
        raise ToolInputError(f"{field} must be an ISO datetime or date, got {value!r}") from exc
