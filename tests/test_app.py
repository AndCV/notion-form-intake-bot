"""Integration tests: the real FastAPI app against a real, disposable Postgres.

Needs TEST_DATABASE_URL pointing at a throwaway database -- every test drops
and recreates its `public` schema. Locally:

    docker run -d --name forms-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=forms_test \
        -p 127.0.0.1:55432:5432 postgres:17-alpine
    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/forms_test pytest

Skipped when TEST_DATABASE_URL is unset. Google Calendar and Zoho are fakes
(FakeSenders): these tests prove what the app asks them to do, not that
Google or Zoho accept it.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")

if TEST_DB:
    import psycopg
    from fastapi.testclient import TestClient

    from app.main import create_app
    from lib import db
    from lib.auth import hash_password
    from lib.pipeline import Senders
    from lib.service_form import ServiceInput

PASSWORD = "una contraseña larga"
SCHEMA = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()


class FakeSenders:
    def __init__(self):
        self.calendar, self.confirmations, self.invoices = [], [], []
        self.receipts, self.receipt_pdfs = [], []
        self.calendar_error = self.receipt_error = None
        self.refused = {}

    def senders(self):
        return Senders(
            create_calendar_event=self._calendar,
            send_confirmation_email=self._confirmation,
            send_invoice_email=self._invoice,
            build_ics=lambda sub, tz, minutes: b"ICS",
            build_invoice_pdf=lambda cfg, sub, number, issued: b"%PDF",
            send_receipt_email=self._receipt,
            build_receipt_pdf=self._receipt_pdf,
        )

    def _receipt(self, config, to_name, to_email, details, pdf, number):
        if self.receipt_error:
            raise RuntimeError(self.receipt_error)
        self.receipts.append(dict(name=to_name, email=to_email, details=details, number=number))
        return {}

    def _receipt_pdf(self, config, name, description, amount, number, issued):
        self.receipt_pdfs.append(dict(name=name, description=description, amount=amount))
        return b"%PDF"

    def _calendar(self, config, sub):
        if self.calendar_error:
            raise RuntimeError(self.calendar_error)
        self.calendar.append(sub)
        return {"id": f"evt-{len(self.calendar)}"}

    def _confirmation(self, config, sub, ics):
        self.confirmations.append(sub)
        return self.refused

    def _invoice(self, config, sub, pdf, number):
        self.invoices.append((sub, number))
        return {}


@pytest.fixture
def pg():
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.execute(SCHEMA)
        conn.execute("INSERT INTO counters VALUES ('invoice', 41), ('receipt', 0)")
        conn.execute(
            "INSERT INTO collaborators (id, name, email) VALUES "
            "(1, 'Colaborador Uno', 'uno@example.com'), (2, 'Colaborador Dos', 'dos@example.com')"
        )
        conn.execute("UPDATE collaborators SET active = FALSE WHERE id = 2")
        conn.execute("SELECT setval('collaborators_id_seq', 2)")
    yield TEST_DB


@pytest.fixture
def fake():
    return FakeSenders()


@pytest.fixture
def client(pg, fake):
    config = SimpleNamespace(
        database_url=pg,
        app_password_hash=hash_password(PASSWORD),
        session_secret="test-secret",
        cookie_secure=True,
        timezone="America/Costa_Rica",
        class_duration_minutes=60,
        invoice_currency_symbol="₡",
    )
    app = create_app(config, fake.senders())
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _csrf(client, path="/login"):
    html = client.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _login(client, password=PASSWORD):
    return client.post(
        "/login", data={"csrf": _csrf(client), "password": password}, follow_redirects=False
    )


def _service_fields(**overrides):
    base = dict(
        service_date="2026-10-20", client_name="Cliente A", job="Cálculo", kind="clase",
        scheduled_at="2026-10-21T15:30", deadline="", price="15000", notes="",
        hire="", collaborator_id="", collaborator_pay="",
    )  # fmt: skip
    base.update(overrides)
    return base


def _create(client, emails=("a@example.com", "b@example.com"), token=None, **overrides):
    data = _service_fields(**overrides)
    data["csrf"] = _csrf(client, "/servicios/nuevo")
    data["submission_token"] = token or str(uuid.uuid4())
    data["emails"] = list(emails)
    return client.post("/servicios", data=data, follow_redirects=False)


def _rows(sql, params=None):
    with psycopg.connect(TEST_DB) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _service_id(response):
    return int(response.headers["location"].rsplit("/", 1)[1])


# --- auth ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/servicios", "/servicios/nuevo", "/servicios/1", "/servicios/1/editar"]
)
def test_pages_require_login(client, path):
    """protects: every page except /login and /healthz sits behind the password;
    fails_when: a page renders data without a session;
    no_claim: POST routes are covered by the CSRF test."""
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_healthz_is_public_and_empty(client):
    """protects: Cloud Run's health check works without a session;
    fails_when: it redirects or leaks data;
    no_claim: none beyond protects."""
    response = client.get("/healthz")

    assert response.status_code == 200 and response.json() == {"ok": True}


def test_global_lockout_after_five_failures_even_with_the_right_password(client):
    """protects: brute force stops after 5 wrong passwords in 15 minutes;
    fails_when: a 6th attempt (right or wrong) gets through;
    no_claim: the 15-minute expiry itself is not time-travelled here."""
    for _ in range(5):
        assert _login(client, "wrong").status_code == 401

    response = _login(client)

    assert response.status_code == 429
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_login_with_right_password_opens_the_app(client):
    """protects: the owner gets in with the configured password;
    fails_when: login fails or doesn't set a session;
    no_claim: none beyond protects."""
    assert _login(client).status_code == 303
    assert client.get("/").status_code == 200


def test_post_without_csrf_is_refused_and_creates_nothing(client):
    """protects: a forged cross-site POST can't create or email anything;
    fails_when: a POST without the session's CSRF token is processed;
    no_claim: none beyond protects."""
    _login(client)
    data = _service_fields() | {"submission_token": str(uuid.uuid4()), "emails": ["a@example.com"]}

    assert client.post("/servicios", data=data).status_code == 403
    assert client.post("/servicios", data=data | {"csrf": "forged"}).status_code == 403
    assert _rows("SELECT id FROM services") == []


# --- creating services ----------------------------------------------------------


def test_class_with_two_emails_runs_every_step_once(client, fake):
    """protects: criterion 7 -- one service, 2 emails, timed event, confirmation and
    invoice to both, next invoice number, no receipt;
    fails_when: a step is skipped, duplicated or sent to only one recipient;
    no_claim: FakeSenders stand in for Google Calendar and Zoho SMTP."""
    _login(client)
    response = _create(client)

    assert response.status_code == 303
    sid = _service_id(response)
    [row] = _rows("SELECT * FROM services WHERE id = %s", (sid,))
    assert row["invoice_number"] == "0000042" and row["receipt_number"] is None
    assert row["calendar_event_id"] == "evt-1"
    assert row["confirmation_sent_at"] and row["invoice_sent_at"] and row["receipt_sent_at"] is None
    assert row["last_error"] is None and row["processing_until"] is None
    assert len(_rows("SELECT * FROM service_emails WHERE service_id = %s", (sid,))) == 2
    [cal] = fake.calendar
    assert type(cal).__name__ == "ClassSubmission"
    assert cal.scheduled_at.strftime("%Y-%m-%d %H:%M %Z") == "2026-10-21 15:30 CST"
    [conf] = fake.confirmations
    assert conf.emails == ["a@example.com", "b@example.com"]
    [(inv, number)] = fake.invoices
    assert number == "0000042" and inv.emails == ["a@example.com", "b@example.com"]


def test_work_with_collaborator_stores_pay_profit_and_receipt_number(client, fake):
    """protects: criterion 8 data -- profit = price - pay, all-day event, receipt number taken;
    fails_when: profit or the receipt number is wrong, or the event is timed;
    no_claim: the receipt email itself is covered in test_receipt_goes_to_the_collaborator."""
    _login(client)
    response = _create(
        client, emails=["a@example.com"], kind="trabajo", scheduled_at="", deadline="2026-10-30",
        price="25000", hire="1", collaborator_id="1", collaborator_pay="15000",
    )  # fmt: skip

    [row] = _rows("SELECT * FROM services WHERE id = %s", (_service_id(response),))
    assert (row["price"], row["collaborator_pay"], row["profit"]) == (25000, 15000, 10000)
    assert row["collaborator_id"] == 1 and row["receipt_number"] == "0000001"
    assert type(fake.calendar[0]).__name__ == "AdvisorySubmission"
    assert fake.calendar[0].deadline == date(2026, 10, 30)


def test_inactive_collaborator_is_refused(client):
    """protects: criterion 8b -- a tampered POST can't hire a deactivated collaborator;
    fails_when: the service is created;
    no_claim: none beyond protects."""
    _login(client)
    response = _create(client, hire="1", collaborator_id="2", collaborator_pay="100")

    assert response.status_code == 400
    assert "desactivado" in response.text
    assert _rows("SELECT id FROM services") == []


def test_invalid_form_returns_400_in_spanish_and_saves_nothing(client):
    """protects: criterion 12 at the HTTP level;
    fails_when: an invalid form is saved or answers 200;
    no_claim: each individual rule is covered in test_service_form.py."""
    _login(client)
    response = _create(client, emails=[], price="0")

    assert response.status_code == 400
    assert "al menos un correo" in response.text and "precio" in response.text
    assert _rows("SELECT id FROM services") == []


def test_double_submit_with_same_token_creates_one_service(client, fake):
    """protects: edge rule 2 -- a double-tapped Confirm sends nothing twice;
    fails_when: a second row or a second email appears;
    no_claim: none beyond protects."""
    _login(client)
    token = str(uuid.uuid4())
    first = _create(client, token=token)
    second = _create(client, token=token)

    assert second.headers["location"] == first.headers["location"]
    assert len(_rows("SELECT id FROM services")) == 1
    assert len(fake.confirmations) == 1


def test_before_cutover_creation_is_refused_with_503(client, fake):
    """protects: edge rule 1 -- no invoice number is handed out before the Notion cutover;
    fails_when: a service is created or emailed without counters.invoice;
    no_claim: none beyond protects."""
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DELETE FROM counters WHERE name = 'invoice'")
    _login(client)

    response = _create(client)

    assert response.status_code == 503
    assert _rows("SELECT id FROM services") == [] and fake.confirmations == []


def test_ten_concurrent_creations_get_ten_consecutive_invoice_numbers(pg):
    """protects: criterion 10 -- the counter row lock serializes numbering;
    fails_when: two services share a number or a number is skipped;
    no_claim: exercises lib.db directly, not HTTP."""
    data = ServiceInput(
        service_date=date(2026, 10, 20), client_name="C", job="J", kind="trabajo",
        scheduled_at=None, deadline=date(2026, 10, 30), price=Decimal(1000), notes=None,
        emails=["a@example.com"],
    )  # fmt: skip
    errors = []

    def worker():
        try:
            with db.connect(pg, autocommit=True) as conn:
                db.create_service(conn, data, uuid.uuid4())
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    numbers = sorted(r["invoice_number"] for r in _rows("SELECT invoice_number FROM services"))
    assert numbers == [f"{n:07d}" for n in range(42, 52)]


# --- failures and retries ------------------------------------------------------------


def test_calendar_failure_keeps_emails_and_retry_only_creates_the_event(client, fake):
    """protects: criterion 9 -- one failing step doesn't block the others, and a retry
    redoes only what's missing;
    fails_when: emails wait on the calendar, or a retry resends them;
    no_claim: FakeSenders stand in for Google and Zoho."""
    _login(client)
    fake.calendar_error = "Google caído"
    sid = _service_id(_create(client))

    [row] = _rows("SELECT * FROM services WHERE id = %s", (sid,))
    assert row["calendar_event_id"] is None and "Google caído" in row["last_error"]
    assert row["confirmation_sent_at"] and row["invoice_sent_at"]
    assert "❌ Calendario" in client.get(f"/servicios/{sid}").text

    fake.calendar_error = None
    csrf = _csrf(client, f"/servicios/{sid}")
    assert client.post(f"/servicios/{sid}/reintentar", data={"csrf": csrf}).status_code == 200

    [row] = _rows("SELECT * FROM services WHERE id = %s", (sid,))
    assert row["calendar_event_id"] == "evt-1" and row["last_error"] is None
    assert len(fake.confirmations) == 1 and len(fake.invoices) == 1


def test_retry_while_another_request_holds_the_lease_is_409(client, fake):
    """protects: edge rule 3 -- two simultaneous retries can't double-send;
    fails_when: the second retry runs the steps;
    no_claim: the lease is taken directly in the database to simulate the race."""
    _login(client)
    fake.calendar_error = "x"
    sid = _service_id(_create(client))
    with db.connect(TEST_DB, autocommit=True) as conn:
        assert db.acquire_lease(conn, sid)

    csrf = _csrf(client, f"/servicios/{sid}")
    response = client.post(f"/servicios/{sid}/reintentar", data={"csrf": csrf})

    assert response.status_code == 409
    assert len(fake.calendar) == 0


def test_partially_refused_recipients_are_recorded_but_step_counts_as_sent(client, fake):
    """protects: edge rule 5 -- partial SMTP refusal is visible without resending;
    fails_when: the refusal is swallowed or the step stays pending;
    no_claim: Zoho's real refusal format is not exercised."""
    _login(client)
    fake.refused = {"b@example.com": (550, b"no such user")}
    sid = _service_id(_create(client))

    [row] = _rows("SELECT * FROM services WHERE id = %s", (sid,))
    assert row["confirmation_sent_at"] is not None
    assert "b@example.com" in row["last_error"]


# --- edit and delete -----------------------------------------------------------------


def test_edit_never_resends_or_renumbers(client, fake):
    """protects: criterion 11 -- editing only changes data;
    fails_when: an email is sent or the invoice number changes;
    no_claim: none beyond protects."""
    _login(client)
    sid = _service_id(_create(client))
    data = _service_fields(client_name="Cliente B", price="18000", scheduled_at="2026-10-22T10:00")
    data |= {"csrf": _csrf(client, f"/servicios/{sid}/editar"), "emails": ["c@example.com"]}

    response = client.post(f"/servicios/{sid}/editar", data=data, follow_redirects=False)

    assert response.headers["location"].endswith("?aviso=calendario")
    [row] = _rows("SELECT * FROM services WHERE id = %s", (sid,))
    assert (row["client_name"], row["price"], row["invoice_number"]) == ("Cliente B", 18000, "0000042")
    assert [r["email"] for r in _rows("SELECT email FROM service_emails")] == ["c@example.com"]
    assert len(fake.confirmations) == 1 and len(fake.invoices) == 1


def test_collaborator_cannot_change_after_receipt_was_sent(client, fake):
    """protects: edge rule 6 -- a sent receipt's collaborator and amount are frozen;
    fails_when: the edit is saved;
    no_claim: none beyond protects."""
    _login(client)
    hired = dict(hire="1", collaborator_id="1", collaborator_pay="5000")
    sid = _service_id(_create(client, **hired))
    assert len(fake.receipts) == 1
    data = _service_fields(**(hired | {"collaborator_pay": "6000"}))
    data |= {"csrf": _csrf(client, f"/servicios/{sid}/editar"), "emails": ["a@example.com"]}

    response = client.post(f"/servicios/{sid}/editar", data=data)

    assert response.status_code == 400 and "recibo" in response.text
    assert _rows("SELECT collaborator_pay FROM services")[0]["collaborator_pay"] == 5000


def test_delete_hides_the_service_and_its_number_is_not_reused(client):
    """protects: criterion 11 -- a deleted service leaves lists and totals, and its
    invoice number stays burned;
    fails_when: it still shows, or the next service reuses 0000042;
    no_claim: the CSV export (part 5) is not covered yet."""
    _login(client)
    sid = _service_id(_create(client))
    csrf = _csrf(client, f"/servicios/{sid}/borrar")
    client.post(f"/servicios/{sid}/borrar", data={"csrf": csrf})

    assert client.get(f"/servicios/{sid}").status_code == 404
    assert "Cliente A" not in client.get("/servicios?anio=2026&mes=10").text
    nxt = _service_id(_create(client, client_name="Cliente Z"))
    assert _rows("SELECT invoice_number FROM services WHERE id = %s", (nxt,))[0]["invoice_number"] == "0000043"


def test_month_page_lists_services_and_totals(client):
    """protects: the home/month page shows each service and the month's sums;
    fails_when: a service or the totals are missing;
    no_claim: layout and styling are not checked."""
    _login(client)
    _create(client, price="15000")
    _create(client, client_name="Cliente B", price="25000", hire="1", collaborator_id="1",
            collaborator_pay="5000")  # fmt: skip

    html = client.get("/servicios?anio=2026&mes=10").text

    assert "Cliente A" in html and "Cliente B" in html
    assert "₡40.000" in html and "₡35.000" in html


# --- collaborator receipts (part 4) ---------------------------------------------------


def test_receipt_goes_to_the_collaborator_without_the_client_price(client, fake):
    """protects: criterion 8 -- the hired collaborator gets a receipt with its number,
    client, job, date and their amount, never what the client paid;
    fails_when: the receipt is missing, misaddressed, or shows the client's price;
    no_claim: FakeSenders stand in for WeasyPrint and Zoho."""
    _login(client)
    sid = _service_id(_create(
        client, emails=["a@example.com"], kind="trabajo", scheduled_at="", deadline="2026-10-30",
        price="25000", hire="1", collaborator_id="1", collaborator_pay="15000",
    ))  # fmt: skip

    [receipt] = fake.receipts
    assert (receipt["name"], receipt["email"], receipt["number"]) == (
        "Colaborador Uno", "uno@example.com", "0000001",
    )
    assert receipt["details"] == [
        ("Cliente", "Cliente A"), ("Trabajo", "Cálculo"), ("Entrega", "30/10/2026"), ("Monto", "₡15.000"),
    ]  # fmt: skip
    [pdf] = fake.receipt_pdfs
    assert pdf["amount"] == 15000 and "25" not in pdf["description"]
    [row] = _rows("SELECT * FROM services WHERE id = %s", (sid,))
    assert row["receipt_sent_at"] is not None
    assert (row["receipt_to_name"], row["receipt_to_email"]) == ("Colaborador Uno", "uno@example.com")
    assert "✅ Recibo a Colaborador Uno" in client.get(f"/servicios/{sid}").text


def test_no_receipt_without_a_hired_collaborator(client, fake):
    """protects: receipts only go out when "Contratar colaborador" was checked;
    fails_when: a receipt is sent for a solo service;
    no_claim: none beyond protects."""
    _login(client)
    _create(client)

    assert fake.receipts == []


def test_failed_receipt_is_retried_alone(client, fake):
    """protects: a failed receipt shows ❌ and a retry sends only the receipt;
    fails_when: the client gets a second confirmation or invoice;
    no_claim: none beyond protects."""
    _login(client)
    fake.receipt_error = "Zoho caído"
    sid = _service_id(_create(client, hire="1", collaborator_id="1", collaborator_pay="5000"))
    assert "Recibo: Zoho caído" in _rows("SELECT last_error FROM services")[0]["last_error"]

    fake.receipt_error = None
    client.post(f"/servicios/{sid}/reintentar", data={"csrf": _csrf(client, f"/servicios/{sid}")})

    assert len(fake.receipts) == 1
    assert len(fake.confirmations) == 1 and len(fake.invoices) == 1
    assert _rows("SELECT last_error FROM services")[0]["last_error"] is None


def test_add_collaborator_appears_in_the_service_form(client):
    """protects: criterion 8b -- "+ Agregar colaborador" makes them hireable right away;
    fails_when: the new collaborator isn't offered in the form;
    no_claim: none beyond protects."""
    _login(client)
    response = client.post(
        "/colaboradores/nuevo",
        data={"csrf": _csrf(client, "/colaboradores/nuevo"), "name": "Nueva Persona", "email": "Nueva@Example.com"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _rows("SELECT email FROM collaborators WHERE name = 'Nueva Persona'")[0]["email"] == "nueva@example.com"
    assert "Nueva Persona" in client.get("/servicios/nuevo").text


@pytest.mark.parametrize(
    "name, email, message",
    [("Otro", "uno@example.com", "Ya hay un colaborador"), ("Otro", "no-es-correo", "no es válido"),
     ("", "x@example.com", "Falta el nombre")],
)  # fmt: skip
def test_invalid_collaborator_is_refused(client, name, email, message):
    """protects: criterion 8b -- duplicate or invalid emails answer 400 in Spanish;
    fails_when: the collaborator is saved;
    no_claim: none beyond protects."""
    _login(client)
    response = client.post(
        "/colaboradores/nuevo",
        data={"csrf": _csrf(client, "/colaboradores/nuevo"), "name": name, "email": email},
    )

    assert response.status_code == 400 and message in response.text
    assert len(_rows("SELECT id FROM collaborators")) == 2


def test_deactivated_collaborator_leaves_the_form_but_keeps_history(client):
    """protects: criterion 8b -- deactivating hides them from new services only;
    fails_when: they stay hireable, or their past service loses the name;
    no_claim: none beyond protects."""
    _login(client)
    sid = _service_id(_create(client, hire="1", collaborator_id="1", collaborator_pay="5000"))

    client.post("/colaboradores/1/desactivar", data={"csrf": _csrf(client, "/colaboradores")})

    new_form = client.get("/servicios/nuevo").text
    assert "Colaborador Uno" not in new_form and "No hay colaboradores activos" in new_form
    assert "Colaborador Uno" in client.get(f"/servicios/{sid}").text
    assert "inactivo" in client.get("/colaboradores").text


def test_editing_a_collaborator_keeps_whom_a_sent_receipt_was_issued_to(client):
    """protects: edge rule 7 -- a sent receipt keeps the original name and email;
    fails_when: renaming the collaborator rewrites the past receipt;
    no_claim: none beyond protects."""
    _login(client)
    sid = _service_id(_create(client, hire="1", collaborator_id="1", collaborator_pay="5000"))

    client.post(
        "/colaboradores/1/editar",
        data={"csrf": _csrf(client, "/colaboradores/1/editar"), "name": "Nombre Nuevo", "email": "nuevo@example.com"},
    )

    [row] = _rows("SELECT receipt_to_name, receipt_to_email FROM services WHERE id = %s", (sid,))
    assert (row["receipt_to_name"], row["receipt_to_email"]) == ("Colaborador Uno", "uno@example.com")
    assert "Recibo a Colaborador Uno" in client.get(f"/servicios/{sid}").text


# --- summary and export (part 5) -------------------------------------------------------


@pytest.mark.parametrize("path", ["/resumen", "/exportar.csv"])
def test_summary_and_export_require_login(client, path):
    """protects: earnings data is never public;
    fails_when: either page answers without a session;
    no_claim: none beyond protects."""
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 302 and response.headers["location"] == "/login"


def test_summary_and_export_reflect_services_and_skip_deleted(client):
    """protects: criteria 11 and 13 -- the summary and CSV come from the database and
    leave deleted services out;
    fails_when: a deleted service is counted or exported;
    no_claim: the real workbook comparison lives in tests/test_ledger.py."""
    _login(client)
    _create(client, price="15000")
    _create(client, client_name="Cliente B", price="25000", hire="1", collaborator_id="1",
            collaborator_pay="5000")  # fmt: skip
    gone = _service_id(_create(client, client_name="Cliente Borrado", price="99000"))
    client.post(f"/servicios/{gone}/borrar", data={"csrf": _csrf(client, f"/servicios/{gone}/borrar")})

    html = client.get("/resumen?anio=2026").text
    assert "₡40.000" in html and "₡35.000" in html and "Octubre" in html

    response = client.get("/exportar.csv?anio=2026")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="Ganancias_2026_completo.csv"'
    text = response.content.decode("utf-8")
    assert "Cliente Borrado" not in text
    sheets = {line.split(",", 1)[0] for line in text.lstrip("\ufeff").splitlines()}
    assert "II Cuatrimestre" not in sheets and "III Cuatrimestre,,,Total,40000,35000" in text
