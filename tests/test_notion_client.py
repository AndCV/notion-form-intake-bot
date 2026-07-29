from lib.notion_client import NotionRepository


def test_find_pending_pages_queries_with_or_filter(mocker):
    fake_client = mocker.Mock()
    fake_client.data_sources.query.return_value = {"results": [{"id": "page-1"}]}
    mocker.patch("lib.notion_client.Client", return_value=fake_client)

    repo = NotionRepository("token", "ds-1")
    pages = repo.find_pending_pages()

    assert pages == [{"id": "page-1"}]
    _, kwargs = fake_client.data_sources.query.call_args
    assert kwargs["data_source_id"] == "ds-1"
    conditions = kwargs["filter"]["or"]
    assert {"property": "Estado", "select": {"is_empty": True}} in conditions
    assert {"property": "Estado", "select": {"equals": "nuevo"}} in conditions
    assert {"property": "Email Enviado", "checkbox": {"equals": False}} in conditions


def test_set_calendar_event_id_updates_rich_text(mocker):
    fake_client = mocker.Mock()
    mocker.patch("lib.notion_client.Client", return_value=fake_client)

    repo = NotionRepository("token", "ds-1")
    repo.set_calendar_event_id("page-1", "evt-1")

    fake_client.pages.update.assert_called_once_with(
        page_id="page-1",
        properties={"Calendar Event ID": {"rich_text": [{"text": {"content": "evt-1"}}]}},
    )


def test_mark_confirmed_sets_checkbox_and_select(mocker):
    fake_client = mocker.Mock()
    mocker.patch("lib.notion_client.Client", return_value=fake_client)

    repo = NotionRepository("token", "ds-1")
    repo.mark_confirmed("page-1")

    fake_client.pages.update.assert_called_once_with(
        page_id="page-1",
        properties={
            "Email Enviado": {"checkbox": True},
            "Estado": {"select": {"name": "confirmado"}},
        },
    )


def test_get_last_invoice_number_parses_highest_existing_number(mocker):
    fake_client = mocker.Mock()
    fake_client.data_sources.query.return_value = {
        "results": [
            {"properties": {"Número de Factura": {"rich_text": [{"plain_text": "0000042"}]}}}
        ]
    }
    mocker.patch("lib.notion_client.Client", return_value=fake_client)

    repo = NotionRepository("token", "ds-1")
    result = repo.get_last_invoice_number()

    assert result == 42
    _, kwargs = fake_client.data_sources.query.call_args
    assert kwargs["data_source_id"] == "ds-1"
    assert kwargs["filter"] == {
        "property": "Número de Factura",
        "rich_text": {"is_not_empty": True},
    }
    assert kwargs["sorts"] == [{"property": "Número de Factura", "direction": "descending"}]


def test_get_last_invoice_number_returns_zero_when_none_issued(mocker):
    fake_client = mocker.Mock()
    fake_client.data_sources.query.return_value = {"results": []}
    mocker.patch("lib.notion_client.Client", return_value=fake_client)

    repo = NotionRepository("token", "ds-1")

    assert repo.get_last_invoice_number() == 0


def test_mark_invoiced_sets_number_and_checkbox(mocker):
    fake_client = mocker.Mock()
    mocker.patch("lib.notion_client.Client", return_value=fake_client)

    repo = NotionRepository("token", "ds-1")
    repo.mark_invoiced("page-1", "0000043")

    fake_client.pages.update.assert_called_once_with(
        page_id="page-1",
        properties={
            "Número de Factura": {"rich_text": [{"text": {"content": "0000043"}}]},
            "Factura Enviada": {"checkbox": True},
        },
    )
