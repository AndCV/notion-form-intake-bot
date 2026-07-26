from types import SimpleNamespace


def _config(**overrides):
    base = dict(
        notion_token="notion-token",
        notion_data_source_id="ds-1",
        gmail_address="andres@gmail.com",
        gmail_app_password="app-password",
        google_service_account_json="{}",
        google_calendar_id="cal-id",
        business_name="Test Co",
        timezone="America/Costa_Rica",
        class_duration_minutes=60,
        create_advisory_calendar_event=True,
        google_credentials_info=lambda: {},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _advisory_page(page_id="page-1"):
    return {
        "id": page_id,
        "properties": {
            "Nombre": {"title": [{"plain_text": "Ana"}]},
            "Tipo": {"select": {"name": "Asesoría"}},
            "Correo": {"email": "ana@example.com"},
            "Fecha ": {"date": {"start": "2026-08-01T00:00:00.000-06:00"}},
            "Calendar Event ID": {"rich_text": []},
            "Email Enviado": {"checkbox": False},
        },
    }


def test_process_page_creates_event_and_sends_email(mocker):
    from scripts import poll_notion

    notion = mocker.Mock()
    calendar = mocker.Mock()
    calendar.create_event_for_submission.return_value = {"id": "evt-1"}
    mocker.patch("scripts.poll_notion.CalendarRepository", return_value=calendar)
    mocker.patch("scripts.poll_notion.build_ics", return_value=b"ICS")
    send_email = mocker.patch("scripts.poll_notion.send_confirmation_email")

    poll_notion.process_page(_config(), notion, _advisory_page())

    calendar.create_event_for_submission.assert_called_once()
    notion.set_calendar_event_id.assert_called_once_with("page-1", "evt-1")
    send_email.assert_called_once()
    notion.mark_confirmed.assert_called_once_with("page-1")


def test_process_page_skips_event_when_already_created(mocker):
    from scripts import poll_notion

    page = _advisory_page()
    page["properties"]["Calendar Event ID"] = {"rich_text": [{"plain_text": "evt-existing"}]}

    notion = mocker.Mock()
    calendar = mocker.Mock()
    mocker.patch("scripts.poll_notion.CalendarRepository", return_value=calendar)
    mocker.patch("scripts.poll_notion.build_ics", return_value=b"ICS")
    send_email = mocker.patch("scripts.poll_notion.send_confirmation_email")

    poll_notion.process_page(_config(), notion, page)

    calendar.create_event_for_submission.assert_not_called()
    notion.set_calendar_event_id.assert_not_called()
    send_email.assert_called_once()
    notion.mark_confirmed.assert_called_once_with("page-1")


def test_process_page_skips_email_when_already_sent(mocker):
    from scripts import poll_notion

    page = _advisory_page()
    page["properties"]["Email Enviado"] = {"checkbox": True}

    notion = mocker.Mock()
    calendar = mocker.Mock()
    calendar.create_event_for_submission.return_value = {"id": "evt-1"}
    mocker.patch("scripts.poll_notion.CalendarRepository", return_value=calendar)
    send_email = mocker.patch("scripts.poll_notion.send_confirmation_email")

    poll_notion.process_page(_config(), notion, page)

    send_email.assert_not_called()
    notion.mark_confirmed.assert_called_once_with("page-1")


def test_run_continues_after_one_row_fails(mocker):
    from scripts import poll_notion

    good_page = _advisory_page("page-good")
    bad_page = {"id": "page-bad", "properties": {}}  # missing required fields

    notion = mocker.Mock()
    notion.find_pending_pages.return_value = [bad_page, good_page]
    mocker.patch("scripts.poll_notion.NotionRepository", return_value=notion)
    mocker.patch("scripts.poll_notion.get_config", return_value=_config())

    calendar = mocker.Mock()
    calendar.create_event_for_submission.return_value = {"id": "evt-1"}
    mocker.patch("scripts.poll_notion.CalendarRepository", return_value=calendar)
    mocker.patch("scripts.poll_notion.build_ics", return_value=b"ICS")
    mocker.patch("scripts.poll_notion.send_confirmation_email")

    poll_notion.run()

    notion.mark_confirmed.assert_called_once_with("page-good")
