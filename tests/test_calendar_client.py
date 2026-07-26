from datetime import date, datetime

from lib.calendar_client import build_all_day_event_body, build_timed_event_body
from lib.schemas import AdvisorySubmission, ClassSubmission


def test_all_day_event_body_spans_single_day():
    submission = AdvisorySubmission(
        page_id="page-1",
        name="Ana",
        email="ana@example.com",
        deadline=date(2026, 8, 1),
        calendar_event_id=None,
        email_sent=False,
    )

    body = build_all_day_event_body(submission)

    assert body["start"] == {"date": "2026-08-01"}
    assert body["end"] == {"date": "2026-08-02"}
    assert "attendees" not in body


def test_timed_event_body_uses_duration_and_timezone():
    submission = ClassSubmission(
        page_id="page-2",
        name="Beto",
        email="beto@example.com",
        scheduled_at=datetime(2026, 8, 2, 15, 30, 0),
        calendar_event_id=None,
        email_sent=False,
    )

    body = build_timed_event_body(submission, timezone="America/Costa_Rica", duration_minutes=90)

    assert body["start"] == {"dateTime": "2026-08-02T15:30:00", "timeZone": "America/Costa_Rica"}
    assert body["end"] == {"dateTime": "2026-08-02T17:00:00", "timeZone": "America/Costa_Rica"}
    assert "attendees" not in body


def test_calendar_repository_dispatches_by_submission_type(mocker):
    fake_credentials = mocker.Mock()
    mocker.patch(
        "lib.calendar_client.service_account.Credentials.from_service_account_info",
        return_value=fake_credentials,
    )
    fake_service = mocker.Mock()
    fake_events = mocker.Mock()
    fake_service.events.return_value = fake_events
    fake_events.insert.return_value.execute.return_value = {"id": "evt-1"}
    mocker.patch("lib.calendar_client.build", return_value=fake_service)

    from lib.calendar_client import CalendarRepository

    repo = CalendarRepository({"type": "service_account"}, "calendar-id")
    submission = ClassSubmission(
        page_id="page-2",
        name="Beto",
        email="beto@example.com",
        scheduled_at=datetime(2026, 8, 2, 15, 30, 0),
        calendar_event_id=None,
        email_sent=False,
    )

    result = repo.create_event_for_submission(submission, "UTC", 60)

    assert result == {"id": "evt-1"}
    fake_events.insert.assert_called_once()
    _, kwargs = fake_events.insert.call_args
    assert kwargs["calendarId"] == "calendar-id"
