"""Entry point for the weekly summary: python -m arthouse_ops.send_digest

Off unless SUMMARY_ENABLED is true. That is the switch, and it is checked
before anything else happens, so a repo variable set to false makes this a
no-op that costs one second and sends nothing.
"""

import argparse
import os
import sys
from datetime import datetime

from . import config, digest, logs, sheets

log = logs.get("digest")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="arthouse-digest", description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="ignore the send day and the once per day mark")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the email and print the subject, send nothing")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)

    logs.setup()
    settings = config.load(args.env_file)

    enabled = os.environ.get("SUMMARY_ENABLED", "false").strip().lower() in ("1", "true", "yes")
    if not enabled and not args.force:
        log.info("disabled, set SUMMARY_ENABLED=true to turn it on")
        return 0

    sender = os.environ.get("GMAIL_SENDER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("WEEKLY_SUMMARY_RECIPIENT", "")
    send_day = os.environ.get("SUMMARY_SEND_DAY", "sunday")

    now = datetime.now(digest.PACIFIC)
    sheet = sheets.Sheet(settings)
    tab_state = os.environ.get("GOOGLE_SHEET_TAB_STATE", "state")

    state_rows = sheet.read_rows(tab_state, digest.STATE_HEADERS)
    if not args.force:
        skip = digest.should_send(state_rows, send_day, now)
        if skip:
            log.info("not sending", reason=skip)
            return 0

    from .config import LEADS_HEADERS
    stats = digest.summarize(sheet.read_rows(settings.tab_leads, LEADS_HEADERS), now)
    subject, html = digest.render(stats, os.environ.get("LOOKER_DASHBOARD_URL", ""))
    log.info("built summary", entries=stats["total"], registrations=stats["registrations"],
             contact_us=stats["contact_us"], urgent=len(stats["urgent"]))

    if args.dry_run:
        log.info("dry run, not sending", subject=subject)
        return 0

    if not (sender and app_password and recipient):
        log.error("missing GMAIL_SENDER, GMAIL_APP_PASSWORD or WEEKLY_SUMMARY_RECIPIENT")
        return 1

    digest.send({"sender": sender, "app_password": app_password, "recipient": recipient},
                subject, html)

    # Mark the day only after the send succeeded, so a failure retries rather
    # than silently skipping next time.
    index = {str(r.get("key", "")).strip(): r["_row"] for r in state_rows
             if str(r.get("key", "")).strip()}
    row_number = index.get(digest.STATE_KEY)
    today = now.strftime("%Y-%m-%d")
    values = [[digest.STATE_KEY, today]]
    if row_number:
        sheet._call(sheet.values.batchUpdate(
            spreadsheetId=settings.google_sheet_id,
            body={"valueInputOption": "USER_ENTERED",
                  "data": [{"range": f"{tab_state}!A{row_number}:B{row_number}",
                            "values": values}]}),
            op="values.batchUpdate", tab=tab_state)
    else:
        sheet._call(sheet.values.append(
            spreadsheetId=settings.google_sheet_id, range=f"{tab_state}!A1:B",
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": values}),
            op="values.append", tab=tab_state)
    log.info("recorded the send", date=today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
