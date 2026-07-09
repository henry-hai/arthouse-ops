# Lead-triage dashboard

A small internal web app that sits on top of the `leads` tab and turns the AI
classifier's output into an actual workflow. Instead of a point-and-click BI
report, it is a lightweight application served by Google Apps Script.

## What it shows

- **KPIs** — total enrollment, revenue (Register-and-Pay), message volume, and
  how many messages "need a human".
- **Action Center** — the heart of it: an interactive list of the messages the
  classifier flagged as sponsor / school / volunteer leads or urgent, filterable
  by one click, so staff triage the ~140 that matter out of ~18,000 instead of
  scrolling a spreadsheet.
- **Context** — an enrollment-and-revenue trend by quarter and a category
  breakdown.

## Why Apps Script

The Google Sheet lives on a personal Google account, and the Action Center shows
real names and emails, so the view cannot be a public page. Apps Script runs the
app on Google's infrastructure for free, reads the sheet directly (no API keys),
and — because the data is PII — the backend enforces a **server-side password
gate**: `getDashboardData()` returns nothing unless the caller supplies the
shared password, so the URL alone exposes nothing.

## Files

- `Code.gs` — backend. Serves the page, reads the `leads` tab, computes the KPIs,
  quarterly trend, and category counts, and returns only the actionable messages
  (never the full sheet to the browser). Holds the `ACCESS_PASSWORD` gate.
- `Index.html` — front-end. Login screen, then the dashboard, rendered from the
  data the backend returns.

## Deploy

1. Open the `arthouse-ops` Google Sheet → **Extensions → Apps Script** (this binds
   the script to the sheet so it can read it directly).
2. Replace the default `Code.gs` with `Code.gs` here, and set `ACCESS_PASSWORD` to
   your own value (keep the committed file's placeholder — don't commit a real
   password).
3. Add an HTML file named `Index` and paste `Index.html`.
4. **Deploy → New deployment → Web app**: execute as **Me**, access **Anyone**
   (the password gate is what protects it). Authorize when prompted.
5. Open the web-app URL, enter the password, and the dashboard loads live from the
   sheet. Share the URL + password only with the intended viewer.