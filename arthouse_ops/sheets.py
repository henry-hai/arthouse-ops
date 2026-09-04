"""Reading and writing the arthouse-ops Google Sheet.

Two things this has to get right that the n8n Google Sheets node did for free.

Upsert. The sheet has no key column the API knows about, so a write keyed on
entry_id means reading the tab, remembering which row each entry_id sits on,
and updating that row rather than appending a second copy. Rerunning the
pipeline must not grow the sheet.

Credentials that work unattended. n8n held a browser OAuth session. A scheduled
job cannot, so the default here is a service account with the sheet shared to
it. The OAuth refresh token path is kept for running on a laptop against the
client that already exists in secrets/.
"""

import json
import os

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import logs, retry

log = logs.get("sheets")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}


def _column_letter(count):
    """A1 letter for the last of `count` columns. The sheet has 12, so A..L."""
    letters = ""
    while count:
        count, remainder = divmod(count - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _retry_sheets(exc):
    """429 and 5xx are worth retrying. So is 403, but only when it is a quota
    error: the same status is also how Sheets says the account cannot see the
    file, and retrying that just delays a clear failure."""
    if isinstance(exc, HttpError):
        status = exc.resp.status
        if status == 403 and "rateLimitExceeded" not in str(exc) and "quota" not in str(exc).lower():
            return None
        if status in RETRYABLE_STATUS:
            return retry.Retryable(exc)
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return retry.Retryable(exc)
    return None


def credentials():
    """Service account first, browser OAuth refresh token second."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=SCOPES)

    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    client_file = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", "secrets/google-oauth-client.json")
    if refresh_token and os.path.exists(client_file):
        with open(client_file) as handle:
            client = json.load(handle)
        installed = client.get("installed") or client.get("web") or {}
        return Credentials(
            None,
            refresh_token=refresh_token,
            client_id=installed["client_id"],
            client_secret=installed["client_secret"],
            token_uri=installed.get("token_uri", "https://oauth2.googleapis.com/token"),
            scopes=SCOPES,
        )

    raise SystemExit(
        "no Google credentials. Set GOOGLE_SERVICE_ACCOUNT_JSON to the contents "
        "of a service account key file, or GOOGLE_SERVICE_ACCOUNT_FILE to its "
        "path, and share the sheet with that account's address as an Editor. "
        "GOOGLE_OAUTH_REFRESH_TOKEN with GOOGLE_OAUTH_CLIENT_FILE also works "
        "for a local run. Run with --dry-run to skip the sheet entirely."
    )


class Sheet:
    def __init__(self, config, service=None):
        self.config = config
        self.values = (service or build(
            "sheets", "v4", credentials=credentials(), cache_discovery=False,
        )).spreadsheets().values()

    def _call(self, request, op, **fields):
        return retry.call(request.execute, classify=_retry_sheets, op=op, **fields)

    def read_rows(self, tab, headers):
        """Every data row of a tab, plus the sheet row number each one sits on.

        Row numbers are 1-based and include the header, so the first data row
        is row 2. They are what an update has to address later.
        """
        last = _column_letter(len(headers))
        body = self._call(
            self.values.get(spreadsheetId=self.config.google_sheet_id,
                            range=f"{tab}!A1:{last}"),
            op="values.get", tab=tab,
        )
        values = body.get("values", [])
        if not values:
            return []
        present = [str(h).strip() for h in values[0]]
        rows = []
        for offset, raw in enumerate(values[1:], start=2):
            padded = list(raw) + [""] * (len(present) - len(raw))
            record = dict(zip(present, padded))
            record["_row"] = offset
            rows.append(record)
        log.info("read tab", tab=tab, rows=len(rows))
        return rows

    def upsert_leads(self, leads, existing_index):
        """Write leads rows, updating in place where the entry_id is known.

        existing_index maps entry_id to sheet row number and is updated as new
        rows are appended, so two batches in the same run cannot both append
        the same entry_id.
        """
        from .config import LEADS_HEADERS

        last = _column_letter(len(LEADS_HEADERS))
        updates, appends = [], []
        for lead in leads:
            values = [lead.get(column, "") for column in LEADS_HEADERS]
            row_number = existing_index.get(str(lead.get("entry_id", "")).strip())
            if row_number:
                updates.append({
                    "range": f"{self.config.tab_leads}!A{row_number}:{last}{row_number}",
                    "values": [values],
                })
            else:
                appends.append((lead, values))

        if updates:
            self._call(
                self.values.batchUpdate(
                    spreadsheetId=self.config.google_sheet_id,
                    body={"valueInputOption": "RAW", "data": updates},
                ),
                op="values.batchUpdate", rows=len(updates),
            )

        if appends:
            response = self._call(
                self.values.append(
                    spreadsheetId=self.config.google_sheet_id,
                    range=f"{self.config.tab_leads}!A1:{last}",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [values for _, values in appends]},
                ),
                op="values.append", rows=len(appends),
            )
            first_row = _first_appended_row(response)
            if first_row:
                for offset, (lead, _) in enumerate(appends):
                    existing_index[str(lead.get("entry_id", "")).strip()] = first_row + offset

        log.info("wrote leads", updated=len(updates), appended=len(appends))
        return len(updates), len(appends)

    def append_errors(self, rows):
        from .config import ERROR_HEADERS

        if not rows:
            return 0
        last = _column_letter(len(ERROR_HEADERS))
        self._call(
            self.values.append(
                spreadsheetId=self.config.google_sheet_id,
                range=f"{self.config.tab_errors}!A1:{last}",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[row.get(c, "") for c in ERROR_HEADERS] for row in rows]},
            ),
            op="values.append", tab=self.config.tab_errors, rows=len(rows),
        )
        log.info("wrote errors", rows=len(rows))
        return len(rows)


def _first_appended_row(response):
    """Sheets returns the range it appended into, e.g. leads!A431:L455."""
    updated = (response.get("updates") or {}).get("updatedRange", "")
    tail = updated.split("!")[-1]
    digits = "".join(c for c in tail.split(":")[0] if c.isdigit())
    return int(digits) if digits else None
