"""The registration path drops student data. This is the privacy boundary."""

import json

from arthouse_ops import parsing

BANNED_SUBSTRINGS = [
    "Mira", "Noor", "2017-04-02", "2016-11-20", "Example Row", "Example Lane",
    "Peanut", "EpiPen", "Jordan Whitfield", "Alex Whitfield", "+15550105678",
]


def test_sensitive_headers_are_recognised():
    for header in ["Date of Birth", "Address", "Allergies/Medical Problems/Comments?",
                   "Emergency Contact Name", "Emergency Contact Phone Number",
                   "Student's Name", "Names of other adults authorized to pick up your child"]:
        assert parsing.is_sensitive(header), header


def test_homeroom_teacher_survives_the_student_rule():
    """The rule needs both 'student' and 'name', or this column would be lost."""
    assert not parsing.is_sensitive("Student's Homeroom Teacher")


def test_no_sensitive_value_reaches_a_lead_row(registration_page):
    rows = parsing.flatten_entries([registration_page])
    serialized = json.dumps([parsing.registration_lead(row)[0] for row in rows])
    for banned in BANNED_SUBSTRINGS:
        assert banned not in serialized, banned


def test_lead_has_exactly_the_sheet_columns(registration_page):
    from arthouse_ops.config import LEADS_HEADERS

    lead, _ = parsing.registration_lead(parsing.flatten_entries([registration_page])[0])
    assert list(lead.keys()) == LEADS_HEADERS


def test_registration_lead_keeps_the_follow_up_fields(registration_page):
    lead, dropped = parsing.registration_lead(parsing.flatten_entries([registration_page])[0])
    assert dropped == []
    assert lead == {
        "entry_id": "18420",
        "entry_date": "2026-02-11 08:30:00",
        "source": "registration",
        "name": "Dana Whitfield",
        "email": "dana@example-pta.org",
        "school": "Rivermont Elementary",
        "grade": "3",
        "homeroom_teacher": "Ms. Alvarez",
        "amount_usd": 245.0,
        "category": "",
        "sentiment": "",
        "summary": "",
    }


def test_a_new_sensitive_column_does_not_leak(registration_page):
    """Adding a field in WordPress must not add a column to the sheet."""
    row = parsing.flatten_entries([registration_page])[0]
    row["Home Address Line 2"] = "Apartment 4"
    row["Custody Notes"] = "sensitive free text"
    lead, _ = parsing.registration_lead(row)
    assert "Apartment 4" not in json.dumps(lead)
    assert "sensitive free text" not in json.dumps(lead)


def test_missing_columns_are_reported(registration_page):
    rows = parsing.flatten_entries([registration_page])
    assert parsing.registration_schema_warning(parsing.registration_lead(rows[0])[0]) == []
    missing = parsing.registration_schema_warning(parsing.registration_lead(rows[1])[0])
    assert missing == ["Parent/Guardian Name", "Parent Email"]


# --- the scrub: an allowed column that swallowed sensitive data ---

def base_row():
    return {
        "Entry ID": "1", "Entry Date": "2026-01-01 00:00:00",
        "Student's Name": "Robin", "Date of Birth": "04/02/2017",
        "Parent/Guardian Name": "Dana Whitfield", "Email": "dana@example-pta.org",
        "Please enter the after-school program's school name:": "Rivermont Elementary",
        "Student's Homeroom Teacher": "Ms. Alvarez", "Register and Pay": "$245.00",
    }


def test_a_note_containing_a_student_name_is_blanked():
    """A parent used the school-name box as a notes box. Really happens."""
    row = base_row()
    row["Please enter the after-school program's school name:"] = (
        "Robin has our permission to walk herself home after the art class")
    lead, dropped = parsing.registration_lead(row)
    assert dropped == ["school"]
    assert lead["school"] == ""
    assert lead["name"] == "Dana Whitfield"


def test_a_normal_school_name_is_untouched():
    lead, dropped = parsing.registration_lead(base_row())
    assert dropped == []
    assert lead["school"] == "Rivermont Elementary"


def test_a_parent_named_as_their_own_emergency_contact_keeps_their_name():
    """Equality means the allowed column, not a leak. Blanking here would cost
    the contact name on a large share of real rows."""
    row = base_row()
    row["Emergency Contact Name"] = "Dana Whitfield"
    lead, dropped = parsing.registration_lead(row)
    assert dropped == []
    assert lead["name"] == "Dana Whitfield"


def test_two_guardians_in_one_box_keeps_the_name():
    row = base_row()
    row["Parent/Guardian Name"] = "Dana Whitfield and Sam Booker"
    row["Names of other adults authorized to pick up your child"] = "Dana Whitfield"
    lead, dropped = parsing.registration_lead(row)
    assert lead["name"] == "Dana Whitfield and Sam Booker"


def test_an_essay_in_a_short_field_is_blanked_even_with_no_student_name():
    row = base_row()
    row["Please enter the after-school program's school name:"] = "x" * (parsing.MAX_SHORT_FIELD + 1)
    lead, dropped = parsing.registration_lead(row)
    assert dropped == ["school"]
    assert lead["school"] == ""
