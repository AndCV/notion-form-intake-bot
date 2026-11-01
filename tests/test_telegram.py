"""Telegram bot send: the request it builds and how it handles Telegram's
response. Mocks urllib -- no real network call, no real credentials."""

import json
from urllib.error import HTTPError

import pytest

from lib import telegram


def test_send_posts_json_with_chat_id_and_markdown(mocker):
    """protects: chat_id/text/parse_mode land in the JSON body against the right bot token URL;
    fails_when: a field is missing/misnamed or Markdown parsing isn't requested;
    no_claim: none beyond protects."""
    fake_response = mocker.Mock()
    fake_response.read.return_value = json.dumps(
        {"ok": True, "result": {"message_id": 42}}
    ).encode()
    fake_response.__enter__ = mocker.Mock(return_value=fake_response)
    fake_response.__exit__ = mocker.Mock(return_value=False)
    urlopen = mocker.patch("lib.telegram.urllib.request.urlopen", return_value=fake_response)

    message_id = telegram.send("bot-token-123", "987654321", "*Cierre de gastos*")

    assert message_id == 42
    request = urlopen.call_args[0][0]
    assert request.full_url == "https://api.telegram.org/botbot-token-123/sendMessage"
    body = json.loads(request.data.decode())
    assert body == {
        "chat_id": "987654321",
        "text": "*Cierre de gastos*",
        "parse_mode": "Markdown",
    }


def test_send_raises_on_telegram_error(mocker):
    """protects: a Telegram rejection (bad token, bot blocked by the user) surfaces as
      TelegramSendError with the status and body, not a silent failure;
    fails_when: the error is swallowed or the caller can't see why it failed."""
    error = HTTPError("url", 403, "Forbidden", {}, None)
    error.read = lambda: b'{"ok": false, "description": "Forbidden: bot was blocked by the user"}'
    mocker.patch("lib.telegram.urllib.request.urlopen", side_effect=error)

    with pytest.raises(telegram.TelegramSendError) as exc:
        telegram.send("bot-token-123", "987654321", "hola")

    assert exc.value.status == 403 and "blocked by the user" in exc.value.body
