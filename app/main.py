"""Web app: the owner registers services here (it replaced the Notion form).

Single user, password-protected. Server-rendered HTML (Jinja2) with a few
lines of inline JS, so it works on a phone without a frontend build.

Run locally:
    uvicorn app.main:app --reload        (with COOKIE_SECURE=false in .env)
"""

from __future__ import annotations

import secrets
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib import db, ledger, payments
from lib.auth import verify_password
from lib.config import Config, ConfigError, get_config
from lib.pipeline import Senders, ServiceBusy, default_senders, process_service
from lib.service_form import MAX_EMAILS, MAX_NAMES, parse_collaborator_form, parse_service_form

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "app"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

MONTH_NAMES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre",
]  # fmt: skip

# Single-valued form inputs, in the order the confirm page re-posts them.
_FORM_FIELDS = (
    "service_date", "client_name", "job", "kind", "scheduled_at", "deadline",
    "price", "notes", "hire", "collaborator_id", "collaborator_pay",
)  # fmt: skip


class _LoginRequired(Exception):
    pass


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def create_app(config: Config | None = None, senders: Senders | None = None) -> FastAPI:
    config = config or get_config()
    for name, value in (
        ("DATABASE_URL", config.database_url),
        ("APP_PASSWORD_HASH", config.app_password_hash),
        ("SESSION_SECRET", config.session_secret),
    ):
        if not value:
            raise ConfigError(f"Missing environment variable {name}")
    senders = senders or default_senders()
    tz = ZoneInfo(config.timezone)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret,
        session_cookie="sid",
        max_age=30 * 24 * 3600,
        same_site="lax",
        https_only=config.cookie_secure,
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    def money(value: Any) -> str:
        amount = int(Decimal(value or 0))
        return f"{config.invoice_currency_symbol}{amount:,}".replace(",", ".")

    def dmy(value: date | datetime | None) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.astimezone(tz).strftime("%d/%m/%Y %H:%M")
        return value.strftime("%d/%m/%Y")

    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value * 100:.1f}%".replace(".", ",")

    templates.env.filters["money"] = money
    templates.env.filters["dmy"] = dmy
    templates.env.filters["pct"] = pct

    def today() -> date:
        return datetime.now(tz).date()

    def conn() -> psycopg.Connection:
        return db.connect(config.database_url, autocommit=True)

    def csrf_token(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)
        return request.session["csrf"]

    def render(request: Request, name: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        ctx.update(
            request=request,
            csrf=csrf_token(request),
            max_emails=MAX_EMAILS,
            max_names=MAX_NAMES,
            # Same name as the invoice letterhead (INVOICE_ISSUER_NAME).
            brand_name=getattr(config, "invoice_issuer_name", "") or "Servicios",
        )
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def require_login(request: Request) -> None:
        if not request.session.get("auth"):
            raise _LoginRequired()

    async def checked_form(request: Request):
        form = await request.form()
        expected = request.session.get("csrf")
        if not expected or not secrets.compare_digest(str(form.get("csrf", "")), expected):
            return None
        return form

    def forbidden() -> HTMLResponse:
        return HTMLResponse("Solicitud inválida (CSRF). Recarga la página.", status_code=403)

    def form_values(form) -> tuple[dict[str, str], list[str], list[str] | None]:
        fields = {name: str(form.get(name, "")) for name in _FORM_FIELDS}
        emails = [str(e) for e in form.getlist("emails")]
        # None when the form only sent the single "client_name" field.
        names = [str(n) for n in form.getlist("client_names")] or None
        return fields, emails, names

    def service_values(service: dict[str, Any]) -> tuple[dict[str, str], list[str], list[str]]:
        scheduled = service["scheduled_at"]
        fields = {
            "service_date": service["service_date"].isoformat(),
            "client_name": service["client_name"],
            "job": service["job"],
            "kind": service["kind"] or "",
            "scheduled_at": scheduled.astimezone(tz).strftime("%Y-%m-%dT%H:%M") if scheduled else "",
            "deadline": service["deadline"].isoformat() if service["deadline"] else "",
            "price": str(int(service["price"])),
            "notes": service["notes"] or "",
            "hire": "1" if service["collaborator_id"] else "",
            "collaborator_id": str(service["collaborator_id"] or ""),
            "collaborator_pay": str(int(service["collaborator_pay"])),
        }
        return fields, list(service["emails"]), db.client_names(service)

    def form_page(request, c, status, *, action, fields, emails, errors, names=None, service=None):
        return render(
            request,
            "service_form.html",
            status=status,
            action=action,
            fields=fields,
            emails=emails or [""],
            names=names or [fields.get("client_name", "")],
            errors=errors,
            service=service,
            collaborators=db.list_collaborators(c),
            contacts=[
                {"key": x["client_key"], "name": x["name"], "emails": list(x["emails"])}
                for x in db.list_contacts(c)
            ],
        )

    @app.exception_handler(_LoginRequired)
    async def _to_login(request: Request, exc: _LoginRequired) -> Response:
        return RedirectResponse("/login", status_code=302)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    # Installable on a phone ("Agregar a pantalla de inicio"): opens full
    # screen with its own icon. Icons and manifest are public; they hold
    # nothing but the logo and the name.
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/manifest.webmanifest")
    def manifest() -> JSONResponse:
        name = getattr(config, "invoice_issuer_name", "") or "Servicios"
        return JSONResponse(
            {
                "name": name,
                "short_name": "Servicios",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#F6F7FB",
                "theme_color": "#0F226F",
                "lang": "es",
                "icons": [
                    {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                    {"src": "/static/icon-maskable-512.png", "sizes": "512x512",
                     "type": "image/png", "purpose": "maskable"},
                ],
            },
            media_type="application/manifest+json",
        )

    # --- login -------------------------------------------------------------

    @app.get("/login")
    def login_page(request: Request):
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request):
        form = await checked_form(request)
        if form is None:
            return forbidden()
        ip = request.client.host if request.client else ""
        with conn() as c:
            if db.login_locked(c):
                return render(
                    request,
                    "login.html",
                    status=429,
                    error="Demasiados intentos fallidos. Espera 15 minutos.",
                )
            ok = verify_password(str(form.get("password", "")), config.app_password_hash)
            db.record_login_attempt(c, ip, ok)
        if not ok:
            return render(request, "login.html", status=401, error="Contraseña incorrecta.")
        request.session.clear()
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        if await checked_form(request) is None:
            return forbidden()
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # --- services ------------------------------------------------------------

    def month_page(request: Request, year: int, month: int):
        start, end = _month_range(year, month)
        prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
        next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
        with conn() as c:
            return render(
                request,
                "services.html",
                services=db.list_services(c, start, end),
                totals=db.totals(c, start, end),
                title=f"{MONTH_NAMES[month - 1]} {year}",
                prev=f"/servicios?anio={prev_y}&mes={prev_m}",
                next=f"/servicios?anio={next_y}&mes={next_m}",
            )

    @app.get("/")
    def home(request: Request):
        require_login(request)
        now = today()
        return month_page(request, now.year, now.month)

    @app.get("/servicios")
    def services(request: Request, anio: int | None = None, mes: int | None = None):
        require_login(request)
        now = today()
        year, month = anio or now.year, mes or now.month
        if not 1 <= month <= 12 or not 2000 <= year <= 2100:
            return HTMLResponse("Mes inválido.", status_code=400)
        return month_page(request, year, month)

    @app.get("/servicios/nuevo")
    def new_service(request: Request):
        require_login(request)
        with conn() as c:
            return form_page(
                request,
                c,
                200,
                action="/servicios/confirmar",
                fields={"service_date": today().isoformat(), "kind": "clase"},
                emails=[""],
                errors=[],
            )

    @app.post("/servicios/confirmar")
    async def confirm_service(request: Request):
        require_login(request)
        form = await checked_form(request)
        if form is None:
            return forbidden()
        fields, emails, names = form_values(form)
        with conn() as c:
            collaborators = db.list_collaborators(c)
            result = parse_service_form(
                fields, emails, config.timezone, {x["id"] for x in collaborators}, names
            )
            if result.errors:
                return form_page(
                    request, c, 400, action="/servicios/confirmar",
                    fields=fields, emails=emails, names=names, errors=result.errors,
                )  # fmt: skip
        collaborator = next(
            (x for x in collaborators if x["id"] == result.value.collaborator_id), None
        )
        return render(
            request,
            "service_confirm.html",
            data=result.value,
            fields=fields,
            emails=result.value.emails,
            names=result.value.client_names,
            collaborator=collaborator,
            token=str(uuid.uuid4()),
        )

    @app.post("/servicios")
    async def create_service(request: Request):
        require_login(request)
        form = await checked_form(request)
        if form is None:
            return forbidden()
        try:
            token = uuid.UUID(str(form.get("submission_token", "")))
        except ValueError:
            return HTMLResponse("Falta el identificador del envío.", status_code=400)
        fields, emails, names = form_values(form)
        with conn() as c:
            existing = db.find_service_by_token(c, token)
            if existing:
                return RedirectResponse(f"/servicios/{existing}", status_code=303)
            collaborators = db.list_collaborators(c)
            result = parse_service_form(
                fields, emails, config.timezone, {x["id"] for x in collaborators}, names
            )
            if result.errors:
                return form_page(
                    request, c, 400, action="/servicios/confirmar",
                    fields=fields, emails=emails, names=names, errors=result.errors,
                )  # fmt: skip
            try:
                service_id = db.create_service(c, result.value, token)
            except db.InvoicingNotInitialized:
                return render(request, "not_ready.html", status=503)
            except psycopg.errors.UniqueViolation:
                # A double submit raced this one past the token check.
                existing = db.find_service_by_token(c, token)
                return RedirectResponse(f"/servicios/{existing}", status_code=303)
            try:
                process_service(c, config, service_id, senders)
            except ServiceBusy:
                pass
        return RedirectResponse(f"/servicios/{service_id}", status_code=303)

    @app.get("/servicios/{service_id}")
    def service_detail(request: Request, service_id: int, aviso: str | None = None):
        require_login(request)
        with conn() as c:
            service = db.get_service(c, service_id)
        if service is None:
            return HTMLResponse("No existe ese servicio.", status_code=404)
        return render(request, "service_detail.html", s=service, aviso=aviso)

    @app.post("/servicios/{service_id}/reintentar")
    async def retry_service(request: Request, service_id: int):
        require_login(request)
        if await checked_form(request) is None:
            return forbidden()
        with conn() as c:
            if db.get_service(c, service_id) is None:
                return HTMLResponse("No existe ese servicio.", status_code=404)
            try:
                process_service(c, config, service_id, senders)
            except ServiceBusy:
                return HTMLResponse(
                    "Ya se está procesando este servicio. Espera un momento y recarga.",
                    status_code=409,
                )
        return RedirectResponse(f"/servicios/{service_id}", status_code=303)

    @app.get("/servicios/{service_id}/editar")
    def edit_service_page(request: Request, service_id: int):
        require_login(request)
        with conn() as c:
            service = db.get_service(c, service_id)
            if service is None:
                return HTMLResponse("No existe ese servicio.", status_code=404)
            fields, emails, names = service_values(service)
            return form_page(
                request, c, 200, action=f"/servicios/{service_id}/editar",
                fields=fields, emails=emails, names=names, errors=[], service=service,
            )  # fmt: skip

    @app.post("/servicios/{service_id}/editar")
    async def edit_service(request: Request, service_id: int):
        require_login(request)
        form = await checked_form(request)
        if form is None:
            return forbidden()
        fields, emails, names = form_values(form)
        with conn() as c:
            service = db.get_service(c, service_id)
            if service is None:
                return HTMLResponse("No existe ese servicio.", status_code=404)
            collaborators = db.list_collaborators(c)
            # A collaborator deactivated after being hired stays valid here.
            allowed = {x["id"] for x in collaborators} | (
                {service["collaborator_id"]} if service["collaborator_id"] else set()
            )
            result = parse_service_form(fields, emails, config.timezone, allowed, names)
            errors = result.errors
            if not errors:
                try:
                    db.update_service(c, service_id, result.value)
                except db.ReceiptLocked:
                    errors = [
                        "El recibo del colaborador ya se envió: no se puede cambiar "
                        "el colaborador ni su pago."
                    ]
            if errors:
                return form_page(
                    request, c, 400, action=f"/servicios/{service_id}/editar",
                    fields=fields, emails=emails, names=names, errors=errors, service=service,
                )  # fmt: skip
        new = result.value
        calendar_moved = service["calendar_event_id"] and (
            new.kind != service["kind"]
            or new.scheduled_at != service["scheduled_at"]
            or new.deadline != service["deadline"]
        )
        suffix = "?aviso=calendario" if calendar_moved else "?aviso=editado"
        return RedirectResponse(f"/servicios/{service_id}{suffix}", status_code=303)

    @app.get("/servicios/{service_id}/borrar")
    def delete_page(request: Request, service_id: int):
        require_login(request)
        with conn() as c:
            service = db.get_service(c, service_id)
        if service is None:
            return HTMLResponse("No existe ese servicio.", status_code=404)
        return render(request, "service_delete.html", s=service)

    @app.post("/servicios/{service_id}/borrar")
    async def delete_service(request: Request, service_id: int):
        require_login(request)
        if await checked_form(request) is None:
            return forbidden()
        with conn() as c:
            db.soft_delete_service(c, service_id)
        return RedirectResponse("/", status_code=303)

    # --- earnings summary and export -------------------------------------------

    def year_rows(c: psycopg.Connection, year: int) -> list[dict[str, Any]]:
        start, end = ledger.year_range(year)
        return db.list_services(c, start, end)

    def valid_year(anio: int | None) -> int | None:
        year = anio or today().year
        return year if 2000 <= year <= 2100 else None

    @app.get("/resumen")
    def summary(request: Request, anio: int | None = None):
        require_login(request)
        year = valid_year(anio)
        if year is None:
            return HTMLResponse("Año inválido.", status_code=400)
        with conn() as c:
            s = ledger.summarize(year_rows(c, year), year)
        return render(request, "summary.html", s=s, growth=s.month_growth(), year=year)

    @app.get("/exportar.csv")
    def export(request: Request, anio: int | None = None):
        require_login(request)
        year = valid_year(anio)
        if year is None:
            return HTMLResponse("Año inválido.", status_code=400)
        with conn() as c:
            body = ledger.export_csv(year_rows(c, year), year)
        return Response(
            body.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="Ganancias_{year}_completo.csv"',
                "Cache-Control": "no-store",
            },
        )

    # --- payments ---------------------------------------------------------------

    _KINDS = {"cliente": "client", "colaborador": "collaborator"}

    def events(c: psycopg.Connection, where: str, limit: int) -> list[dict[str, Any]]:
        return payments.query(
            c,
            "SELECT e.*, COALESCE(json_agg(json_build_object('id', s.id, 'client_name', s.client_name, "
            "'job', s.job) ORDER BY s.service_date) FILTER (WHERE s.id IS NOT NULL), '[]') AS services "
            "FROM payment_events e LEFT JOIN payment_allocations a ON a.payment_id = e.id "
            f"LEFT JOIN services s ON s.id = a.service_id WHERE {where} "
            "GROUP BY e.id ORDER BY e.occurred_on DESC, e.id DESC LIMIT %s",
            (limit,),
        )

    @app.get("/pagos")
    def payments_page(request: Request):
        require_login(request)
        with conn() as c:
            to_collect = payments.pending_client_services(c)
            to_pay = payments.pending_collaborator_services(c)
            by_collaborator: dict[str, list[dict[str, Any]]] = {}
            for s in to_pay:
                by_collaborator.setdefault(s["collaborator_name"], []).append(s)
            last_sync = payments.query(c, "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
            to_confirm = events(c, "e.status = 'pending'", 100)
            ignored = events(
                c, f"e.status = 'ignored' AND COALESCE(e.note, '') <> '{payments.OWN_MOVEMENT}'", 40
            )
            for e in to_confirm + ignored:
                # Same amount first, then oldest; a known collaborator's own only.
                if e["source"] == "bn_statement":
                    pool, field = to_collect, "price"
                else:
                    pool = [s for s in to_pay if e["collaborator_id"] in (None, s["collaborator_id"])]
                    field = "collaborator_pay"
                e["candidates"] = sorted(
                    pool, key=lambda s: (s[field] != e["amount"], s["service_date"], s["id"])
                )
                e["field"] = field
            return render(
                request,
                "payments.html",
                to_collect=to_collect,
                to_collect_total=sum(s["price"] for s in to_collect),
                by_collaborator=by_collaborator,
                to_pay_total=sum(s["collaborator_pay"] for s in to_pay),
                to_confirm=to_confirm,
                ignored=ignored,
                recent=events(c, "e.status = 'matched'", 30),
                last_sync=last_sync[0] if last_sync else None,
            )

    @app.post("/pagos/servicios/{service_id}/{who}/{state}")
    async def set_paid(request: Request, service_id: int, who: str, state: str):
        require_login(request)
        kind = _KINDS.get(who)
        if kind is None or state not in ("pagado", "pendiente"):
            return HTMLResponse("Acción inválida.", status_code=404)
        if await checked_form(request) is None:
            return forbidden()
        with conn() as c:
            if db.get_service(c, service_id) is None:
                return HTMLResponse("No existe ese servicio.", status_code=404)
            if state == "pagado":
                payments.mark_paid_by_hand(c, service_id, kind, today())
            else:
                payments.unallocate(c, service_id, kind)
        # Back to the page the button was on; only our own paths, never a full URL.
        back = urlparse(request.headers.get("referer") or "").path
        if back not in ("/pagos", f"/servicios/{service_id}"):
            back = f"/servicios/{service_id}"
        return RedirectResponse(back, status_code=303)

    @app.post("/pagos/movimientos/{event_id}/asignar")
    async def assign_payment(request: Request, event_id: int):
        require_login(request)
        form = await checked_form(request)
        if form is None:
            return forbidden()
        try:
            service_ids = sorted({int(x) for x in form.getlist("service_id")})
        except ValueError:
            return HTMLResponse("Servicio inválido.", status_code=400)
        if not service_ids:
            return HTMLResponse("Elige al menos un servicio.", status_code=400)
        with conn() as c:
            [event] = payments.query(c, "SELECT * FROM payment_events WHERE id = %s", (event_id,)) or [None]
            if event is None or event["status"] == "matched":
                return HTMLResponse("Ese movimiento no existe o ya está asignado.", status_code=404)
            kind = "client" if event["source"] == "bn_statement" else "collaborator"
            pending = (
                payments.pending_client_services(c)
                if kind == "client"
                else payments.pending_collaborator_services(c)
            )
            chosen = [s for s in pending if s["id"] in service_ids]
            if len(chosen) != len(service_ids):
                return HTMLResponse("Algún servicio ya no está pendiente.", status_code=400)
            payments.allocate(c, event_id, service_ids, kind, auto=False)
            if kind == "client" and form.get("remember") and len({s["client_name"] for s in chosen}) == 1:
                payments.remember_alias(c, event["counterparty"], chosen[0]["client_name"])
        return RedirectResponse("/pagos", status_code=303)

    @app.post("/pagos/movimientos/{event_id}/ignorar")
    async def ignore_payment(request: Request, event_id: int):
        require_login(request)
        if await checked_form(request) is None:
            return forbidden()
        with conn() as c:
            payments.ignore_by_hand(c, event_id)
        return RedirectResponse("/pagos", status_code=303)

    # --- collaborators ---------------------------------------------------------

    @app.get("/colaboradores")
    def collaborators(request: Request):
        require_login(request)
        with conn() as c:
            return render(
                request, "collaborators.html", collaborators=db.list_collaborators(c, False)
            )

    def collaborator_form(request, status, *, action, fields, errors, collaborator=None):
        return render(
            request,
            "collaborator_form.html",
            status=status,
            action=action,
            fields=fields,
            errors=errors,
            collaborator=collaborator,
        )

    @app.get("/colaboradores/nuevo")
    def new_collaborator(request: Request):
        require_login(request)
        return collaborator_form(
            request, 200, action="/colaboradores/nuevo", fields={}, errors=[]
        )

    @app.post("/colaboradores/nuevo")
    async def create_collaborator(request: Request):
        require_login(request)
        form = await checked_form(request)
        if form is None:
            return forbidden()
        fields = {k: str(form.get(k, "")) for k in ("name", "email", "payout_name")}
        name, email, payout_name, errors = parse_collaborator_form(fields)
        if not errors:
            with conn() as c:
                try:
                    db.create_collaborator(c, name, email, payout_name)
                except db.DuplicateEmail:
                    errors = ["Ya hay un colaborador con ese correo."]
        if errors:
            return collaborator_form(
                request, 400, action="/colaboradores/nuevo", fields=fields, errors=errors
            )
        return RedirectResponse("/colaboradores", status_code=303)

    @app.get("/colaboradores/{collaborator_id}/editar")
    def edit_collaborator_page(request: Request, collaborator_id: int):
        require_login(request)
        with conn() as c:
            collaborator = db.get_collaborator(c, collaborator_id)
        if collaborator is None:
            return HTMLResponse("No existe ese colaborador.", status_code=404)
        return collaborator_form(
            request, 200, action=f"/colaboradores/{collaborator_id}/editar",
            fields=collaborator, errors=[], collaborator=collaborator,
        )  # fmt: skip

    @app.post("/colaboradores/{collaborator_id}/editar")
    async def edit_collaborator(request: Request, collaborator_id: int):
        require_login(request)
        form = await checked_form(request)
        if form is None:
            return forbidden()
        fields = {k: str(form.get(k, "")) for k in ("name", "email", "payout_name")}
        name, email, payout_name, errors = parse_collaborator_form(fields)
        with conn() as c:
            collaborator = db.get_collaborator(c, collaborator_id)
            if collaborator is None:
                return HTMLResponse("No existe ese colaborador.", status_code=404)
            if not errors:
                try:
                    db.update_collaborator(c, collaborator_id, name, email, payout_name)
                except db.DuplicateEmail:
                    errors = ["Ya hay un colaborador con ese correo."]
        if errors:
            return collaborator_form(
                request, 400, action=f"/colaboradores/{collaborator_id}/editar",
                fields=fields, errors=errors, collaborator=collaborator,
            )  # fmt: skip
        return RedirectResponse("/colaboradores", status_code=303)

    @app.post("/colaboradores/{collaborator_id}/{action}")
    async def toggle_collaborator(request: Request, collaborator_id: int, action: str):
        require_login(request)
        if action not in ("activar", "desactivar"):
            return HTMLResponse("Acción inválida.", status_code=404)
        if await checked_form(request) is None:
            return forbidden()
        with conn() as c:
            if db.get_collaborator(c, collaborator_id) is None:
                return HTMLResponse("No existe ese colaborador.", status_code=404)
            db.set_collaborator_active(c, collaborator_id, action == "activar")
        return RedirectResponse("/colaboradores", status_code=303)

    return app


def _app_or_none() -> FastAPI | None:
    # `uvicorn app.main:app` needs a module-level app; tests build their own
    # with create_app() and must be able to import this module without one.
    try:
        return create_app()
    except ConfigError:
        return None


app = _app_or_none()
