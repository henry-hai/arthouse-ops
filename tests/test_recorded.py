"""Parsing run over real recorded payloads from the live connector.

These files hold real names, emails and message bodies, so inputs/ is
gitignored and every test here skips when the payloads are absent. Record them
with scripts/record-sample.py. They are the tests that catch a parser which
only works on tidy fixtures: 200 entries of what people actually typed, HTML
wrappers, empty columns, accented names, pasted email signatures and all.
"""

import json

import pytest

from arthouse_ops import parsing
from conftest import RECORDED_CONTACT_US, RECORDED_REGISTRATION


def load(path):
    if not path.exists():
        pytest.skip(f"{path.name} is local only, record it with scripts/record-sample.py")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def contact_us_rows():
    return parsing.flatten_entries([load(RECORDED_CONTACT_US)])


@pytest.fixture(scope="module")
def registration_rows():
    return parsing.flatten_entries([load(RECORDED_REGISTRATION)])


def test_the_recorded_page_is_a_real_batch(contact_us_rows):
    assert len(contact_us_rows) > 50


def test_every_recorded_contact_us_row_parses(contact_us_rows):
    for row in contact_us_rows:
        parsed = parsing.contact_us_row(row)
        assert parsed["entry_id"], row
        assert set(parsed) == {"entry_id", "entry_date", "name", "email", "phone",
                               "company_school", "subject", "message"}


def test_a_populated_subject_always_yields_a_message(contact_us_rows):
    """The form stores the body in Subject. Whenever it holds text, the parsed
    row must carry it. A parser that silently stopped finding the body would
    show up here."""
    for row in contact_us_rows:
        if str(row.get("Subject", "")).strip():
            assert parsing.contact_us_row(row)["message"], row["Entry ID"]


def test_the_recorded_page_really_does_contain_empty_submissions(contact_us_rows):
    """Most of the older entries are a name and an email with no body at all.
    This is the shape the classifier has to recognise and not pay for."""
    from arthouse_ops import classify

    parsed = [parsing.contact_us_row(row) for row in contact_us_rows]
    empty = [row for row in parsed if not classify.has_message(row)]
    assert empty, "expected the recorded page to include bodyless submissions"
    for row in empty:
        assert row["email"] or row["name"], "an entry with nothing in it at all"


def test_no_markup_survives_into_the_prompt(contact_us_rows):
    for row in contact_us_rows:
        prompt = parsing.contact_us_row(row)["message"]
        for markup in ("<p>", "<br", "</", "&nbsp;", "&#039;", "&amp;"):
            assert markup not in prompt, row["Entry ID"]


def test_prompts_stay_within_the_input_cap(contact_us_rows):
    from arthouse_ops import classify

    for row in contact_us_rows:
        prompt = classify.build_prompt(parsing.contact_us_row(row))
        assert len(prompt) <= classify.MAX_INPUT_CHARS


def test_every_recorded_registration_row_parses(registration_rows):
    from arthouse_ops.config import LEADS_HEADERS

    for row in registration_rows:
        lead, _ = parsing.registration_lead(row)
        assert list(lead.keys()) == LEADS_HEADERS
        assert lead["source"] == "registration"
        assert isinstance(lead["amount_usd"], (int, float))


def test_no_sensitive_column_leaks_into_the_descriptive_fields(registration_rows):
    """Every value from a sensitive column, checked against the fields a parent
    can misuse as a notes box, across a real page of registrations.

    name and email are excluded on purpose. They are the parent contact, they
    are the point of the row, and they overlap with the emergency-contact and
    authorized-adult columns constantly and harmlessly, because a parent writes
    "Dana Whitfield and Sam Booker" in one box and "Dana Whitfield" in
    another. The exposure that matters is a student's name landing in a column
    the dashboards chart, and that is what this asserts.
    """
    checked = 0
    for row in registration_rows:
        lead, _ = parsing.registration_lead(row)
        descriptive = " | ".join(str(lead[field]) for field in parsing._SHORT_FIELDS)
        for value in parsing.sensitive_values(row):
            if len(value) < 4 or value in descriptive.split(" | "):
                continue
            checked += 1
            assert value not in descriptive, (
                f"{value!r} from a sensitive column reached entry {row['Entry ID']}")
    assert checked > 100, "the recorded page had too few sensitive values to be a real check"


def test_the_scrub_fires_on_the_recorded_page(registration_rows):
    """The recorded page contains at least one parent who used the school-name
    box for a note. If it ever stops firing, the guard is untested here."""
    scrubbed = [row for row in registration_rows if parsing.registration_lead(row)[1]]
    assert scrubbed, "no row on the recorded page needed scrubbing"


def test_recorded_registration_rows_mostly_have_a_parent_contact(registration_rows):
    leads = [parsing.registration_lead(row)[0] for row in registration_rows]
    with_email = [lead for lead in leads if lead["email"]]
    assert len(with_email) / len(leads) > 0.8
