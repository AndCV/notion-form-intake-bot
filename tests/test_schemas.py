from datetime import date

import pytest

from lib.schemas import AdvisorySubmission, ClassSubmission, RowError, submission_from_page


def _page(page_id="page-1", **overrides):
    properties = {
        "Nombre": {"title": [{"plain_text": "Ana Lopez"}]},
        "Tipo": {"select": {"name": "Asesoría"}},
        "Correo": {"email": "ana@example.com"},
        "Fecha ": {"date": {"start": "2026-08-01T00:00:00.000-06:00"}},
        "Calendar Event ID": {"rich_text": []},
        "Email Enviado": {"checkbox": False},
    }
    properties.update(overrides)
    return {"id": page_id, "properties": properties}


def test_parse_advisory_page():
    submission = submission_from_page(_page())

    assert isinstance(submission, AdvisorySubmission)
    assert submission.page_id == "page-1"
    assert submission.name == "Ana Lopez"
    assert submission.deadline == date(2026, 8, 1)
    assert submission.calendar_event_id is None
    assert submission.email_sent is False


def test_parse_class_page():
    page = _page(
        Tipo={"select": {"name": "Clases"}},
        **{"Fecha ": {"date": {"start": "2026-08-02T15:30:00.000-06:00"}}},
    )

    submission = submission_from_page(page)

    assert isinstance(submission, ClassSubmission)
    assert submission.scheduled_at.hour == 15
    assert submission.scheduled_at.minute == 30


def test_parse_page_with_existing_calendar_event_and_email_sent():
    page = _page(
        **{
            "Calendar Event ID": {"rich_text": [{"plain_text": "evt-123"}]},
            "Email Enviado": {"checkbox": True},
        }
    )

    submission = submission_from_page(page)

    assert submission.calendar_event_id == "evt-123"
    assert submission.email_sent is True


def test_missing_required_field_raises():
    page = _page(Correo={"email": None})
    with pytest.raises(RowError):
        submission_from_page(page)


def test_empty_placeholder_row_raises():
    """A row created by hand in Notion, without data from the form yet."""
    page = {
        "id": "page-empty",
        "properties": {
            "Nombre": {"title": []},
            "Tipo": {"select": None},
            "Correo": {"email": None},
            "Fecha ": {"date": None},
            "Calendar Event ID": {"rich_text": []},
            "Email Enviado": {"checkbox": False},
        },
    }
    with pytest.raises(RowError):
        submission_from_page(page)


def test_unknown_type_raises():
    page = _page(Tipo={"select": {"name": "Otro"}})
    with pytest.raises(RowError):
        submission_from_page(page)


def test_invalid_date_raises():
    page = _page(**{"Fecha ": {"date": {"start": "not a date"}}})
    with pytest.raises(RowError):
        submission_from_page(page)
