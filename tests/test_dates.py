"""ISO date parsing for the tool arguments that take one."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import pytest

from paperless_mcp.tools._dates import parse_date, parse_datetime
from paperless_mcp.tools._errors import ToolInputError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02", dt.date(2026, 1, 2)),
        # A datetime keeps only its date half.
        ("2026-01-02T13:45:00", dt.date(2026, 1, 2)),
    ],
)
def test_parse_date(value: str, expected: dt.date) -> None:
    assert parse_date(value, field="created") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02T13:45:00", dt.datetime(2026, 1, 2, 13, 45)),
        # A bare date widens to midnight.
        ("2026-01-02", dt.datetime(2026, 1, 2, 0, 0)),
    ],
)
def test_parse_datetime(value: str, expected: dt.datetime) -> None:
    assert parse_datetime(value, field="expiration") == expected


@pytest.mark.parametrize(
    ("parse", "field"), [(parse_date, "created"), (parse_datetime, "expiration")]
)
def test_a_date_that_is_not_iso_names_the_field_it_came_from(
    parse: Callable[..., object], field: str
) -> None:
    with pytest.raises(ToolInputError, match=field):
        parse("last tuesday", field=field)
