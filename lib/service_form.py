"""Validation of the "new/edit service" form.

Pure functions over the submitted fields, so every rejection rule is unit
tested without a browser or a database. Error messages are in Spanish: they
are shown to the owner as-is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

MAX_EMAILS = 5
KINDS = ("clase", "trabajo")
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


@dataclass
class ServiceInput:
    service_date: date
    client_name: str
    job: str
    kind: str
    scheduled_at: datetime | None
    deadline: date | None
    price: Decimal
    notes: str | None
    emails: list[str]
    collaborator_id: int | None = None
    collaborator_pay: Decimal = Decimal(0)

    @property
    def profit(self) -> Decimal:
        return self.price - self.collaborator_pay


@dataclass
class FormResult:
    value: ServiceInput | None
    errors: list[str] = field(default_factory=list)


def parse_collaborator_form(fields: dict[str, str]) -> tuple[str, str, str | None, list[str]]:
    """Returns (name, email, payout_name, errors) for the collaborator form.
    payout_name is the name as SINPE/Wink shows it; optional."""
    name = (fields.get("name") or "").strip()
    email = (fields.get("email") or "").strip().lower()
    payout_name = " ".join((fields.get("payout_name") or "").split()) or None
    errors = []
    if not name:
        errors.append("Falta el nombre.")
    if not _EMAIL_RE.match(email):
        errors.append("El correo no es válido.")
    if payout_name and len(payout_name.split()) < 2:
        errors.append("El nombre en SINPE/Wink debe tener al menos nombre y apellido.")
    return name, email, payout_name, errors


def _amount(raw: str) -> Decimal | None:
    raw = (raw or "").strip()
    return Decimal(raw) if raw.isdigit() else None


def parse_service_form(
    fields: dict[str, str],
    emails: list[str],
    timezone: str,
    active_collaborator_ids: set[int],
) -> FormResult:
    """`fields` holds the single-valued inputs; `emails` every "emails" input.

    Amounts are whole colones (the form uses <input type=number step=1>).
    """
    errors: list[str] = []
    get = lambda name: (fields.get(name) or "").strip()  # noqa: E731

    service_date = None
    try:
        service_date = date.fromisoformat(get("service_date"))
    except ValueError:
        errors.append("La fecha del servicio no es válida.")

    client_name, job = get("client_name"), get("job")
    if not client_name:
        errors.append("Falta el nombre del cliente.")
    if not job:
        errors.append("Falta el trabajo.")

    kind = get("kind")
    scheduled_at = deadline = None
    if kind not in KINDS:
        errors.append("Elige el tipo: clase o trabajo.")
    elif kind == "clase":
        try:
            scheduled_at = datetime.fromisoformat(get("scheduled_at")).replace(
                tzinfo=ZoneInfo(timezone)
            )
        except ValueError:
            errors.append("Una clase necesita fecha y hora.")
    else:
        try:
            deadline = date.fromisoformat(get("deadline"))
        except ValueError:
            errors.append("Un trabajo necesita fecha de entrega.")

    price = _amount(get("price"))
    if price is None or price <= 0:
        errors.append("El precio debe ser un monto mayor que 0, sin decimales.")

    cleaned: list[str] = []
    for raw in emails:
        email = raw.strip().lower()
        if not email:
            continue
        if not _EMAIL_RE.match(email):
            errors.append(f"Correo inválido: {raw.strip()}")
        elif email not in cleaned:
            cleaned.append(email)
    if not cleaned and not any("Correo inválido" in e for e in errors):
        errors.append("Agrega al menos un correo del cliente.")
    if len(cleaned) > MAX_EMAILS:
        errors.append(f"Máximo {MAX_EMAILS} correos por servicio.")

    collaborator_id = None
    collaborator_pay = Decimal(0)
    if get("hire") in ("1", "on", "true"):
        try:
            collaborator_id = int(get("collaborator_id"))
        except ValueError:
            collaborator_id = None
        if collaborator_id is None:
            errors.append("Marcaste contratar colaborador: elige a quién.")
        elif collaborator_id not in active_collaborator_ids:
            errors.append("Ese colaborador no existe o está desactivado.")
        pay = _amount(get("collaborator_pay"))
        if pay is None:
            errors.append("El pago al colaborador debe ser un monto sin decimales.")
        elif price is not None and pay > price:
            errors.append("El pago al colaborador no puede ser mayor que el precio.")
        else:
            collaborator_pay = pay

    if errors:
        return FormResult(None, errors)
    return FormResult(
        ServiceInput(
            service_date=service_date,
            client_name=client_name,
            job=job,
            kind=kind,
            scheduled_at=scheduled_at,
            deadline=deadline,
            price=price,
            notes=get("notes") or None,
            emails=cleaned,
            collaborator_id=collaborator_id,
            collaborator_pay=collaborator_pay,
        )
    )
