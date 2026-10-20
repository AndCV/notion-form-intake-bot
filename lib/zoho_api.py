"""Sends email through the Zoho Mail REST API (HTTPS) instead of SMTP.

Needed on hosts that block outbound SMTP ports, like Render's free tier.
Uses a Zoho "Self Client" (api-console.zoho.com) with the scopes
ZohoMail.messages.ALL and ZohoMail.accounts.READ; its long-lived refresh
token lives in ZOHO_REFRESH_TOKEN (see README, "Zoho Mail (sending email)").

Each attachment is uploaded first, then referenced from the send call.
Standard library only.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Personal @zohomail.com accounts live in the US data center; other regions
# use zoho.eu, zoho.in, ... (override with ZOHO_ACCOUNTS_URL / ZOHO_MAIL_API_URL).
DEFAULT_ACCOUNTS_URL = "https://accounts.zoho.com"
DEFAULT_MAIL_API_URL = "https://mail.zoho.com"
_TIMEOUT = 30

_lock = threading.Lock()
_token: dict[str, Any] = {"value": None, "expires_at": 0.0, "key": None}


class ZohoApiError(RuntimeError):
    pass


def _request(req: urllib.request.Request, retries: int = 0) -> dict[str, Any]:
    """`retries` only for calls that are safe to repeat (token refresh,
    attachment upload) -- never the final send, which could go out twice."""
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = {}
        status = detail.get("status", {}) if isinstance(detail, dict) else {}
        data = detail.get("data", {}) if isinstance(detail, dict) else {}
        reason = (data.get("moreInfo") if isinstance(data, dict) else None) or status.get(
            "description"
        )
        raise ZohoApiError(f"Zoho respondió {exc.code}: {reason or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        # Network-level failure (DNS, TLS handshake, timeout): no HTTP answer.
        if retries > 0:
            time.sleep(2)
            return _request(req, retries - 1)
        raise ZohoApiError(f"No se pudo conectar con Zoho: {getattr(exc, 'reason', exc)}") from exc


def _access_token(config: Any) -> str:
    """Cached access token (they last an hour); refreshed a minute early."""
    key = (config.zoho_client_id, config.zoho_refresh_token)
    with _lock:
        if _token["key"] == key and _token["value"] and time.time() < _token["expires_at"]:
            return _token["value"]
        accounts = getattr(config, "zoho_accounts_url", "") or DEFAULT_ACCOUNTS_URL
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": config.zoho_refresh_token,
                "client_id": config.zoho_client_id,
                "client_secret": config.zoho_client_secret,
            }
        ).encode()
        data = _request(
            urllib.request.Request(f"{accounts}/oauth/v2/token", data=body, method="POST"),
            retries=2,
        )
        if "access_token" not in data:
            raise ZohoApiError(f"Zoho no entregó access_token: {data.get('error', 'sin detalle')}")
        _token.update(
            value=data["access_token"],
            expires_at=time.time() + int(data.get("expires_in", 3600)) - 60,
            key=key,
        )
        return _token["value"]


def send(config: Any, email: Any) -> None:
    """`email` is a lib.email_client.OutgoingEmail. Raises ZohoApiError."""
    token = _access_token(config)
    mail_api = getattr(config, "zoho_mail_api_url", "") or DEFAULT_MAIL_API_URL
    base = f"{mail_api}/api/accounts/{config.zoho_account_id}"
    auth = {"Authorization": f"Zoho-oauthtoken {token}"}

    refs = []
    for filename, content, mime_type in email.attachments:
        query = urllib.parse.urlencode({"fileName": filename})
        uploaded = _request(
            urllib.request.Request(
                f"{base}/messages/attachments?{query}",
                data=content,
                method="POST",
                headers=auth | {"Content-Type": "application/octet-stream"},
            ),
            retries=2,
        )["data"]
        if isinstance(uploaded, list):
            uploaded = uploaded[0]
        refs.append({k: uploaded[k] for k in ("storeName", "attachmentPath", "attachmentName")})

    payload = {
        "fromAddress": config.zoho_address,
        "toAddress": ",".join(email.to),
        "subject": email.subject,
        "content": email.html,
        "mailFormat": "html",
        "encoding": "UTF-8",
    }
    if refs:
        payload["attachments"] = refs
    _request(
        urllib.request.Request(
            f"{base}/messages",
            data=json.dumps(payload).encode(),
            method="POST",
            headers=auth | {"Content-Type": "application/json"},
        )
    )
