"""Loads and validates configuration from environment variables.

Never hardcode secrets: everything comes in via env vars (see .env.example).
"""

from __future__ import annotations

import base64
import json
import os


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Missing environment variable {name}")
    return value


class Config:
    def __init__(self) -> None:
        # Only the Notion poller needs these (scripts/poll_notion.py checks
        # them); the web app runs without Notion.
        self.notion_token = os.environ.get("NOTION_TOKEN", "")
        # The Notion API (2025-09-03+) splits "database" from "data source":
        # properties and rows live in the data source, not the database
        # container itself (see README).
        self.notion_data_source_id = os.environ.get("NOTION_DATA_SOURCE_ID", "")

        # Email is sent via Zoho Mail (SMTP + App Password) instead of a
        # transactional provider: there's no verified custom domain, and
        # Zoho doesn't require one to send to arbitrary recipients.
        self.zoho_address = _require("ZOHO_ADDRESS")
        # Two ways to send (lib/email_client.py picks one): the Zoho Mail
        # HTTPS API when the OAuth settings below are present -- required on
        # hosts that block SMTP, like Render's free tier -- else SMTP with
        # the App Password.
        self.zoho_app_password = os.environ.get("ZOHO_APP_PASSWORD", "")
        self.zoho_client_id = os.environ.get("ZOHO_CLIENT_ID", "")
        self.zoho_client_secret = os.environ.get("ZOHO_CLIENT_SECRET", "")
        self.zoho_refresh_token = os.environ.get("ZOHO_REFRESH_TOKEN", "")
        self.zoho_account_id = os.environ.get("ZOHO_ACCOUNT_ID", "")
        self.zoho_accounts_url = os.environ.get("ZOHO_ACCOUNTS_URL", "")
        self.zoho_mail_api_url = os.environ.get("ZOHO_MAIL_API_URL", "")
        api = (self.zoho_client_id, self.zoho_client_secret, self.zoho_refresh_token, self.zoho_account_id)
        if any(api) and not all(api):
            raise ConfigError(
                "Zoho API: set all of ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, "
                "ZOHO_REFRESH_TOKEN and ZOHO_ACCOUNT_ID, or none of them"
            )
        if not any(api) and not self.zoho_app_password:
            raise ConfigError("Missing environment variable ZOHO_APP_PASSWORD (or the Zoho API settings)")

        self.google_service_account_json = _require("GOOGLE_SERVICE_ACCOUNT_JSON")
        self.google_calendar_id = _require("GOOGLE_CALENDAR_ID")

        # Display name used in the confirmation emails (see lib/email_client.py).
        self.business_name = os.environ.get("BUSINESS_NAME", "your business")

        self.timezone = os.environ.get("TIMEZONE", "UTC")
        self.class_duration_minutes = int(os.environ.get("CLASS_DURATION_MINUTES", "60"))
        self.create_advisory_calendar_event = (
            os.environ.get("ADVISORY_CREATE_CALENDAR_EVENT", "true").lower() == "true"
        )

        # Invoice PDF content (see lib/invoicing.py / templates/invoice.html).
        # A row only gets invoiced once it has a price set (see
        # scripts/poll_notion.py) -- these fields are about the invoice's
        # letterhead, not any single request.
        self.invoice_issuer_name = os.environ.get("INVOICE_ISSUER_NAME", "Your Name")
        self.invoice_tagline = os.environ.get("INVOICE_TAGLINE", "")
        self.invoice_phone = os.environ.get("INVOICE_PHONE", "")
        self.invoice_email = os.environ.get("INVOICE_EMAIL", self.zoho_address)
        self.invoice_address = os.environ.get("INVOICE_ADDRESS", "")
        self.invoice_website = os.environ.get("INVOICE_WEBSITE", "")
        self.invoice_payment_method = os.environ.get("INVOICE_PAYMENT_METHOD", "Bank transfer")
        self.invoice_currency_symbol = os.environ.get("INVOICE_CURRENCY_SYMBOL", "$")

        # Web app only (app/main.py checks them at startup).
        self.database_url = os.environ.get("DATABASE_URL", "")
        # scrypt hash from scripts/hash_password.py, never the password itself.
        self.app_password_hash = os.environ.get("APP_PASSWORD_HASH", "")
        self.session_secret = os.environ.get("SESSION_SECRET", "")
        # Off only for local http://localhost runs; Cloud Run is always HTTPS.
        self.cookie_secure = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

    def google_credentials_info(self) -> dict:
        raw = self.google_service_account_json.strip()
        if not raw.startswith("{"):
            # Accepts the service account JSON as base64, useful to avoid
            # newline-escaping issues when pasting it as a GitHub Actions
            # secret.
            raw = base64.b64decode(raw).decode()
        return json.loads(raw)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config_cache() -> None:
    """Test-only: forces the next get_config() call to re-read the environment."""
    global _config
    _config = None
