import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# Real recorded payloads live in inputs/ and are gitignored, because they hold
# names, emails and message bodies from real people. Tests that need them skip
# when they are absent, so CI stays green without them. Record them with
# scripts/record-sample.py.
INPUTS = pathlib.Path(__file__).resolve().parents[1] / "inputs"
RECORDED_CONTACT_US = INPUTS / "contact_us-page.json"
RECORDED_REGISTRATION = INPUTS / "registration-page.json"


def load(name):
    with open(FIXTURES / name) as handle:
        return json.load(handle)


@pytest.fixture
def contact_us_page():
    return load("contact_us_page.json")


@pytest.fixture
def registration_page():
    return load("registration_page.json")


@pytest.fixture
def settings():
    from arthouse_ops.config import Config

    return Config(
        entries_url="https://example.test/entries",
        wp_username="user", wp_app_password="pass",
        registration_form_id="2", contact_us_form_id="3",
        page_size=200, max_pages=10, start_offset=0,
        google_sheet_id="sheet-id", tab_leads="leads", tab_errors="errors",
        llm_model="claude-haiku-4-5", anthropic_api_key="test-key",
        categories=["sponsor", "school", "volunteer", "general", "spam"],
        sentiments=["positive", "neutral", "urgent"],
        write_batch_size=2,
    )
