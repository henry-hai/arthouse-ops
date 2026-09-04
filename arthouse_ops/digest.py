"""The weekly summary email.

Ported from the n8n Build Weekly Summary and Send Weekly Summary nodes. Same
numbers, same layout, same two gates.

The two gates are what keep it from being noise. It sends only on the
configured weekday, and only once per day, because a manual run and the
scheduled run landing on the same Sunday must not both email. The once per day
mark lives in the state tab, which is the same place n8n kept it, so switching
between the two implementations does not resend.

All day logic is in Pacific time regardless of where this runs. A GitHub runner
is on UTC, and a Sunday 14:00 UTC run is Sunday morning in San Jose, but the
weekday has to be decided by the nonprofit's clock rather than the runner's.
"""

import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from . import logs

log = logs.get("digest")

PACIFIC = ZoneInfo("America/Los_Angeles")
STATE_KEY = "last_weekly_email_date"
STATE_HEADERS = ["key", "value"]
CATEGORIES = ("sponsor", "school", "volunteer", "general", "spam")


def parse_date(value):
    """Sheet dates arrive as text. Anything unparseable is skipped, not fatal."""
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=PACIFIC)
        except ValueError:
            continue
    return None


def parse_amount(value):
    try:
        return float(str(value).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def summarize(rows, now):
    """Counts and totals for the seven days ending now."""
    cutoff = now - timedelta(days=7)
    stats = {
        "registrations": 0, "contact_us": 0, "revenue": 0.0,
        "categories": {name: 0 for name in CATEGORIES}, "urgent": [],
    }
    for row in rows:
        when = parse_date(row.get("entry_date"))
        if when is None or when < cutoff:
            continue
        source = str(row.get("source", "")).strip()
        if source == "registration":
            stats["registrations"] += 1
            stats["revenue"] += parse_amount(row.get("amount_usd"))
        elif source == "contact_us":
            stats["contact_us"] += 1
            category = str(row.get("category", "general")).strip()
            if category in stats["categories"]:
                stats["categories"][category] += 1
            if str(row.get("sentiment", "")).strip() == "urgent":
                stats["urgent"].append({
                    "name": row.get("name") or "(no name)",
                    "email": row.get("email") or "(no email)",
                    "summary": row.get("summary") or "",
                })
    stats["total"] = stats["registrations"] + stats["contact_us"]
    stats["range_start"] = cutoff.strftime("%Y-%m-%d")
    stats["range_end"] = now.strftime("%Y-%m-%d")
    return stats


def escape(text):
    """Names and summaries come from a public form and land in HTML."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render(stats, dashboard_url=""):
    if stats["urgent"]:
        urgent = "<ul style=\"margin:6px 0 6px 18px;padding:0;\">" + "".join(
            f"<li style=\"margin:2px 0;\"><strong>{escape(u['name'])}</strong> "
            f"({escape(u['email'])}): {escape(u['summary'])}</li>"
            for u in stats["urgent"]) + "</ul>"
    else:
        urgent = "<p style=\"margin:6px 0;color:#666;\">No urgent messages this week.</p>"

    counts = ", ".join(f"{name}: {stats['categories'][name]}" for name in CATEGORIES)
    link = (f"<p style=\"margin:14px 0 0;\"><a href=\"{escape(dashboard_url)}\">"
            "Open the dashboard</a></p>") if dashboard_url else ""

    html = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;max-width:600px;\">"
        "<h2 style=\"margin:0 0 4px;\">ArtHouse weekly summary</h2>"
        f"<p style=\"margin:0 0 12px;color:#666;\">{stats['range_start']} to {stats['range_end']}</p>"
        f"<p style=\"margin:6px 0;\"><strong>{stats['total']}</strong> new form entries this week "
        f"({stats['registrations']} registration, {stats['contact_us']} contact us).</p>"
        f"<p style=\"margin:6px 0;\"><strong>Revenue this week:</strong> ${stats['revenue']:.2f}</p>"
        "<p style=\"margin:12px 0 4px;\"><strong>Contact Us by category</strong></p>"
        f"<p style=\"margin:0 0 6px;\">{counts}</p>"
        "<p style=\"margin:12px 0 4px;\"><strong>Urgent messages</strong></p>"
        f"{urgent}{link}</div>"
    )
    subject = f"ArtHouse weekly summary, {stats['range_start']} to {stats['range_end']}"
    return subject, html


def last_sent(state_rows):
    for row in state_rows:
        if str(row.get("key", "")).strip() == STATE_KEY:
            return str(row.get("value", "")).strip()
    return ""


def should_send(state_rows, send_day, now):
    """The two gates. Returns the reason to skip, or None to go ahead."""
    if now.strftime("%A").lower() != str(send_day).strip().lower():
        return f"today is {now.strftime('%A').lower()}, not {send_day}"
    if last_sent(state_rows) == now.strftime("%Y-%m-%d"):
        return "already sent today"
    return None


def send(settings, subject, html):
    """Gmail over SMTP with an app password.

    Not the Gmail API, on purpose. The API would need an OAuth refresh token,
    and Google expires those after seven days while the consent screen is in
    testing mode, so a weekly job would break about every other run. An app
    password does not expire.
    """
    message = EmailMessage()
    message["From"] = settings["sender"]
    message["To"] = settings["recipient"]
    message["Subject"] = subject
    message.set_content("This summary is HTML. Open it in a mail client that renders HTML.")
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(settings["sender"], settings["app_password"])
        smtp.send_message(message)
    log.info("sent", to=settings["recipient"], subject=subject)
