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
        self.notion_token = _require("NOTION_TOKEN")
        # The Notion API (2025-09-03+) splits "database" from "data source":
        # properties and rows live in the data source, not the database
        # container itself (see README).
        self.notion_data_source_id = _require("NOTION_DATA_SOURCE_ID")

        # Email is sent via Zoho Mail (SMTP + App Password) instead of a
        # transactional provider: there's no verified custom domain, and
        # Zoho doesn't require one to send to arbitrary recipients.
        self.zoho_address = _require("ZOHO_ADDRESS")
        self.zoho_app_password = _require("ZOHO_APP_PASSWORD")

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
