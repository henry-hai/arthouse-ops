"""Runtime settings, read from the environment.

This is the Config node from the n8n workflow. The names and the defaults are
the same, so the same .env drives either implementation while both are running.
Values are read once at startup and passed down, rather than reached for from
inside the pipeline, so a run is reproducible from the line it logs at start.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

LEADS_HEADERS = [
    "entry_id", "entry_date", "source", "name", "email", "school", "grade",
    "homeroom_teacher", "amount_usd", "category", "sentiment", "summary",
]

ERROR_HEADERS = ["timestamp", "node_name", "error_message", "workflow_run_id"]


def _list(name, default):
    return [p.strip() for p in os.environ.get(name, default).split(",") if p.strip()]


@dataclass(frozen=True)
class Config:
    entries_url: str
    wp_username: str
    wp_app_password: str
    registration_form_id: str
    contact_us_form_id: str
    page_size: int
    max_pages: int
    start_offset: int

    google_sheet_id: str
    tab_leads: str
    tab_errors: str

    llm_model: str
    anthropic_api_key: str
    categories: list = field(default_factory=list)
    sentiments: list = field(default_factory=list)

    # How many classified rows to write before moving on. A run that dies
    # partway through has still committed every flushed batch, and the next run
    # skips those entries instead of paying for them again.
    write_batch_size: int = 25

    def redacted(self):
        """The settings worth logging. Never includes a credential."""
        return {
            "entries_url": self.entries_url,
            "sheet_id": self.google_sheet_id[:8] + "...",
            "tab_leads": self.tab_leads,
            "model": self.llm_model,
            "page_size": self.page_size,
            "write_batch_size": self.write_batch_size,
        }


def load(env_file=".env"):
    load_dotenv(env_file, override=False)

    missing = [
        name for name in ("WP_USERNAME", "WP_APP_PASSWORD", "GOOGLE_SHEET_ID", "ANTHROPIC_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit("missing required environment variables: " + ", ".join(missing))

    return Config(
        entries_url=os.environ.get(
            "WORDPRESS_ENTRIES_URL",
            "https://arthousestudioca.org/wp-json/arthouse-ops/v1/entries",
        ),
        wp_username=os.environ["WP_USERNAME"],
        wp_app_password=os.environ["WP_APP_PASSWORD"],
        registration_form_id=os.environ.get("REGISTRATION_FORM_ID", "2"),
        contact_us_form_id=os.environ.get("CONTACT_US_FORM_ID", "3"),
        page_size=int(os.environ.get("ENTRIES_PAGE_SIZE", "200")),
        max_pages=int(os.environ.get("ENTRIES_MAX_PAGES", "200")),
        start_offset=int(os.environ.get("ENTRIES_START_OFFSET", "0")),
        google_sheet_id=os.environ["GOOGLE_SHEET_ID"],
        tab_leads=os.environ.get("GOOGLE_SHEET_TAB_LEADS", "leads"),
        tab_errors=os.environ.get("GOOGLE_SHEET_TAB_ERRORS", "errors"),
        llm_model=os.environ.get("LLM_MODEL", "claude-haiku-4-5"),
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        categories=_list("CATEGORY_LIST", "sponsor,school,volunteer,general,spam"),
        sentiments=_list("SENTIMENT_LIST", "positive,neutral,urgent"),
        write_batch_size=int(os.environ.get("WRITE_BATCH_SIZE", "25")),
    )
