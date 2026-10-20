"""Postgres access (Neon). The connection string comes from DATABASE_URL;
never hardcode it (see .env.example)."""

from __future__ import annotations

import os

import psycopg


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Missing environment variable DATABASE_URL")
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), connect_timeout=15)
