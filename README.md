# Services web app

A small web app for a solo tutoring/consulting business. The owner registers
each service from a phone, and the app:

1. Creates the Google Calendar event: a timed one for a class, an all-day one
   on the delivery date for a work.
2. Emails the client (one or more addresses) a confirmation with an `.ics`
   attached.
3. Emails the client an invoice PDF with a gapless, 7-digit number.
4. If a collaborator was hired for the service, emails them a payment
   receipt PDF with their amount (never the client's price).
5. Records the service in the earnings ledger: price, collaborator pay and
   profit, with monthly / per-cuatrimestre summaries and a CSV export in the
   owner's original workbook layout.

It replaced an earlier setup (an external form → Notion → a GitHub Actions
poller) and a hand-kept Excel workbook; see issue #1 for the full spec and
the cutover.

## Architecture

```
Phone ──HTTPS──> Render (free web service, Docker: FastAPI + Jinja2)
                    ├── Neon Postgres (free): services, emails, collaborators, counters
                    ├── Google Calendar API (service account)
                    └── Zoho Mail REST API (HTTPS, OAuth)
                           ├── client(s): confirmation + .ics, invoice PDF
                           └── collaborator: payment receipt PDF

GitHub Actions: tests on every push (Render deploys only when they pass)
                + a ping every 10 minutes so the free instance doesn't sleep
```

## Structure

```
app/main.py                  # the web app: login, services, collaborators, summary, export
lib/db.py                    # Postgres access (one function per operation)
lib/pipeline.py              # runs a service's outgoing steps, idempotently
lib/service_form.py          # form validation (Spanish error messages)
lib/schemas.py               # adapts a service row for calendar/.ics/email/invoice code
lib/calendar_client.py       # Google Calendar events
lib/ics.py                   # the .ics attachment
lib/email_client.py          # email copy + transport choice (Zoho API or SMTP)
lib/zoho_api.py              # Zoho Mail REST API client (stdlib only)
lib/invoicing.py             # invoice and receipt PDFs (WeasyPrint)
lib/ledger.py                # earnings summary + CSV export
lib/workbook_csv.py          # reads the old workbook's CSV export
lib/auth.py                  # scrypt password hashing
db/schema.sql                # schema (idempotent; applied on every start)
templates/invoice.html       # invoice/receipt layout (shared letterhead)
templates/app/               # web app pages
scripts/migrate.py           # apply schema; seed a collaborator or the invoice counter
scripts/import_2026.py       # one-time import of the 2026 workbook
scripts/hash_password.py     # turns APP_PASSWORD into APP_PASSWORD_HASH in .env
scripts/generate_sample_invoice.py  # sample invoice PDF, no external calls
Dockerfile                   # the image Render runs
.github/workflows/tests.yml  # pytest + a disposable Postgres
.github/workflows/keepalive.yml     # pings /healthz every 10 minutes
```

## Key decisions

- **Render free tier, not Cloud Run.** No credit card. Trade-offs: the free
  instance sleeps after 15 minutes idle (the keep-alive ping covers this, at
  ~744 of the 750 free hours a month), and **outbound SMTP is blocked**,
  hence the next point.
- **Zoho Mail over its HTTPS API.** A Zoho "Self Client" with a long-lived
  refresh token. Each attachment is uploaded, then referenced from the send
  call. SMTP (`smtp.zoho.com:465` + App Password) remains as a fallback when
  the API settings are absent, e.g. on a host that allows SMTP.
- **Gapless numbering from a counter row.** The invoice (and receipt) number
  is taken with `UPDATE counters ... RETURNING` in the same transaction that
  inserts the service: the row lock serializes concurrent submissions, and a
  rolled-back insert burns no number. Deleting a service never frees its
  number. The invoice counter was seeded once at 72, the last number Notion
  issued, so numbering continued without a gap.
- **Idempotent steps.** Calendar, confirmation, invoice and receipt each
  stamp a marker as soon as they succeed. A failed step shows ❌ on the
  service page, and **Reintentar** redoes only what's missing. A lease column
  (`processing_until`) keeps two retries from running at once.
  Accepted gap: if the process dies after an email left but before its
  marker was stamped, a retry sends that one email again.
- **Editing never resends.** Edits change data only. Moving a class or a
  delivery date doesn't move the calendar event (the page says so). A
  collaborator's pay is frozen once their receipt went out.
- **Collaborators are a list**, managed from the app. Receipts snapshot the
  name and email they were issued to.
- **The ledger replaces the workbook.** `/resumen` rebuilds the workbook's
  summary sheets (same class/tutoring rule as its `COUNTIF`). `/exportar.csv`
  writes the workbook's layout. Both were checked against the real 2026
  workbook (`LEDGER_CSV` test, local only).
- **The repo is public; the earnings data isn't.** `*.csv` / `*.xlsx` are
  gitignored, and tests use synthetic data. The real-workbook tests run only
  when `LEDGER_CSV` points at a local file.

## Setup

Copy `.env.example` to `.env` and fill it in. Everything below goes there,
and the same variables go in Render's environment (never in the repo).

### Neon (database)

Create a free project in the US East region and copy its connection string
into `DATABASE_URL`. Then:

```bash
python scripts/migrate.py                                   # schema (also runs on every app start)
python scripts/migrate.py --seed-invoice-counter <last invoice number already issued>
python scripts/migrate.py --seed-collaborator "Name" name@example.com   # optional
```

### Zoho Mail (sending email)

1. At [api-console.zoho.com](https://api-console.zoho.com), create a **Self
   Client**. Its *Client Secret* tab gives `ZOHO_CLIENT_ID` and
   `ZOHO_CLIENT_SECRET`.
2. Under *Generate Code*, use scope `ZohoMail.messages.ALL,ZohoMail.accounts.READ`
   and a 10-minute duration. Exchange the code within 10 minutes for a
   refresh token (`POST https://accounts.zoho.com/oauth/v2/token` with
   `grant_type=authorization_code`) → `ZOHO_REFRESH_TOKEN`.
3. `GET https://mail.zoho.com/api/accounts` (header `Authorization:
   Zoho-oauthtoken <access token>`) → `accountId` → `ZOHO_ACCOUNT_ID`.
4. `ZOHO_ADDRESS` is the sending address. Personal `@zohomail.com` accounts
   are in the US data center. Others can set `ZOHO_ACCOUNTS_URL` /
   `ZOHO_MAIL_API_URL` (e.g. `https://accounts.zoho.eu`).

The email copy lives in `lib/email_client.py` (`_advisory_email`,
`_class_email`, and the invoice/receipt functions below them).

### Google Calendar (service account)

1. In Google Cloud Console, enable the **Google Calendar API**, create a
   service account (no project roles), and add a JSON key.
2. Paste the JSON, on one line or base64-encoded, as
   `GOOGLE_SERVICE_ACCOUNT_JSON`.
3. Share your calendar with the service account's `client_email`, with
   "Make changes to events". Then set `GOOGLE_CALENDAR_ID` (for a primary
   calendar, it's your email).

No attendees are added to events: service accounts without domain-wide
delegation can't invite attendees. The client gets the `.ics` by email
instead.

### Login

Put `APP_PASSWORD=<at least 10 characters>` in `.env` and run
`python scripts/hash_password.py`. It replaces that line with
`APP_PASSWORD_HASH` and adds a random `SESSION_SECRET`, so the plain password
never stays on disk. Login locks for 15 minutes after 5 failures (globally,
not per IP).

### Invoice letterhead

The `INVOICE_*` variables set the issuer name, tagline, contact details,
payment method and currency symbol. The issuer name and tagline also brand
the web app. To tweak the design, edit `templates/invoice.html` and run
`python scripts/generate_sample_invoice.py`.

## Running locally

WeasyPrint needs system libraries:

```bash
brew install pango cairo gdk-pixbuf libffi                    # macOS
export DYLD_LIBRARY_PATH="$(brew --prefix)/lib:$DYLD_LIBRARY_PATH"
# Debian/Ubuntu: see the apt-get line in the Dockerfile
```

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
COOKIE_SECURE=false uvicorn app.main:app --port 8010   # http://127.0.0.1:8010
```

## Tests

```bash
docker run -d --name forms-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=forms_test \
    -p 127.0.0.1:55432:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/forms_test pytest
# plus, with the private workbook export available locally:
LEDGER_CSV=~/Downloads/Ganancias_2026_completo.csv pytest tests/test_ledger.py
```

`tests/test_app.py` drives the real app against that disposable database,
with Google and Zoho faked. CI runs the same suite (`tests.yml`).

## Deploying

A push to `main` deploys to Render once the **Tests** workflow passes (its `deploy` job calls the Render API with the `RENDER_API_KEY` secret and the `RENDER_SERVICE_ID` variable, then waits until the deploy is live). The
service, `acv-servicios`, is a Docker web service on the free plan in Ohio,
with health check `/healthz`. Its environment variables are set in the
Render dashboard: the `.env` keys listed above plus `COOKIE_SECURE=true`.
The schema is applied on every start, so a deploy needs no manual step.

Rollback: in Render, *Manual Deploy → Rollback* to a previous deploy, or
`git revert` and push.
