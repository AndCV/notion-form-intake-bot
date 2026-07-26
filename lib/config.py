"""Carga y validacion de configuracion desde variables de entorno.

Nunca hardcodear secretos: todo entra por env vars (ver .env.example).
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
        raise ConfigError(f"Falta la variable de entorno {name}")
    return value


class Config:
    def __init__(self) -> None:
        self.notion_token = _require("NOTION_TOKEN")
        # La API de Notion (2025-09-03+) separa "database" de "data source":
        # las propiedades y las filas viven en el data source, no en el
        # database contenedor. Este id se obtuvo consultando la base
        # "Solicitudes ACV" via la API (ver README).
        self.notion_data_source_id = _require("NOTION_DATA_SOURCE_ID")

        # Envio de correo via Gmail (SMTP + App Password) en vez de un
        # proveedor transaccional: no hay dominio propio verificado, y Gmail
        # no lo exige para mandar a cualquier destinatario.
        self.gmail_address = _require("GMAIL_ADDRESS")
        self.gmail_app_password = _require("GMAIL_APP_PASSWORD")

        self.google_service_account_json = _require("GOOGLE_SERVICE_ACCOUNT_JSON")
        self.google_calendar_id = _require("GOOGLE_CALENDAR_ID")

        self.timezone = os.environ.get("TIMEZONE", "America/Costa_Rica")
        self.class_duration_minutes = int(os.environ.get("CLASS_DURATION_MINUTES", "60"))
        self.create_asesoria_calendar_event = (
            os.environ.get("ASESORIA_CREATE_CALENDAR_EVENT", "true").lower() == "true"
        )

    def google_credentials_info(self) -> dict:
        raw = self.google_service_account_json.strip()
        if not raw.startswith("{"):
            # Soporta pegar el JSON del service account en base64, util para
            # evitar problemas de escaping de saltos de linea al cargarlo
            # como GitHub Actions secret.
            raw = base64.b64decode(raw).decode()
        return json.loads(raw)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config_cache() -> None:
    """Solo para tests: fuerza a que la proxima get_config() relea el entorno."""
    global _config
    _config = None
