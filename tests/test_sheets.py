"""The pieces of the Sheets client that are worth testing without the network."""

import pytest
from googleapiclient.errors import HttpError

from arthouse_ops import sheets


@pytest.mark.parametrize("count,letter", [(1, "A"), (12, "L"), (26, "Z"), (27, "AA"), (52, "AZ")])
def test_column_letter(count, letter):
    assert sheets._column_letter(count) == letter


@pytest.mark.parametrize("updated_range,expected", [
    ("leads!A431:L455", 431),
    ("'my leads'!A2:L2", 2),
    ("", None),
])
def test_first_appended_row(updated_range, expected):
    assert sheets._first_appended_row({"updates": {"updatedRange": updated_range}}) == expected


def http_error(status, content=b"{}"):
    return HttpError(type("Resp", (), {"status": status, "reason": "x"})(), content)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(status):
    assert sheets._retry_sheets(http_error(status)) is not None


def test_a_permission_denied_is_not_retried():
    """403 for a sheet the account cannot see must fail fast, not five times."""
    denied = http_error(403, b'{"error":{"message":"The caller does not have permission"}}')
    assert sheets._retry_sheets(denied) is None


def test_a_quota_403_is_retried():
    quota = http_error(403, b'{"error":{"message":"Quota exceeded","errors":[{"reason":"rateLimitExceeded"}]}}')
    assert sheets._retry_sheets(quota) is not None


def test_a_bad_request_is_not_retried():
    assert sheets._retry_sheets(http_error(400)) is None


# --- cell encoding ---

def test_typed_columns_pass_through_unchanged():
    """entry_id must land as a number and entry_date as a date, which is what
    the rows already in the tab are and what Looker types the column from."""
    assert sheets._cell("entry_id", "22801") == "22801"
    assert sheets._cell("entry_date", "2026-08-17 20:51:49") == "2026-08-17 20:51:49"
    assert sheets._cell("amount_usd", 245.0) == 245.0


@pytest.mark.parametrize("value", [
    '=HYPERLINK("https://evil.test","click")',
    "+1+1",
    "-1+1",
    "@import",
])
def test_a_formula_in_free_text_is_neutralised(value):
    """name, school and summary carry text from a public web form, and the
    write is USER_ENTERED, so a leading = would become a live formula."""
    assert sheets._cell("name", value) == "'" + value


def test_ordinary_text_is_untouched():
    for value in ["Rivermont Elementary PTA", "sponsor", "[classification failed]",
                  "Asks about signup.", ""]:
        assert sheets._cell("summary", value) == value


def test_none_becomes_an_empty_string():
    assert sheets._cell("school", None) == ""
