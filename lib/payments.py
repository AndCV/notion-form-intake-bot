"""Matches money movements read from email to services.

Clients (BN statement credits) -- a service is marked paid automatically
only when BOTH hold:
  - the amount equals its price (or the sum of that client's pending
    services, for one transfer covering several), and
  - the payer's name on the statement matches the client's name (a shared
    word, allowing for the statement's truncation), or a payer alias the
    owner confirmed earlier.
Anything else that could be a client payment waits in "Por confirmar".

Collaborators (Wink sends) -- the recipient name is matched to a
collaborator's payout_name, then the amount to one pending service's pay,
or to the oldest pending services adding up to it.

Every service is tracked except those imported from the old workbook
(source = 'import'), which were already settled. Unmatched movements are
re-evaluated on every sync, since a service may be registered after it
was paid.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from .ledger import client_key
from .payment_mail import Movement

CLIENT_WINDOW_BEFORE = timedelta(days=60)  # paid up to 60 days after the service date
CLIENT_WINDOW_AFTER = timedelta(days=30)   # ...or up to 30 days before it (prepaid)

# Notes that make an unmatched movement final: never re-evaluated.
OWN_MOVEMENT = "Movimiento propio"
IGNORED_BY_HAND = "Ignorado a mano"
_FINAL_NOTES = (OWN_MOVEMENT, IGNORED_BY_HAND)


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    return [t for t in re.split(r"[^A-Z]+", text) if t]


def payer_key(payer: str) -> str:
    return " ".join(tokens(payer))


def _same_word(a: str, b: str) -> bool:
    """Equal, or one is the other cut short (statements truncate names)."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 4 and long_.startswith(short)


def is_owner(payer: str, owner: str) -> bool:
    """The owner moving their own money ("CUBILLO VILLALOBOS A", ...)."""
    # Every word must start one of the owner's words: the statement cuts
    # names anywhere ("...CUBILLO VIL", "...VILLALOBOS A").
    payer_t, owner_t = tokens(payer), tokens(owner)
    return bool(payer_t) and bool(owner_t) and all(
        any(o.startswith(p) for o in owner_t) for p in payer_t
    )


def name_matches(payer: str, client_name: str, aliases: dict[str, str]) -> bool:
    if aliases.get(payer_key(payer)) == client_key(client_name):
        return True
    payer_t = [p for p in tokens(payer) if len(p) >= 3]
    return any(_same_word(c, p) for c in tokens(client_name) if len(c) >= 3 for p in payer_t)


def recipient_matches(recipient: str, payout_name: str | None) -> bool:
    """Wink shows the first words of the registered name, in order
    ("ANGEL_EDUARDO_FALLAS" for "Angel Eduardo Fallas Valverde")."""
    rec, full = tokens(recipient), tokens(payout_name or "")
    if len(rec) < 2 or len(rec) > len(full):
        return False
    return all(_same_word(r, f) for r, f in zip(rec, full))


@dataclass
class Decision:
    status: str                      # 'matched' | 'pending' | 'ignored'
    service_ids: list[int]
    note: str | None = None
    collaborator_id: int | None = None


def _oldest_first(services: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(services, key=lambda s: (s["service_date"], s["id"]))


def _prefix_summing_to(services: list[dict[str, Any]], amount: Decimal, field: str) -> list[int]:
    total, picked = Decimal(0), []
    for s in services:
        total += Decimal(s[field])
        picked.append(s["id"])
        if total == amount:
            return picked
        if total > amount:
            break
    return []


def decide_client(
    credit: Movement, owner: str, pending: list[dict[str, Any]], aliases: dict[str, str]
) -> Decision:
    """`pending`: unpaid web app services (id, service_date, client_name, price)."""
    if owner and is_owner(credit.counterparty, owner):
        return Decision("ignored", [], OWN_MOVEMENT)
    window = [
        s for s in pending
        if credit.occurred_on - CLIENT_WINDOW_BEFORE <= s["service_date"] <= credit.occurred_on + CLIENT_WINDOW_AFTER
    ]  # fmt: skip
    named = [s for s in window if name_matches(credit.counterparty, s["client_name"], aliases)]
    exact = [s for s in _oldest_first(named) if Decimal(s["price"]) == credit.amount]
    if exact:
        return Decision("matched", [exact[0]["id"]])
    by_client: dict[str, list[dict[str, Any]]] = {}
    for s in named:
        by_client.setdefault(client_key(s["client_name"]), []).append(s)
    for services in by_client.values():
        ids = _prefix_summing_to(_oldest_first(services), credit.amount, "price")
        if len(ids) > 1:
            return Decision("matched", ids)
    same_amount = [s for s in window if Decimal(s["price"]) == credit.amount]
    if same_amount:
        clients = ", ".join(sorted({s["client_name"] for s in same_amount}))
        return Decision("pending", [], f"El monto coincide con {clients}, pero el nombre no")
    if named:
        clients = ", ".join(sorted({s["client_name"] for s in named}))
        return Decision("pending", [], f"El nombre coincide con {clients}, pero el monto no")
    return Decision("ignored", [], "Ningún cobro pendiente con ese nombre o monto")


def decide_collaborator(
    send: Movement, collaborators: list[dict[str, Any]], pending: list[dict[str, Any]]
) -> Decision:
    """`pending`: unpaid web app services with a collaborator
    (id, service_date, collaborator_id, collaborator_pay)."""
    collab = next(
        (c for c in collaborators if recipient_matches(send.counterparty, c["payout_name"])), None
    )
    if collab is None:
        return Decision("ignored", [], "No corresponde a ningún colaborador")
    mine = _oldest_first(s for s in pending if s["collaborator_id"] == collab["id"])
    exact = [s for s in mine if Decimal(s["collaborator_pay"]) == send.amount]
    if exact:
        return Decision("matched", [exact[0]["id"]], collaborator_id=collab["id"])
    ids = _prefix_summing_to(mine, send.amount, "collaborator_pay")
    if ids:
        return Decision("matched", ids, collaborator_id=collab["id"])
    note = (
        f"No cuadra con los pagos pendientes de {collab['name']}"
        if mine
        else f"{collab['name']} no tiene pagos pendientes"
    )
    return Decision("pending", [], note, collaborator_id=collab["id"])


# --- database ----------------------------------------------------------------


def query(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def pending_client_services(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return query(
        conn,
        "SELECT id, service_date, client_name, job, price FROM services "
        "WHERE source <> 'import' AND deleted_at IS NULL AND client_paid_on IS NULL "
        "ORDER BY service_date, id",
    )


def pending_collaborator_services(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return query(
        conn,
        "SELECT s.id, s.service_date, s.client_name, s.job, s.collaborator_id, s.collaborator_pay, "
        "c.name AS collaborator_name FROM services s JOIN collaborators c ON c.id = s.collaborator_id "
        "WHERE s.source <> 'import' AND s.deleted_at IS NULL AND s.collaborator_pay > 0 "
        "AND s.collaborator_paid_on IS NULL ORDER BY s.service_date, s.id",
    )


def aliases(conn: psycopg.Connection) -> dict[str, str]:
    return {r["payer_key"]: r["client_key"] for r in query(conn, "SELECT * FROM payer_aliases")}


def record_movement(conn: psycopg.Connection, m: Movement) -> int | None:
    """Stores a movement once; None if it was already stored."""
    rows = query(
        conn,
        "INSERT INTO payment_events (source, occurred_on, amount, counterparty, description, external_ref) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (source, external_ref) DO NOTHING RETURNING id",
        (m.source, m.occurred_on, m.amount, m.counterparty, m.description, m.external_ref),
    )
    return rows[0]["id"] if rows else None


def allocate(
    conn: psycopg.Connection, event_id: int, service_ids: list[int], kind: str, auto: bool
) -> None:
    """Marks the services paid on the movement's date, in one transaction."""
    column = "client_paid_on" if kind == "client" else "collaborator_paid_on"
    amount_col = "price" if kind == "client" else "collaborator_pay"
    with conn.transaction():
        event = query(conn, "SELECT occurred_on FROM payment_events WHERE id = %s FOR UPDATE", (event_id,))[0]
        for sid in service_ids:
            conn.execute(
                f"INSERT INTO payment_allocations (payment_id, service_id, kind, amount, auto) "
                f"SELECT %s, id, %s, {amount_col}, %s FROM services WHERE id = %s",
                (event_id, kind, auto, sid),
            )
            conn.execute(f"UPDATE services SET {column} = %s WHERE id = %s", (event["occurred_on"], sid))
        conn.execute(
            "UPDATE payment_events SET status = 'matched', note = NULL WHERE id = %s", (event_id,)
        )


def ingest(conn: psycopg.Connection, m: Movement, owner: str = "") -> int | None:
    """Stores a new movement; the owner's own transfers are set aside for
    good (the holder's name only comes with the statement)."""
    event_id = record_movement(conn, m)
    if event_id and m.source == "bn_statement" and is_owner(m.counterparty, owner):
        conn.execute(
            "UPDATE payment_events SET status = 'ignored', note = %s WHERE id = %s",
            (OWN_MOVEMENT, event_id),
        )
    return event_id


def ignore_by_hand(conn: psycopg.Connection, event_id: int) -> None:
    conn.execute(
        "UPDATE payment_events SET status = 'ignored', note = %s WHERE id = %s AND status <> 'matched'",
        (IGNORED_BY_HAND, event_id),
    )


def rematch(conn: psycopg.Connection) -> dict[str, int]:
    """Re-evaluates every movement not matched yet, except the owner's own
    and those ignored by hand."""
    counts = {"matched": 0, "pending": 0, "ignored": 0}
    known_aliases = aliases(conn)
    collaborators = query(conn, "SELECT id, name, payout_name FROM collaborators")
    for event in query(
        conn,
        "SELECT * FROM payment_events WHERE status <> 'matched' "
        "AND COALESCE(note, '') NOT IN (%s, %s) ORDER BY occurred_on, id",
        _FINAL_NOTES,
    ):
        movement = Movement(event["source"], event["occurred_on"], Decimal(event["amount"]),
                            event["counterparty"], event["description"] or "", event["external_ref"])  # fmt: skip
        if event["source"] == "bn_statement":
            decision = decide_client(movement, "", pending_client_services(conn), known_aliases)
            kind = "client"
        else:
            decision = decide_collaborator(movement, collaborators, pending_collaborator_services(conn))
            kind = "collaborator"
        if decision.status == "matched":
            allocate(conn, event["id"], decision.service_ids, kind, auto=True)
            conn.execute(
                "UPDATE payment_events SET collaborator_id = %s WHERE id = %s",
                (decision.collaborator_id, event["id"]),
            )
        else:
            conn.execute(
                "UPDATE payment_events SET status = %s, note = %s, collaborator_id = %s WHERE id = %s",
                (decision.status, decision.note, decision.collaborator_id, event["id"]),
            )
        counts[decision.status] += 1
    return counts


def unallocate(conn: psycopg.Connection, service_id: int, kind: str) -> None:
    """Undoes a service's payment; movements left without any allocation
    go back to "Por confirmar"."""
    column = "client_paid_on" if kind == "client" else "collaborator_paid_on"
    with conn.transaction():
        freed = query(
            conn,
            "DELETE FROM payment_allocations WHERE service_id = %s AND kind = %s RETURNING payment_id",
            (service_id, kind),
        )
        conn.execute(f"UPDATE services SET {column} = NULL WHERE id = %s", (service_id,))
        for row in freed:
            conn.execute(
                "UPDATE payment_events SET status = 'pending', note = 'Desasignado a mano' "
                "WHERE id = %s AND NOT EXISTS (SELECT 1 FROM payment_allocations WHERE payment_id = %s)",
                (row["payment_id"], row["payment_id"]),
            )


def mark_paid_by_hand(conn: psycopg.Connection, service_id: int, kind: str, on: date) -> None:
    column = "client_paid_on" if kind == "client" else "collaborator_paid_on"
    conn.execute(
        f"UPDATE services SET {column} = %s WHERE id = %s AND {column} IS NULL", (on, service_id)
    )


def remember_alias(conn: psycopg.Connection, payer: str, client_name: str) -> None:
    conn.execute(
        "INSERT INTO payer_aliases (payer_key, client_key) VALUES (%s, %s) "
        "ON CONFLICT (payer_key) DO UPDATE SET client_key = EXCLUDED.client_key",
        (payer_key(payer), client_key(client_name)),
    )
