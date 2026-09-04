"""Idempotency, resumability and the error path, against fake collaborators."""

from arthouse_ops import classify as classify_module
from arthouse_ops import parsing, run
from arthouse_ops.config import LEADS_HEADERS

FAILED = classify_module.FAILED


def sheet_row(entry_id, row_number, summary="Asks about signup.", source="contact_us"):
    record = {column: "" for column in LEADS_HEADERS}
    record.update({"entry_id": entry_id, "source": source, "summary": summary,
                   "_row": row_number})
    return record


class FakeSheet:
    """A leads tab in memory, with the same upsert contract as the real one."""

    def __init__(self, rows=None, fail_reads=False, fail_writes_after=None):
        self.rows = rows or []
        self.errors = []
        self.fail_reads = fail_reads
        self.fail_writes_after = fail_writes_after
        self.write_calls = 0

    def read_rows(self, tab, headers):
        if self.fail_reads:
            raise RuntimeError("sheet unavailable")
        return list(self.rows)

    def upsert_leads(self, leads, index):
        self.write_calls += 1
        if self.fail_writes_after is not None and self.write_calls > self.fail_writes_after:
            raise RuntimeError("quota exhausted")
        updated = appended = 0
        for lead in leads:
            entry_id = str(lead["entry_id"])
            if entry_id in index:
                self.rows[index[entry_id] - 2] = dict(lead, _row=index[entry_id])
                updated += 1
            else:
                row_number = len(self.rows) + 2
                index[entry_id] = row_number
                self.rows.append(dict(lead, _row=row_number))
                appended += 1
        return updated, appended

    def append_errors(self, rows):
        self.errors.extend(rows)
        return len(rows)

    def entry_ids(self):
        return [row["entry_id"] for row in self.rows]


class FakeWordPress:
    def __init__(self, pages_by_form, fail_forms=()):
        self.pages_by_form = pages_by_form
        self.fail_forms = fail_forms

    def fetch_all(self, form_id):
        if form_id in self.fail_forms:
            raise RuntimeError("wordpress 503")
        return self.pages_by_form.get(form_id, [])


class FakeClassifier:
    def __init__(self, fail_on=(), raise_after=None):
        self.seen = []
        self.fail_on = set(fail_on)
        self.raise_after = raise_after

    def classify(self, row):
        entry_id = row["entry_id"]
        if self.raise_after is not None and len(self.seen) >= self.raise_after:
            raise KeyboardInterrupt("run cancelled")
        self.seen.append(entry_id)
        if entry_id in self.fail_on:
            return {"category": "general", "sentiment": "neutral", "summary": FAILED}, RuntimeError("529")
        return {"category": "sponsor", "sentiment": "positive",
                "summary": f"summary for {entry_id}"}, None


def make_run(settings, sheet, wordpress, classifier, **kwargs):
    return run.Run(settings, wordpress, sheet, classifier, **kwargs)


def pages(contact_us_page=None, registration_page=None):
    return {"3": [contact_us_page] if contact_us_page else [],
            "2": [registration_page] if registration_page else []}


# --- idempotency ---

def test_a_second_run_writes_no_new_rows(settings, contact_us_page, registration_page):
    sheet = FakeSheet()
    wordpress = FakeWordPress(pages(contact_us_page, registration_page))

    make_run(settings, sheet, wordpress, FakeClassifier()).execute()
    first = list(sheet.entry_ids())
    assert len(first) == 6

    make_run(settings, sheet, wordpress, FakeClassifier()).execute()
    assert sheet.entry_ids() == first


def test_an_already_classified_entry_is_not_sent_to_the_model(settings, contact_us_page):
    sheet = FakeSheet([sheet_row("22801", 2), sheet_row("22802", 3)])
    classifier = FakeClassifier()
    result = make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), classifier)
    result.execute(sources=("contact_us",))
    assert classifier.seen == ["22803", "22804"]
    assert result.stats["skipped"] == 2


def test_a_failed_classification_is_retried_next_run(settings, contact_us_page):
    """The failure sentinel in the summary column is what marks it unfinished."""
    sheet = FakeSheet([sheet_row("22801", 2, summary=FAILED)])
    classifier = FakeClassifier()
    make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), classifier).execute(
        sources=("contact_us",))
    assert "22801" in classifier.seen


def test_a_registration_row_never_counts_as_classified(settings, contact_us_page):
    """Registration rows have an empty summary, and must not be read as done."""
    sheet = FakeSheet([sheet_row("22801", 2, summary="", source="registration")])
    classifier = FakeClassifier()
    make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), classifier).execute(
        sources=("contact_us",))
    assert "22801" in classifier.seen


def test_reclassifying_updates_the_row_in_place(settings, contact_us_page):
    sheet = FakeSheet([sheet_row("22801", 2, summary=FAILED)])
    make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), FakeClassifier()).execute(
        sources=("contact_us",))
    matching = [row for row in sheet.rows if row["entry_id"] == "22801"]
    assert len(matching) == 1
    assert matching[0]["summary"] == "summary for 22801"


# --- partial failure ---

def test_an_interrupted_run_keeps_the_batches_it_finished(settings, contact_us_page):
    """write_batch_size is 2 in the test settings, so two entries are committed
    before the third raises. Those two must survive."""
    sheet = FakeSheet()
    classifier = FakeClassifier(raise_after=3)
    runner = make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), classifier)
    try:
        runner.execute(sources=("contact_us",))
    except KeyboardInterrupt:
        pass
    assert sheet.entry_ids() == ["22801", "22802"]


def test_the_next_run_resumes_where_the_last_one_stopped(settings, contact_us_page):
    sheet = FakeSheet()
    try:
        make_run(settings, sheet, FakeWordPress(pages(contact_us_page)),
                 FakeClassifier(raise_after=3)).execute(sources=("contact_us",))
    except KeyboardInterrupt:
        pass
    classifier = FakeClassifier()
    make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), classifier).execute(
        sources=("contact_us",))
    assert classifier.seen == ["22803", "22804"]
    assert len(sheet.rows) == 4


def test_a_failed_write_does_not_end_the_run(settings, contact_us_page):
    sheet = FakeSheet(fail_writes_after=1)
    runner = make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), FakeClassifier())
    assert runner.execute(sources=("contact_us",)) == 1
    assert any(e["node_name"] == "append_contact_us_leads" for e in sheet.errors)


# --- the error path ---

def test_a_classification_failure_becomes_an_error_row(settings, contact_us_page):
    sheet = FakeSheet()
    runner = make_run(settings, sheet, FakeWordPress(pages(contact_us_page)),
                      FakeClassifier(fail_on=["22802"]))
    runner.execute(sources=("contact_us",))
    assert len(sheet.errors) == 1
    assert sheet.errors[0]["node_name"] == "classify_message"
    assert "entry 22802" in sheet.errors[0]["error_message"]
    assert sheet.errors[0]["workflow_run_id"]


def test_the_failed_entry_is_still_written_so_it_can_be_retried(settings, contact_us_page):
    sheet = FakeSheet()
    make_run(settings, sheet, FakeWordPress(pages(contact_us_page)),
             FakeClassifier(fail_on=["22802"])).execute(sources=("contact_us",))
    row = [r for r in sheet.rows if r["entry_id"] == "22802"][0]
    assert row["summary"] == FAILED


def test_one_form_failing_does_not_stop_the_other(settings, contact_us_page, registration_page):
    sheet = FakeSheet()
    wordpress = FakeWordPress(pages(contact_us_page, registration_page), fail_forms=("2",))
    runner = make_run(settings, sheet, wordpress, FakeClassifier())
    assert runner.execute() == 1
    assert sorted(sheet.entry_ids()) == ["22801", "22802", "22803", "22804"]
    assert sheet.errors[0]["node_name"] == "fetch_registration_entries"


def test_an_unreadable_sheet_stops_before_any_write(settings, contact_us_page):
    """Without the existing rows there is no dedupe, and writing anyway would
    duplicate the whole tab. Stop instead."""
    sheet = FakeSheet(fail_reads=True)
    classifier = FakeClassifier()
    runner = make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), classifier)
    assert runner.execute() == 1
    assert classifier.seen == []
    assert sheet.write_calls == 0


def test_error_messages_are_one_line_and_capped():
    row = run.error_row("classify_message", "line one\n   line two " + "x" * 500, entry_id="9")
    assert "\n" not in row["error_message"]
    assert len(row["error_message"]) == 400
    assert row["error_message"].startswith("entry 9: line one line two")


def test_a_clean_run_reports_success(settings, contact_us_page):
    runner = make_run(settings, FakeSheet(), FakeWordPress(pages(contact_us_page)),
                      FakeClassifier())
    assert runner.execute(sources=("contact_us",)) == 0


def test_dry_run_writes_nothing(settings, contact_us_page):
    sheet = FakeSheet()
    make_run(settings, sheet, FakeWordPress(pages(contact_us_page)), FakeClassifier(),
             dry_run=True).execute(sources=("contact_us",))
    assert sheet.rows == []
    assert sheet.errors == []


def test_the_limit_caps_how_many_entries_are_classified(settings, contact_us_page):
    classifier = FakeClassifier()
    runner = make_run(settings, FakeSheet(), FakeWordPress(pages(contact_us_page)),
                      classifier, limit=1)
    runner.execute(sources=("contact_us",))
    assert classifier.seen == ["22801"]


def test_a_capped_run_leaves_the_rest_for_the_next_one(settings, contact_us_page):
    sheet = FakeSheet()
    wordpress = FakeWordPress(pages(contact_us_page))
    make_run(settings, sheet, wordpress, FakeClassifier(), limit=2).execute(sources=("contact_us",))
    classifier = FakeClassifier()
    make_run(settings, sheet, wordpress, classifier, limit=2).execute(sources=("contact_us",))
    assert classifier.seen == ["22803", "22804"]
