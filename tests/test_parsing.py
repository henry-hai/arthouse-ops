"""Parsing the connector payload into leads rows."""

import pytest

from arthouse_ops import parsing


def test_flatten_uses_field_labels_as_keys(contact_us_page):
    rows = parsing.flatten_entries([contact_us_page])
    assert len(rows) == 4
    assert rows[0]["Entry ID"] == "22801"
    assert rows[0]["Entry Date"] == "2026-08-17 20:51:49"
    assert rows[0]["Name"] == "Dana Whitfield"


def test_subject_is_copied_to_message(contact_us_page):
    """The Contact Us form has no Message field. Subject carries the body."""
    rows = parsing.flatten_entries([contact_us_page])
    assert rows[0]["Message"] == rows[0]["Subject"]


def test_entry_with_no_subject_gets_no_message(contact_us_page):
    rows = parsing.flatten_entries([contact_us_page])
    assert "Message" not in rows[3]


def test_pages_are_concatenated(contact_us_page):
    rows = parsing.flatten_entries([contact_us_page, contact_us_page])
    assert len(rows) == 8


def test_html_is_stripped_from_the_message(contact_us_page):
    row = parsing.contact_us_row(parsing.flatten_entries([contact_us_page])[1])
    assert "<p>" not in row["message"]
    assert "&#039;" not in row["message"]
    assert row["message"].startswith("Hi Are your programs")
    assert "We're homeschooling." in row["message"]


def test_contact_us_row_fields(contact_us_page):
    rows = parsing.flatten_entries([contact_us_page])
    row = parsing.contact_us_row(rows[0])
    assert row["entry_id"] == "22801"
    assert row["email"] == "dana@example-pta.org"
    assert row["phone"] == "+15550101234"
    assert row["company_school"] == "Rivermont Elementary PTA"


def test_name_picker_does_not_grab_the_company_column(contact_us_page):
    """'Company / School' contains neither 'name' nor a name, but the loose
    ['name'] word set would match any header containing it. Guard the case."""
    row = parsing.contact_us_row({"Entry ID": "1", "Company / School": "Acme Name Co",
                                  "Name": "Real Person"})
    assert row["name"] == "Real Person"


def test_missing_fields_become_empty_strings(contact_us_page):
    row = parsing.contact_us_row(parsing.flatten_entries([contact_us_page])[3])
    assert row["phone"] == ""
    assert row["company_school"] == ""
    assert row["message"] == ""


@pytest.mark.parametrize("value,expected", [
    ("$245.00", 245.0), ("1,250", 1250.0), ("", 0), (None, 0), ("free", 0), ("  $12 ", 12.0),
])
def test_parse_currency(value, expected):
    assert parsing.parse_currency(value) == expected
