"""Postgres access (Neon). The connection string comes from DATABASE_URL;
never hardcode it (see .env.example).

Plain SQL over psycopg, one function per operation the app needs. Rows come
back as dicts.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .service_form import ServiceInput

# Columns the processing steps may stamp; anything else is refused so a
# caller can't turn mark_step into arbitrary SQL.
_STEP_COLUMNS = {
    "calendar_event_id",
    "confirmation_sent_at",
    "invoice_sent_at",
    "receipt_sent_at",
}

LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_FAILURES = 5


class InvoicingNotInitialized(RuntimeError):
    """counters has no 'invoice' row yet (scripts/migrate.py --seed-invoice-counter)."""


class ReceiptLocked(ValueError):
    """The collaborator or their pay can't change once the receipt went out."""


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Missing environment variable DATABASE_URL")
    return url


def connect(url: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    """autocommit=True for the web app: each write commits on its own and
    conn.transaction() opens a real transaction. Scripts keep the default so
    `with connect() as conn:` is one all-or-nothing transaction."""
    # prepare_threshold=None: Neon's pooler (PgBouncer, transaction mode)
    # can hand each statement a different server connection.
    return psycopg.connect(
        url or database_url(), connect_timeout=15, prepare_threshold=None, autocommit=autocommit
    )


def format_invoice_number(number: int) -> str:
    # Same 7-digit format as lib.invoicing.format_invoice_number, kept here so
    # the scripts don't import WeasyPrint just to format a number.
    return f"{number:07d}"


def _one(conn: psycopg.Connection, sql: str, params: Any = None) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# --- collaborators ---------------------------------------------------------


class DuplicateEmail(ValueError):
    """Another collaborator already uses that email."""


def list_collaborators(conn: psycopg.Connection, active_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE active" if active_only else ""
    return _all(
        conn,
        f"SELECT id, name, email, active FROM collaborators {where} ORDER BY active DESC, name",
    )


def get_collaborator(conn: psycopg.Connection, collaborator_id: int) -> dict[str, Any] | None:
    return _one(
        conn, "SELECT id, name, email, active FROM collaborators WHERE id = %s", (collaborator_id,)
    )


def create_collaborator(conn: psycopg.Connection, name: str, email: str) -> int:
    try:
        row = _one(
            conn,
            "INSERT INTO collaborators (name, email) VALUES (%s, %s) RETURNING id",
            (name, email),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateEmail(email) from exc
    conn.commit()
    return row["id"]


def update_collaborator(conn: psycopg.Connection, collaborator_id: int, name: str, email: str) -> None:
    """Receipts already sent keep the name/email they were issued to
    (services.receipt_to_*); only future receipts use the new values."""
    try:
        conn.execute(
            "UPDATE collaborators SET name = %s, email = %s WHERE id = %s",
            (name, email, collaborator_id),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateEmail(email) from exc
    conn.commit()


def set_collaborator_active(conn: psycopg.Connection, collaborator_id: int, active: bool) -> None:
    conn.execute(
        "UPDATE collaborators SET active = %s WHERE id = %s", (active, collaborator_id)
    )
    conn.commit()


# --- services ---------------------------------------------------------------

_SERVICE_SELECT = """
    SELECT s.*, c.name AS collaborator_name, c.email AS collaborator_email,
           COALESCE(
             (SELECT array_agg(e.email ORDER BY e.email) FROM service_emails e WHERE e.service_id = s.id),
             '{}'
           ) AS emails
    FROM services s
    LEFT JOIN collaborators c ON c.id = s.collaborator_id
"""


def find_service_by_token(conn: psycopg.Connection, token: uuid.UUID) -> int | None:
    row = _one(conn, "SELECT id FROM services WHERE submission_token = %s", (token,))
    return row["id"] if row else None


def create_service(conn: psycopg.Connection, data: ServiceInput, token: uuid.UUID) -> int:
    """Inserts the service, its emails and its invoice (and receipt) number
    in one transaction. Raises InvoicingNotInitialized before the cutover."""
    with conn.transaction():
        invoice = _one(
            conn,
            "UPDATE counters SET last_value = last_value + 1 WHERE name = 'invoice' RETURNING last_value",
        )
        if invoice is None:
            raise InvoicingNotInitialized()
        receipt_number = None
        if data.collaborator_id is not None:
            receipt = _one(
                conn,
                "UPDATE counters SET last_value = last_value + 1 WHERE name = 'receipt' RETURNING last_value",
            )
            receipt_number = format_invoice_number(receipt["last_value"])

        row = _one(
            conn,
            """
            INSERT INTO services (service_date, client_name, job, kind, scheduled_at, deadline,
                                  price, collaborator_id, collaborator_pay, notes,
                                  invoice_number, receipt_number, submission_token, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'app')
            RETURNING id
            """,
            (
                data.service_date,
                data.client_name,
                data.job,
                data.kind,
                data.scheduled_at,
                data.deadline,
                data.price,
                data.collaborator_id,
                data.collaborator_pay,
                data.notes,
                format_invoice_number(invoice["last_value"]),
                receipt_number,
                token,
            ),
        )
        _replace_emails(conn, row["id"], data.emails)
    return row["id"]


def _replace_emails(conn: psycopg.Connection, service_id: int, emails: list[str]) -> None:
    conn.execute("DELETE FROM service_emails WHERE service_id = %s", (service_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO service_emails (service_id, email) VALUES (%s, %s)",
            [(service_id, email) for email in emails],
        )


def get_service(conn: psycopg.Connection, service_id: int) -> dict[str, Any] | None:
    return _one(conn, _SERVICE_SELECT + " WHERE s.id = %s AND s.deleted_at IS NULL", (service_id,))


def list_services(conn: psycopg.Connection, start: date, end: date) -> list[dict[str, Any]]:
    """Services with start <= service_date < end, oldest first."""
    return _all(
        conn,
        _SERVICE_SELECT
        + " WHERE s.deleted_at IS NULL AND s.service_date >= %s AND s.service_date < %s"
        " ORDER BY s.service_date, s.id",
        (start, end),
    )


def totals(conn: psycopg.Connection, start: date, end: date) -> dict[str, Any]:
    return _one(
        conn,
        """
        SELECT count(*) AS count,
               COALESCE(sum(price), 0) AS price,
               COALESCE(sum(collaborator_pay), 0) AS collaborator_pay,
               COALESCE(sum(profit), 0) AS profit
        FROM services
        WHERE deleted_at IS NULL AND service_date >= %s AND service_date < %s
        """,
        (start, end),
    )


def update_service(conn: psycopg.Connection, service_id: int, data: ServiceInput) -> None:
    """Edits data only: never resends anything nor touches issued numbers."""
    with conn.transaction():
        current = _one(
            conn,
            "SELECT collaborator_id, collaborator_pay, receipt_sent_at, receipt_number "
            "FROM services WHERE id = %s AND deleted_at IS NULL FOR UPDATE",
            (service_id,),
        )
        if current is None:
            raise LookupError(service_id)
        collaborator_changed = (
            current["collaborator_id"] != data.collaborator_id
            or current["collaborator_pay"] != data.collaborator_pay
        )
        if current["receipt_sent_at"] and collaborator_changed:
            raise ReceiptLocked()

        receipt_number = current["receipt_number"]
        if data.collaborator_id is not None and receipt_number is None:
            receipt = _one(
                conn,
                "UPDATE counters SET last_value = last_value + 1 WHERE name = 'receipt' RETURNING last_value",
            )
            receipt_number = format_invoice_number(receipt["last_value"])

        conn.execute(
            """
            UPDATE services SET service_date = %s, client_name = %s, job = %s, kind = %s,
                   scheduled_at = %s, deadline = %s, price = %s, collaborator_id = %s,
                   collaborator_pay = %s, notes = %s, receipt_number = %s, updated_at = now()
            WHERE id = %s
            """,
            (
                data.service_date,
                data.client_name,
                data.job,
                data.kind,
                data.scheduled_at,
                data.deadline,
                data.price,
                data.collaborator_id,
                data.collaborator_pay,
                data.notes,
                receipt_number,
                service_id,
            ),
        )
        _replace_emails(conn, service_id, data.emails)


def soft_delete_service(conn: psycopg.Connection, service_id: int) -> None:
    """Hides the service; its invoice/receipt numbers stay taken."""
    conn.execute(
        "UPDATE services SET deleted_at = now(), updated_at = now() WHERE id = %s", (service_id,)
    )
    conn.commit()


# --- processing steps -------------------------------------------------------


def acquire_lease(conn: psycopg.Connection, service_id: int, seconds: int = 180) -> bool:
    row = _one(
        conn,
        """
        UPDATE services SET processing_until = now() + make_interval(secs => %s)
        WHERE id = %s AND (processing_until IS NULL OR processing_until < now())
        RETURNING id
        """,
        (seconds, service_id),
    )
    conn.commit()
    return row is not None


def release_lease(conn: psycopg.Connection, service_id: int) -> None:
    conn.execute("UPDATE services SET processing_until = NULL WHERE id = %s", (service_id,))
    conn.commit()


def mark_step(conn: psycopg.Connection, service_id: int, column: str, value: Any) -> None:
    if column not in _STEP_COLUMNS:
        raise ValueError(f"not a step column: {column}")
    conn.execute(f"UPDATE services SET {column} = %s WHERE id = %s", (value, service_id))
    conn.commit()


def mark_receipt_sent(conn: psycopg.Connection, service_id: int, name: str, email: str) -> None:
    """Stamps the receipt as sent together with who it went to, so later
    edits to the collaborator don't rewrite whom this receipt was issued to."""
    conn.execute(
        "UPDATE services SET receipt_sent_at = now(), receipt_to_name = %s, receipt_to_email = %s "
        "WHERE id = %s",
        (name, email, service_id),
    )
    conn.commit()


def set_last_error(conn: psycopg.Connection, service_id: int, message: str | None) -> None:
    conn.execute("UPDATE services SET last_error = %s WHERE id = %s", (message, service_id))
    conn.commit()


# --- login throttling -------------------------------------------------------


def login_locked(conn: psycopg.Connection) -> bool:
    """Global (not per-IP) lock: 5 failures in the last 15 minutes."""
    row = _one(
        conn,
        "SELECT count(*) AS n FROM login_attempts "
        "WHERE NOT success AND at > now() - make_interval(mins => %s)",
        (LOGIN_WINDOW_MINUTES,),
    )
    return row["n"] >= LOGIN_MAX_FAILURES


def record_login_attempt(conn: psycopg.Connection, ip: str, success: bool) -> None:
    conn.execute("INSERT INTO login_attempts (ip, success) VALUES (%s, %s)", (ip, success))
    conn.commit()
