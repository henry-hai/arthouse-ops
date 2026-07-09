# arthouse-ops

An n8n automation that unifies ArtHouse Studio's WordPress form submissions into
a single Google Sheet with a custom lead-triage web app on top, plus a Claude LLM
classifier that categorizes Contact Us messages. The entire workflow is defined
as version-controlled JSON and imported into n8n through the CLI, rather than
assembled by hand in the visual editor.

## What it does

ArtHouse Studio is a 501(c)(3) nonprofit in San Jose serving Bay Area Title I
schools. Its WordPress site collects three forms through the Visual Form Builder
Pro plugin, but the plugin's Entries page throws a fatal error, so staff have no
working view of their own data. This project pulls the form entries through a
custom WordPress plugin, cleans them,
strips student PII, uses Claude Haiku 4.5 to categorize inbound messages, and
writes everything to a Google Sheet that feeds a live dashboard and a weekly
email summary.

## Architecture

Two triggers (a Manual Trigger for demos and a daily Schedule Trigger) feed one
Config node that centralizes every setting and reads from environment variables.
The flow then branches into three paths that all write to one Google Sheet. The
leads tab is the single source of truth: every write is an upsert keyed on the
entry id, so a run is idempotent and resumable. Re-running never duplicates a
row, and an interrupted backfill can simply be run again to finish. A custom web
app reads and aggregates enrollment, revenue, and message counts directly from
the leads tab, so the workflow keeps no separate summary table to fall out of
sync.

- **Registration path**: fetch the Registration entries from the connector plugin, parse them, strip student
  PII, and upsert the follow-up leads into the leads tab keyed by entry id.
- **Contact Us path**: fetch the Contact Us entries from the connector plugin, read the existing leads to skip
  anything already classified, parse, dedupe, classify each new message with
  Claude, validate the result, and upsert the leads keyed by entry id.
- **Notification path**: read recent leads, build a weekly HTML summary (only on
  the configured send day, default Sunday), send it via Gmail, and record the
  send so a repeat run the same day does not email twice.

A separate error workflow catches any node failure and appends a row to an
errors tab.

## Node-by-node flow

1. **Manual Trigger** starts a run on demand for demos.
2. **Schedule Trigger** starts a run weekly on Sunday at 2:00 PM Pacific time.
3. **Config** defines every setting (URLs, form ids, sheet id, tab names,
   category and sentiment lists, model, backfill flag, date ranges) from env.
4. **Fetch Registration Entries** GETs the connector plugin with WordPress Basic
   Auth and returns entries as JSON, paginated over the full history.
5. **Parse Registration Entries** flattens the plugin's JSON entries into one row
   per entry, keyed by field label.
6. **Strip PII from Registration** drops sensitive student fields and keeps only
   the whitelisted follow-up fields.
7. **Append Registration Leads** upserts the PII-stripped detail rows into the
   leads tab, keyed on entry id.
8. **Fetch Contact Us Entries** GETs the connector plugin for the Contact Us
   form, paginated.
9. **Read Existing Leads** reads the leads tab to know what has already been
   classified.
10. **Parse Contact Us Entries** flattens the plugin's JSON entries into rows.
11. **Dedupe Against Sheet** normalizes each row and skips only entries that
    already hold a successful classification, so new and previously failed rows
    pass through to be (re)classified.
12. **Classify Message** calls the Anthropic API for each new message and asks
    for structured JSON, with batching to stay within rate limits.
13. **Apply Classification** parses and validates the model output, falling back
    to safe defaults on any bad response.
14. **Append Contact Us Leads** upserts each classified message into the leads
    tab, keyed on entry id.
15. **Read Email State** reads the date a summary was last sent (duplicate guard).
16. **Read Recent Leads** reads the leads tab for the weekly summary.
17. **Build Weekly Summary** composes the email on the configured send day
    (default Sunday) and skips if it is not that day or one already went out today.
18. **Send Weekly Summary** sends the email through Gmail.
19. **Record Email Sent** records today in the state tab so a repeat run the same
    day does not email again.

Plus a separate error workflow: **Error Trigger** to **Format Error Row** to
**Append Error**.

## Setup, in order

Follow these nine steps top to bottom. Each one is required before the next.

### Step 1: Create an Anthropic API account and fund it

The Contact Us classifier calls Claude, which bills usage to your account. Go to
https://console.anthropic.com, sign up, add a payment method and credit under
Billing, then under API Keys click Create Key. Copy the key (shown once) and
paste it into `.env` as `ANTHROPIC_API_KEY`.

### Step 2: Install the connector plugin and create a WordPress Application Password

The form plugin's own Entries screen throws a fatal error, so this project reads
the entries through a small custom plugin in `wordpress-plugin/`. Install it once:
in wp-admin go to Plugins, then Add New Plugin, then Upload Plugin, upload
`wordpress-plugin/arthouse-ops-connector.zip` (zip the `.php` if the zip is not
present), and Activate. It exposes read-only, authenticated REST endpoints that
return the form entries as JSON, and it reads them straight from the database so
it is unaffected by the broken Entries screen.

The workflow authenticates to that plugin with a purpose-built credential. Sign in
at https://arthousestudioca.org/wp-admin, open Users then Profile, scroll to
Application Passwords, enter the name `n8n arthouse-ops`, click Add New Application
Password, and copy the generated code (shown once). Put your WordPress username in
`.env` as `WP_USERNAME` and the code as `WP_APP_PASSWORD`.

### Step 3: Confirm your Google Sheet and grab the Sheet ID

Open your `arthouse-ops` spreadsheet at https://sheets.google.com. The Sheet ID
is the long string in the URL between `/d/` and `/edit`. Paste it into `.env` as
`GOOGLE_SHEET_ID`. Create three tabs named `leads`, `errors`, and `state`, and
put the matching header row (see the Data model section below) in row 1 of each,
one column name per cell.

### Step 4: Fill in .env with real values

Copy the example and edit it:

```bash
cp .env.example .env
```

Fill in these values:

- `ANTHROPIC_API_KEY` from Step 1
- `WP_USERNAME` and `WP_APP_PASSWORD` from Step 2
- `GOOGLE_SHEET_ID` from Step 3
- `WEEKLY_SUMMARY_RECIPIENT` (the email that receives the Monday summary)
- `LOOKER_DASHBOARD_URL` (the dashboard link in the weekly email) can stay a
  placeholder until the dashboard web app is deployed
- leave `BACKFILL_MODE=true` for the first run

The real `.env` is gitignored and never committed.

### Step 5: Start n8n

```bash
docker compose up -d
```

This runs n8n at http://localhost:5678 with a named volume so your login and
imported workflows survive a restart. Wait until the container reports healthy
(about 30 seconds).

### Step 6: Import the workflow

```bash
bash scripts/import-workflow.sh
```

This loads the credential scaffolds and both workflows into the running
container. It is safe to run more than once.

### Step 7: Complete OAuth in the browser

Self-hosted n8n needs its own Google sign-in app before it can reach your Sheet
and Gmail. In the Google Cloud Console create a project, enable the Google Sheets
API, Google Drive API, and Gmail API, configure the OAuth consent screen
(External, with your own email added as a test user), and create an OAuth client
ID of type Web application with this authorized redirect URI:

```
http://localhost:5678/rest/oauth2-credential/callback
```

Then in n8n at http://localhost:5678, open Credentials, click ArtHouse Google
Sheets, paste the Client ID and Client Secret, click Sign in with Google, and
approve. Repeat for ArtHouse Gmail using the same Client ID and Secret. The
WordPress and Anthropic credentials need nothing here because they read from
`.env` automatically.

### Step 8: Run the initial backfill

Open http://localhost:5678, open the arthouse-ops workflow, and click Execute.
With `BACKFILL_MODE=true` this processes the full history and classifies every
Contact Us message, which takes about 20 to 40 minutes. You can also run it from
the terminal with `bash scripts/run-workflow.sh`.

### Step 9: Flip BACKFILL_MODE to false

After the backfill finishes successfully, set `BACKFILL_MODE=false` in `.env` and
restart with `docker compose up -d`. From then on the daily run pulls only recent
data (Registration last 400 days, Contact Us last 30 days) and dedupes against
what is already stored, so it stays fast and light.

## Switching LLM provider

Claude Haiku 4.5 is the default because it is fast and well suited to
high-volume classification. The provider and model are set by `LLM_PROVIDER` and
`LLM_MODEL` in `.env`. To move to a different provider such as OpenAI GPT-4o-mini, point the
Classify Message node at that provider's endpoint, set the matching auth header
credential, and set `LLM_MODEL` to the new model id. The rest of the workflow is
provider-agnostic because it only reads category, sentiment, and summary from the
JSON response.

## Data model

Paste these header rows into row 1 of each tab, one name per cell.

**leads** (the single source of truth: one row per submission, upserted by
`entry_id`; drives both follow-up and the dashboard)

```
entry_id,entry_date,source,name,email,school,grade,homeroom_teacher,amount_usd,category,sentiment,summary
```

**errors** (failure log)

```
timestamp,node_name,error_message,workflow_run_id
```

**state** (internal, for the once-per-day weekly-email guard)

```
key,value
```

On the registration side the shared `name` and `email` columns hold the parent
or guardian values. On the contact side they hold the sender's values and
`school` holds the Company/School field.

## Privacy and PII handling

Registration submissions contain sensitive student information. The Strip PII
node enforces a hard boundary and never passes these fields downstream:

- Student's Name
- Date of Birth
- Address
- Allergies, Medical Problems, and Comments
- Names of other adults authorized to pick up
- Emergency Contact Name
- Emergency Contact Phone Number

Only these non-sensitive fields are kept for follow-up: Entry ID, Entry Date,
Parent or Guardian Name, Parent Email, school name, Grade, the Register and Pay
amount, and Homeroom Teacher. If the export layout changes and an expected column
goes missing, the node logs a warning rather than failing silently.

## Dashboard

A custom web app in `dashboard/` sits on top of the Google Sheet as the
leadership view. Rather than a point-and-click BI report, it is a small
application (HTML/CSS/JS on a Google Apps Script backend) that reads the leads tab
live and gives staff:

- top-line KPIs (enrollment, revenue, message volume, and how many messages need
  a human),
- an interactive "action center" that filters the AI-surfaced sponsor, school,
  volunteer, and urgent messages out of the ~18k so staff can read and follow up,
- an enrollment-and-revenue trend and a category breakdown.

It deploys as a Google Apps Script web app behind a server-enforced password gate,
so the contact data is never on a public link, and it costs nothing to host and
stays current from the same live sheet. See `dashboard/` for the code and
deployment steps.

The leads tab is also BI-tool-ready: a Looker Studio / Data Studio report can be
layered on the same data (leads tab as a source, a month dimension on
`entry_date`, then scorecards, a trend, category/school bars, and a table) if a
stakeholder prefers one.

## Troubleshooting

- **OAuth expired or disconnected**: reopen the credential in n8n and click Sign
  in with Google again. Make sure your email is still a test user on the OAuth
  consent screen.
- **redirect_uri_mismatch**: the redirect URI in Google Cloud must exactly match
  the one shown in the n8n credential, including http and the path.
- **Sheets node errors after connecting**: confirm both the Google Sheets API and
  the Google Drive API are enabled in your Google Cloud project.
- **WordPress fatal on the VFB Pro Entries page**: unrelated and expected. This
  project uses the CSV export, not the Entries screen.
- **Rate limit hits during the backfill**: the Classify node batches requests. If
  you still hit limits, lower the batch size or raise the interval in that node,
  or raise your Anthropic rate tier.
- **Docker will not start**: make sure Docker Desktop is running, then
  `docker compose up -d` and check `docker compose logs -f n8n`.
- **.env not being read**: confirm the file is named exactly `.env` in the
  project root and restart with `docker compose up -d` after any change.
