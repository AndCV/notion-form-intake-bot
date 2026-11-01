"""Sends the nightly expense closing via a Telegram bot.

Why Telegram and not WhatsApp: Meta's WhatsApp Cloud API requires a Meta
Business Account, and CallMeBot (the free no-business-account WhatsApp
workaround) has its registration closed ("This Bot is full", 2026-11-01).
Telegram's Bot API is free, official, needs no business account, and
activates in under a minute -- same job (one nightly message to the owner),
different app.

One-time setup: message @BotFather on Telegram, send "/newbot", follow its
prompts to get a bot token. Then message your new bot once (anything --
Telegram bots can't message first) so it has a chat to reply into, and get
your chat id from https://api.telegram.org/bot<TOKEN>/getUpdates (look for
"chat":{"id": ...} in the reply). See .env.example.

stdlib only (urllib): a single POST isn't worth a new dependency (see
lib.payment_mail's own stdlib IMAP client for the same choice). Credentials
come from environment variables, set as GitHub Secrets for
scripts/nightly_expense_report.py.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramSendError(RuntimeError):
    """Telegram rejected the message; .status and .body carry its response."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Telegram responded {status}: {body}")
        self.status = status
        self.body = body


def send(bot_token: str, chat_id: str, text: str) -> int:
    """Sends `text` to `chat_id` through the bot identified by `bot_token`.
    `text` may use Telegram's legacy Markdown (*bold*, same syntax
    lib.expenses.format_message already produces). Returns Telegram's
    message_id on success."""
    url = _API_URL.format(token=bot_token)
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    request = urllib.request.Request(
        url, data=payload, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            response = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise TelegramSendError(exc.code, exc.read().decode()) from exc
    return response["result"]["message_id"]
