"""The pipeline itself: fetch, classify, write.

Two paths, matching the workflow. Registration entries are stripped of the
sensitive fields and upserted. Contact Us entries are deduped against what the
sheet already holds, classified, and upserted in batches.

Batching the writes is what makes a partial failure survivable. Every flushed
batch is in the sheet with a real summary, and the dedupe step at the start of
the next run treats those entries as done, so a run that dies at entry 900 of
1200 resumes at 900 rather than at 1.
"""

import time

from . import classify as classify_module
from . import logs, parsing
from .config import ERROR_HEADERS, LEADS_HEADERS

log = logs.get("run")

# The summary value that means an entry still needs classifying.
FAILED = classify_module.FAILED


def build_lead_index(rows):
    """entry_id -> sheet row number, for every row already in the leads tab."""
    index = {}
    for row in rows:
        entry_id = str(row.get("entry_id", "")).strip()
        if entry_id:
            index[entry_id] = row["_row"]
    return index


def classified_entry_ids(rows):
    """Entry ids whose classification succeeded, so they can be skipped.

    A row whose summary is empty is a registration row and never qualifies. A
    row carrying the failure sentinel is unfinished and gets another attempt.
    """
    done = set()
    for row in rows:
        entry_id = str(row.get("entry_id", "")).strip()
        summary = str(row.get("summary", "")).strip()
        if entry_id and summary and summary != FAILED:
            done.add(entry_id)
    return done


def error_row(stage, message, entry_id=""):
    prefix = f"entry {entry_id}: " if entry_id else ""
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node_name": stage,
        "error_message": (prefix + " ".join(str(message).split()))[:400],
        "workflow_run_id": logs.RUN_ID,
    }


class NullSheet:
    """Stands in for the sheet on a dry run, so the pipeline can be exercised
    end to end before any Google credential exists."""

    def read_rows(self, tab, headers):
        return []

    def upsert_leads(self, leads, index):
        return 0, len(leads)

    def append_errors(self, rows):
        return len(rows)


class Run:
    def __init__(self, config, wordpress, sheet, classifier, dry_run=False, limit=None):
        self.config = config
        self.wordpress = wordpress
        self.sheet = sheet
        self.classifier = classifier
        self.dry_run = dry_run
        # Caps how many entries are classified in one run. The backfill is
        # eighteen thousand entries, so a first pass wants a ceiling, and the
        # dedupe means the next run picks up where this one left off.
        self.limit = limit
        self.errors = []
        self.stats = {"registration": 0, "contact_us": 0, "skipped": 0, "failed": 0}

    def record_error(self, stage, exc, entry_id=""):
        self.errors.append(error_row(stage, exc, entry_id))
        log.error(stage + " failed", entry_id=entry_id, cause=str(exc)[:200])

    def registration(self, lead_index):
        try:
            pages = self.wordpress.fetch_all(self.config.registration_form_id)
        except Exception as exc:  # noqa: BLE001 - one path failing must not stop the other
            self.record_error("fetch_registration_entries", exc)
            return

        rows = parsing.flatten_entries(pages)
        leads, warned, scrubbed = [], False, 0
        for row in rows:
            lead, dropped = parsing.registration_lead(row)
            missing = parsing.registration_schema_warning(lead)
            if missing and not warned:
                log.warn("registration columns not found, the form layout may have changed",
                         missing=missing)
                warned = True
            if dropped:
                scrubbed += 1
                log.warn("blanked a field holding data from a sensitive column",
                         entry_id=lead["entry_id"], fields=dropped)
            leads.append(lead)

        log.info("parsed registration entries", entries=len(rows), scrubbed=scrubbed)
        self.stats["registration"] = self._write(leads, lead_index, "append_registration_leads")

    def contact_us(self, lead_index, done):
        try:
            pages = self.wordpress.fetch_all(self.config.contact_us_form_id)
        except Exception as exc:  # noqa: BLE001
            self.record_error("fetch_contact_us_entries", exc)
            return

        rows = [parsing.contact_us_row(row) for row in parsing.flatten_entries(pages)]
        pending = [row for row in rows if str(row["entry_id"]).strip() not in done]
        self.stats["skipped"] = len(rows) - len(pending)
        capped = len(pending)
        if self.limit is not None:
            pending = pending[:self.limit]
        log.info("parsed contact us entries", entries=len(rows),
                 pending=capped, classifying=len(pending),
                 already_classified=self.stats["skipped"])

        batch = []
        for row in pending:
            result, exc = self.classifier.classify(row)
            if exc is not None:
                self.record_error("classify_message", exc, row["entry_id"])
            if result["summary"] == FAILED:
                self.stats["failed"] += 1
            batch.append(parsing.contact_us_lead(row, result))
            if len(batch) >= self.config.write_batch_size:
                self.stats["contact_us"] += self._write(batch, lead_index, "append_contact_us_leads")
                batch = []
        if batch:
            self.stats["contact_us"] += self._write(batch, lead_index, "append_contact_us_leads")

    def _write(self, leads, lead_index, stage):
        if not leads:
            return 0
        if self.dry_run:
            log.info("dry run, not writing", stage=stage, rows=len(leads))
            return len(leads)
        try:
            updated, appended = self.sheet.upsert_leads(leads, lead_index)
            return updated + appended
        except Exception as exc:  # noqa: BLE001 - a failed batch is recorded, the run continues
            self.record_error(stage, exc)
            return 0

    def flush_errors(self):
        """The error path. Failures are rows in the errors tab, same as before.

        This is best effort by design: if the sheet is what is broken, there is
        nowhere to write, and the structured log still holds every failure.
        """
        if not self.errors or self.dry_run:
            return
        try:
            self.sheet.append_errors(self.errors)
        except Exception as exc:  # noqa: BLE001
            log.error("could not write the errors tab", cause=str(exc)[:200])

    def execute(self, sources=("registration", "contact_us")):
        started = time.time()
        log.info("run started", **self.config.redacted(), dry_run=self.dry_run)

        try:
            existing = self.sheet.read_rows(self.config.tab_leads, LEADS_HEADERS)
        except Exception as exc:  # noqa: BLE001
            self.record_error("read_existing_leads", exc)
            log.error("cannot read the leads tab, stopping before any write")
            self.flush_errors()
            return 1

        lead_index = build_lead_index(existing)
        done = classified_entry_ids(existing)

        if "registration" in sources:
            self.registration(lead_index)
        if "contact_us" in sources:
            self.contact_us(lead_index, done)

        self.flush_errors()
        log.info("run finished", seconds=round(time.time() - started, 1),
                 errors=len(self.errors), **self.stats)
        return 1 if self.errors else 0
