"""The weekly summary: the two gates, the counting, and the HTML."""

from datetime import datetime

import pytest

from arthouse_ops import digest

SUNDAY = datetime(2026, 9, 6, 7, 0, tzinfo=digest.PACIFIC)
MONDAY = datetime(2026, 9, 7, 7, 0, tzinfo=digest.PACIFIC)


def lead(entry_id, source, days_ago, **extra):
    when = SUNDAY.replace(hour=12) - __import__("datetime").timedelta(days=days_ago)
    row = {"entry_id": entry_id, "entry_date": when.strftime("%Y-%m-%d %H:%M:%S"),
           "source": source, "name": "", "email": "", "amount_usd": "",
           "category": "", "sentiment": "", "summary": ""}
    row.update(extra)
    return row


# --- gate 1, the send day ---

def test_it_sends_on_the_configured_day():
    assert digest.should_send([], "sunday", SUNDAY) is None


def test_it_does_not_send_on_any_other_day():
    assert "not sunday" in digest.should_send([], "sunday", MONDAY)


def test_the_send_day_is_configurable():
    assert digest.should_send([], "monday", MONDAY) is None


# --- gate 2, once per day ---

def test_it_does_not_send_twice_in_one_day():
    """A manual run and the scheduled run on the same Sunday must not both
    email. The mark lives in the state tab, the same place n8n kept it."""
    state = [{"key": "last_weekly_email_date", "value": "2026-09-06", "_row": 2}]
    assert digest.should_send(state, "sunday", SUNDAY) == "already sent today"


def test_a_send_last_week_does_not_block_this_week():
    state = [{"key": "last_weekly_email_date", "value": "2026-08-30", "_row": 2}]
    assert digest.should_send(state, "sunday", SUNDAY) is None


def test_an_unrelated_state_row_is_ignored():
    state = [{"key": "something_else", "value": "2026-09-06", "_row": 2}]
    assert digest.should_send(state, "sunday", SUNDAY) is None


# --- counting ---

def test_only_the_last_seven_days_are_counted():
    rows = [lead("1", "registration", 2), lead("2", "registration", 30)]
    assert digest.summarize(rows, SUNDAY)["registrations"] == 1


def test_the_two_sources_are_counted_separately():
    rows = [lead("1", "registration", 1), lead("2", "contact_us", 1), lead("3", "contact_us", 1)]
    stats = digest.summarize(rows, SUNDAY)
    assert (stats["registrations"], stats["contact_us"], stats["total"]) == (1, 2, 3)


def test_revenue_sums_the_registration_amounts():
    rows = [lead("1", "registration", 1, amount_usd="$245.00"),
            lead("2", "registration", 1, amount_usd=161.5),
            lead("3", "registration", 1, amount_usd="")]
    assert digest.summarize(rows, SUNDAY)["revenue"] == pytest.approx(406.5)


def test_categories_are_tallied():
    rows = [lead("1", "contact_us", 1, category="sponsor"),
            lead("2", "contact_us", 1, category="sponsor"),
            lead("3", "contact_us", 1, category="spam")]
    counts = digest.summarize(rows, SUNDAY)["categories"]
    assert counts["sponsor"] == 2 and counts["spam"] == 1 and counts["volunteer"] == 0


def test_urgent_messages_are_listed():
    rows = [lead("1", "contact_us", 1, sentiment="urgent", name="Robin", summary="Needs a reply")]
    urgent = digest.summarize(rows, SUNDAY)["urgent"]
    assert len(urgent) == 1 and urgent[0]["name"] == "Robin"


def test_an_unparseable_date_is_skipped_not_fatal():
    rows = [lead("1", "registration", 1), {"entry_date": "not a date", "source": "registration"}]
    assert digest.summarize(rows, SUNDAY)["registrations"] == 1


# --- rendering ---

def test_the_summary_reports_its_numbers():
    stats = digest.summarize([lead("1", "registration", 1, amount_usd="100")], SUNDAY)
    subject, html = digest.render(stats)
    assert "ArtHouse weekly summary" in subject
    assert "$100.00" in html
    assert "1</strong> new form entries" in html


def test_a_quiet_week_says_so():
    _, html = digest.render(digest.summarize([], SUNDAY))
    assert "No urgent messages this week." in html


def test_the_dashboard_link_is_optional():
    stats = digest.summarize([], SUNDAY)
    assert "Open the dashboard" not in digest.render(stats)[1]
    assert "Open the dashboard" in digest.render(stats, "https://example.test/d")[1]


def test_form_text_cannot_inject_html():
    """Names and summaries come from a public web form and land in an email."""
    rows = [lead("1", "contact_us", 1, sentiment="urgent",
                 name="<script>alert(1)</script>", summary="a & b")]
    _, html = digest.render(digest.summarize(rows, SUNDAY))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b" in html
