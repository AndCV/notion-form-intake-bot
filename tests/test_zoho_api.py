import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from lib import zoho_api
from lib.email_client import send_invoice_email, send_receipt_email
from lib.schemas import AdvisorySubmission


def _config(**overrides):
    base = dict(
        zoho_address="andres@zohomail.com",
        zoho_client_id="cid",
        zoho_client_secret="secret",
        zoho_refresh_token="refresh",
        zoho_account_id="123",
        zoho_accounts_url="",
        zoho_mail_api_url="",
        business_name="Test Co",
        invoice_issuer_name="Issuer",
        invoice_tagline="",
        invoice_phone="",
        invoice_email="andres@zohomail.com",
        invoice_website="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeZoho:
    """Records every request urlopen gets and answers like Zoho does."""

    def __init__(self, fail_send=False):
        self.calls = []
        self.fail_send = fail_send

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        url = req.full_url
        if url.endswith("/oauth/v2/token"):
            return io.BytesIO(json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
        if "/messages/attachments" in url:
            name = url.split("fileName=")[1]
            return io.BytesIO(json.dumps({"status": {"code": 200}, "data": {
                "storeName": "s1", "attachmentPath": f"/p/{name}", "attachmentName": name}}).encode())
        if url.endswith("/messages"):
            if self.fail_send:
                raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(json.dumps(
                    {"status": {"code": 400, "description": "Invalid Input"},
                     "data": {"moreInfo": "Invalid toAddress"}}).encode()))
            return io.BytesIO(json.dumps({"status": {"code": 200}}).encode())
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def fake(mocker):
    zoho_api._token.update(value=None, expires_at=0.0, key=None)
    fake = FakeZoho()
    mocker.patch("lib.zoho_api.urllib.request.urlopen", side_effect=fake)
    smtp = mocker.patch("lib.email_client.smtplib.SMTP_SSL")
    yield fake
    smtp.assert_not_called()


def _submission():
    from datetime import date
    return AdvisorySubmission(page_id="s-1", name="Ana", email="a@example.com", deadline=date(2026, 10, 30),
                              calendar_event_id=None, email_sent=False, price=15000,
                              emails=["a@example.com", "b@example.com"])  # fmt: skip


def test_invoice_goes_through_the_api_to_every_recipient_with_its_pdf(fake):
    """protects: with API credentials set, mail leaves over HTTPS (Render blocks SMTP),
    to all client emails, with the PDF uploaded then referenced;
    fails_when: SMTP is used, a recipient is dropped, or the attachment isn't linked;
    no_claim: Zoho's real endpoint behaviour was checked once by hand (2026-10-20)."""
    refused = send_invoice_email(_config(), _submission(), b"%PDF", "0000042")

    assert refused == {}
    token, upload, send = fake.calls
    assert token.full_url == "https://accounts.zoho.com/oauth/v2/token"
    assert upload.full_url == "https://mail.zoho.com/api/accounts/123/messages/attachments?fileName=factura-0000042.pdf"
    assert upload.data == b"%PDF" and upload.get_header("Authorization") == "Zoho-oauthtoken tok"
    body = json.loads(send.data)
    assert body["fromAddress"] == "andres@zohomail.com"
    assert body["toAddress"] == "a@example.com,b@example.com"
    assert body["subject"] == "Factura 0000042" and body["mailFormat"] == "html"
    assert body["attachments"] == [{"storeName": "s1", "attachmentPath": "/p/factura-0000042.pdf",
                                    "attachmentName": "factura-0000042.pdf"}]  # fmt: skip


def test_access_token_is_reused_between_sends(fake):
    """protects: one token refresh per hour, not per email;
    fails_when: every send hits the token endpoint;
    no_claim: expiry timing itself is not time-travelled."""
    send_receipt_email(_config(), "Uno", "uno@example.com", [("Monto", "₡1")], b"%PDF", "0000001")
    send_receipt_email(_config(), "Uno", "uno@example.com", [("Monto", "₡1")], b"%PDF", "0000002")

    assert sum(c.full_url.endswith("/oauth/v2/token") for c in fake.calls) == 1


def test_api_error_surfaces_zohos_reason(mocker):
    """protects: a rejected send raises with Zoho's own reason, so the step shows ❌
    with something readable;
    fails_when: the failure is swallowed or the reason lost;
    no_claim: none beyond protects."""
    zoho_api._token.update(value=None, expires_at=0.0, key=None)
    mocker.patch("lib.zoho_api.urllib.request.urlopen", side_effect=FakeZoho(fail_send=True))

    with pytest.raises(zoho_api.ZohoApiError, match="Invalid toAddress"):
        send_invoice_email(_config(), _submission(), b"%PDF", "0000042")


def test_partial_api_settings_are_a_config_error(monkeypatch):
    """protects: half-configured API credentials fail at startup, not at the first send;
    fails_when: Config accepts e.g. a client id without a refresh token;
    no_claim: none beyond protects."""
    from lib.config import Config, ConfigError

    for name in ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_CALENDAR_ID", "ZOHO_ADDRESS"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("ZOHO_CLIENT_ID", "cid")
    for name in ("ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_ACCOUNT_ID", "ZOHO_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError, match="ZOHO_REFRESH_TOKEN"):
        Config()


def test_token_refresh_retries_a_network_hiccup_but_send_is_never_retried(mocker):
    """protects: a transient TLS/connection error on the token or upload call is retried,
    while the final send is attempted once (a retry could deliver it twice);
    fails_when: one hiccup fails the email, or the send call is repeated;
    no_claim: real network timing is not simulated (sleep is patched out)."""
    zoho_api._token.update(value=None, expires_at=0.0, key=None)
    mocker.patch("lib.zoho_api.time.sleep")
    fake = FakeZoho()
    calls = {"token": 0, "send": 0}

    def flaky(req, timeout=None):
        if req.full_url.endswith("/oauth/v2/token"):
            calls["token"] += 1
            if calls["token"] == 1:
                raise urllib.error.URLError("handshake timed out")
        if req.full_url.endswith("/messages"):
            calls["send"] += 1
            raise urllib.error.URLError("connection reset")
        return fake(req, timeout)

    mocker.patch("lib.zoho_api.urllib.request.urlopen", side_effect=flaky)

    with pytest.raises(zoho_api.ZohoApiError, match="No se pudo conectar"):
        send_invoice_email(_config(), _submission(), b"%PDF", "0000042")
    assert calls == {"token": 2, "send": 1}
