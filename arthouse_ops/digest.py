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
    # What a reader sees. The ISO pair above stays because it is unambiguous
    # and it is what the state tab and any future export want. The day number
    # is built by hand rather than with %-d, which is not portable.
    #
    # The year is written once when both ends share it, and twice when the week
    # crosses New Year, which the last week of December does.
    start = f"{cutoff.strftime('%B')} {cutoff.day}"
    end = f"{now.strftime('%B')} {now.day}, {now.year}"
    if cutoff.year != now.year:
        start += f", {cutoff.year}"
    stats["range_label"] = f"{start} to {end}"
    return stats


def escape(text):
    """Names and summaries come from a public form and land in HTML."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

# ArtHouse brand, sampled off the logo at
# arthousestudioca.org/wp-content/uploads/2022/04/Arthouse_logo_Final.png
CHARCOAL = "#333132"
TERRACOTTA = "#B56859"
TERRACOTTA_DARK = "#9C5749"
CREAM = "#FAF6F4"
LINE = "#E8DEDA"
MUTED = "#8A8385"
# The sage green the site wears, sampled off the header wordmark on
# arthousestudioca.org rather than off the logo file. The two carry different
# versions of the mark and only the one on the site is green.
SAGE = "#709994"
SAGE_PALE = "#F1F6F5"
SAGE_LINE = "#D8E5E2"
LOGO_URL = ("https://arthousestudioca.org/wp-content/uploads/2022/04/"
            "Arthouse_logo_Final.png")


def kpi(label, value, ink=TERRACOTTA, background=CREAM, border=LINE):
    """One of the two figures at the top. A table cell, because a phone mail
    client will not lay out flexbox. The two cards take different colours so
    the eye reads them as two separate facts rather than one block."""
    return (
        f'<td width="50%" valign="top" style="padding:0 6px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{background};border:1px solid {border};border-radius:10px;">'
        f'<tr><td style="padding:16px 18px;">'
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'letter-spacing:.8px;text-transform:uppercase;color:{MUTED};font-weight:bold;">{label}</div>'
        f'<div style="font-family:Georgia,\'Times New Roman\',serif;font-size:30px;'
        f'line-height:1.1;color:{ink};padding-top:6px;">{value}</div>'
        f'</td></tr></table></td>'
    )


def render(stats, dashboard_url=""):
    if stats["urgent"]:
        urgent = "".join(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:0 0 8px;"><tr>'
            f'<td width="3" style="background:{TERRACOTTA};border-radius:2px;"></td>'
            f'<td style="padding:8px 0 8px 12px;font-family:Arial,Helvetica,sans-serif;font-size:14px;">'
            f'<div style="color:{CHARCOAL};font-weight:bold;">{escape(u["name"])}</div>'
            f'<div style="color:{MUTED};font-size:13px;padding:1px 0 3px;">{escape(u["email"])}</div>'
            f'<div style="color:#4A4547;">{escape(u["summary"])}</div>'
            f'</td></tr></table>'
            for u in stats["urgent"])
    else:
        urgent = (f'<p style="margin:0;font-family:Arial,Helvetica,sans-serif;'
                  f'font-size:14px;color:{MUTED};">No urgent messages this week.</p>')

    rows = "".join(
        f'<tr>'
        f'<td style="padding:7px 0;border-bottom:1px solid {LINE};'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'color:{CHARCOAL if stats["categories"][name] else MUTED};">{name.capitalize()}</td>'
        f'<td align="right" style="padding:7px 0;border-bottom:1px solid {LINE};'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:bold;'
        f'color:{TERRACOTTA if stats["categories"][name] else MUTED};">'
        f'{stats["categories"][name]}</td></tr>'
        for name in CATEGORIES)

    if dashboard_url:
        button = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:26px 0 0;"><tr>'
            f'<td align="center" bgcolor="{TERRACOTTA}" style="border-radius:8px;">'
            f'<a href="{escape(dashboard_url)}" '
            f'style="display:inline-block;padding:13px 26px;'
            f'font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:bold;'
            f'color:#FFFFFF;text-decoration:none;">Open the Dashboard</a>'
            f'</td></tr></table>'
        )
    else:
        button = ""

    section = (f'font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:bold;'
               f'letter-spacing:1px;text-transform:uppercase;color:{SAGE};'
               f'padding:26px 0 10px;')

    html = f"""<body style="margin:0;padding:0;background:#F2EEEC;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F2EEEC;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
  style="width:100%;max-width:600px;background:#FFFFFF;border-radius:14px;overflow:hidden;">

  <tr><td style="background:{SAGE};height:5px;line-height:5px;font-size:0;">&nbsp;</td></tr>

  <tr><td align="center" style="padding:28px 24px 0;">
    <img src="{LOGO_URL}" width="200" alt="ArtHouse Studio"
      style="width:200px;max-width:70%;height:auto;display:block;border:0;" />
  </td></tr>

  <tr><td align="center" style="padding:18px 24px 0;">
    <div style="font-family:Georgia,'Times New Roman',serif;font-size:25px;color:{CHARCOAL};">Weekly Summary</div>
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:{MUTED};padding-top:5px;">
      {stats['range_label']}</div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:16px auto 0;"><tr>
      <td style="width:26px;height:3px;line-height:3px;font-size:0;background:{TERRACOTTA};">&nbsp;</td>
      <td style="width:26px;height:3px;line-height:3px;font-size:0;background:{SAGE};">&nbsp;</td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:24px 18px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      {kpi("New Entries", stats['total'], SAGE, SAGE_PALE, SAGE_LINE)}
      {kpi("Revenue this Week", f"${stats['revenue']:,.2f}")}
    </tr></table>
  </td></tr>

  <tr><td style="padding:14px 24px 0;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:{MUTED};">
    {stats['total']} New Form Entries this Week
    ({stats['registrations']} Registration, {stats['contact_us']} Contact Us).
  </td></tr>

  <tr><td style="padding:0 24px;">
    <div style="{section}">Contact Us by Category</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>

    <div style="{section}">Urgent Messages</div>
    {urgent}
    {button}
  </td></tr>

  <tr><td style="padding:26px 24px 28px;">
    <div style="border-top:1px solid {LINE};padding-top:14px;
      font-family:Arial,Helvetica,sans-serif;font-size:11px;color:{MUTED};line-height:1.6;">
      Sent automatically every Sunday morning by the ArtHouse lead pipeline.<br />
      The dashboard needs the shared password. Reply to this email if you need it again.
    </div>
  </td></tr>

</table></td></tr></table></body>"""

    subject = f"ArtHouse Weekly Summary, {stats['range_label']}"
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
