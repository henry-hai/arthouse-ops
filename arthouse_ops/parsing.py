"""Turning WordPress form entries into leads rows.

Everything here is a pure function over plain dicts. That is deliberate: this
is the logic most likely to break when someone edits a form in WordPress, and
pure functions are the part that can be tested against a recorded payload
without touching the network.

The field pickers match on normalized header text rather than field ids because
Visual Form Builder renumbers ids when a form is rebuilt, but the labels the
staff type stay recognizable.
"""

import re

# Fields that never leave this module. The registration form collects data
# about children, and only the follow-up fields below are allowed downstream.
_SENSITIVE = ("date of birth", "birth", "address", "allerg", "medical",
              "authorized to pick", "other adults", "emergency")

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#039;": "'", "&apos;": "'",
}


def normalize(text):
    """Lowercase a header down to words, so 'Parent/Guardian Name' matches."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def strip_html(value):
    """Form bodies arrive wrapped in <p> tags. Flatten them to one line."""
    text = str(value)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"</p\s*>", " ", text, flags=re.I)
    text = _HTML_TAG.sub("", text)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return _WS.sub(" ", text).strip()


def is_sensitive(header):
    """True for a registration column that must never be emitted.

    "Student's Homeroom Teacher" has to survive, so the student rule needs both
    "student" and "name" rather than "student" on its own.
    """
    h = normalize(header)
    if any(word in h for word in _SENSITIVE):
        return True
    return "student" in h and "name" in h


def pick(row, word_sets, exclude=(), skip_sensitive=False):
    """First value whose header contains every word in one of the word sets.

    Word sets are tried in order, so the specific match ("parent name") gets a
    chance before the loose one ("name").
    """
    excluded = [normalize(word) for word in exclude]
    for words in word_sets:
        for key, value in row.items():
            if skip_sensitive and is_sensitive(key):
                continue
            nk = normalize(key)
            if any(word and word in nk for word in excluded):
                continue
            if all(normalize(word) in nk for word in words):
                return str(value).strip()
    return ""


def parse_currency(value):
    if value in (None, ""):
        return 0
    cleaned = re.sub(r"[$,\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return 0


def flatten_entries(pages):
    """Flatten the connector's paged {entries: [...]} responses into rows.

    Each row is keyed by the field label the form shows, plus Entry ID and
    Entry Date, which is the shape the pickers below expect.
    """
    rows = []
    for page in pages:
        for entry in page.get("entries") or []:
            row = {
                "Entry ID": str(entry.get("entry_id", "")),
                "Entry Date": str(entry.get("date", "")),
            }
            for label, value in (entry.get("fields") or {}).items():
                row[label] = "" if value is None else str(value).strip()
            # The Contact Us form puts the message body in Subject.
            if row.get("Subject") and not row.get("Message"):
                row["Message"] = row["Subject"]
            rows.append(row)
    return rows


# A school name, a grade and a teacher name are all short. A value longer than
# this is a parent using the box for something else, and whatever they wrote is
# not something the dashboard should be charting.
MAX_SHORT_FIELD = 80

_SHORT_FIELDS = ("school", "grade", "homeroom_teacher")


def sensitive_values(row):
    """Every value the row holds in a column that must not be emitted."""
    return {str(value).strip() for header, value in row.items()
            if is_sensitive(header) and str(value).strip()}


def scrub(lead, row):
    """Blank any emitted field that swallowed a value from a sensitive column.

    Dropping a column by header is not enough on its own. A parent who types
    "Robin has our permission to walk herself home" into the school-name box has
    put a student's name into an allowed field, and that field is bound for a
    sheet the dashboards read.

    The rule is containment, not equality. A value equal to a sensitive one
    came from the allowed column legitimately, which is the common case of a
    parent naming themselves as the emergency contact. A value that contains a
    sensitive one plus other text is free text that picked something up.

    Only the short descriptive fields are scrubbed. name and email are the
    parent contact and are the point of the row, and they collide with the
    sensitive columns constantly and harmlessly, because parents write
    "Dana Whitfield and Sam Booker" in one box and "Dana Whitfield" in
    another. Blanking those would cost real data and protect nothing.
    """
    banned = [value for value in sensitive_values(row) if len(value) >= 4]
    dropped = []
    for field in _SHORT_FIELDS:
        value = str(lead.get(field, ""))
        if not value:
            continue
        if any(item != value and item in value for item in banned):
            lead[field] = ""
            dropped.append(field)
        elif len(value) > MAX_SHORT_FIELD:
            lead[field] = ""
            dropped.append(field)
    return dropped


def registration_lead(row):
    """A registration entry as a leads row, with the sensitive fields dropped.

    The returned dict is built fresh from named fields rather than copied and
    pruned, so a new sensitive column added to the form in WordPress cannot
    leak through by default. scrub() then catches what got through an allowed
    column because a parent typed it in the wrong box.

    Returns the lead and the list of fields the scrub blanked, so a caller
    cannot end up with an unscrubbed row by forgetting to call scrub itself.
    """
    lead = {
        "entry_id": pick(row, [["entry", "id"], ["id"]], skip_sensitive=True),
        "entry_date": pick(row, [["entry", "date"], ["date", "submitted"], ["date"]], skip_sensitive=True),
        "source": "registration",
        "name": pick(row, [["parent", "name"], ["guardian", "name"], ["guardian"], ["parent"]], skip_sensitive=True),
        "email": pick(row, [["parent", "email"], ["guardian", "email"], ["email"]], skip_sensitive=True),
        "school": pick(row, [["school", "name"], ["school"]], skip_sensitive=True),
        "grade": pick(row, [["grade"]], skip_sensitive=True),
        "homeroom_teacher": pick(row, [["homeroom"], ["teacher"]], skip_sensitive=True),
        "amount_usd": parse_currency(pick(row, [["register", "pay"], ["amount"], ["pay"]], skip_sensitive=True)),
        "category": "",
        "sentiment": "",
        "summary": "",
    }
    return lead, scrub(lead, row)


def registration_schema_warning(lead):
    """Names the columns a registration row was expected to have and did not.

    A silent empty column here means someone rebuilt the form, so it is worth
    a log line rather than an empty cell nobody notices.
    """
    missing = [
        label for label, value in (
            ("Entry ID", lead["entry_id"]),
            ("Parent/Guardian Name", lead["name"]),
            ("Parent Email", lead["email"]),
        ) if not value
    ]
    return missing


def contact_us_row(row):
    """A Contact Us entry normalized to the fields the classifier reads."""
    return {
        "entry_id": pick(row, [["entry", "id"], ["id"]]),
        "entry_date": pick(row, [["entry", "date"], ["date", "submitted"], ["date"]]),
        "name": pick(row, [["first", "name"], ["full", "name"], ["name"]],
                     exclude=("school", "company", "user", "file")),
        "email": pick(row, [["email"]]),
        "phone": pick(row, [["phone"], ["telephone"], ["mobile"]]),
        "company_school": pick(row, [["company", "school"], ["company"], ["school"],
                                     ["organization"], ["organisation"]]),
        "subject": strip_html(pick(row, [["subject"], ["topic"], ["reason"]])),
        "message": strip_html(pick(row, [["message"], ["comment"], ["inquiry"],
                                         ["enquiry"], ["body"], ["details"], ["question"]])),
    }


def contact_us_lead(row, classification):
    """Merge a classification back onto its Contact Us row."""
    return {
        "entry_id": row.get("entry_id", ""),
        "entry_date": row.get("entry_date", ""),
        "source": "contact_us",
        "name": row.get("name", ""),
        "email": row.get("email", ""),
        "school": row.get("company_school", ""),
        "grade": "",
        "homeroom_teacher": "",
        "amount_usd": "",
        "category": classification["category"],
        "sentiment": classification["sentiment"],
        "summary": classification["summary"],
    }
