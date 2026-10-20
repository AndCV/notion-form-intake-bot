# Notion Form Intake Bot

A Python service that runs on a schedule (no persistent server) and, on
every run:

1. Queries a Notion database for new or unprocessed rows.
2. For each row: if its `Type` property is a scheduled-session type, creates
   a timed Google Calendar event; if it's a deadline type, creates an
   all-day event on that date.
3. Sends a confirmation email (with an `.ics` attachment).
4. Marks the row as confirmed back in Notion.
5. If the row has a price set, generates an invoice PDF and emails it, then
   marks the row as invoiced (see "Invoicing" below).

The form itself lives in an external form builder that already writes
directly into a Notion database (that connection is the form builder's own
Notion integration). This repo never talks to the form builder — it only
reads and updates Notion by polling.

## Why polling instead of a webhook

Some form builders' free tiers don't include outgoing webhooks, but do offer
a native "save responses to Notion" integration. Instead of waiting for a
push notification, this service polls the shared Notion database every few
minutes via cron.

If your form builder does support webhooks, you don't need this pattern at
all — a webhook handler is simpler and near-instant. This repo is for the
case where polling is the only option.

## Structure

```
scripts/poll_notion.py             # entry point: runs one full polling cycle
scripts/generate_sample_invoice.py # generates one sample invoice PDF locally
lib/config.py                      # loads and validates environment variables
lib/schemas.py                     # parses a Notion row (page) into a typed object
lib/notion_client.py               # queries pending rows + marks them as processed
lib/calendar_client.py             # creates events in Google Calendar
lib/ics.py                         # builds the .ics attachment
lib/email_client.py                # sends email via Zoho Mail SMTP (copy is easy to edit)
lib/invoicing.py                   # renders templates/invoice.html to a PDF
templates/invoice.html             # invoice layout (HTML/CSS + inline SVG)
.github/workflows/poll.yml         # cron every 15 minutes
tests/                             # pytest, mocks every external API
```

## Key decisions

- **Scheduling: GitHub Actions**, not a hosted cron service or a platform's
  built-in cron. The workflow runs `scripts/poll_notion.py` directly — there's
  no web service to deploy. Some platforms' free tiers restrict cron jobs to
  once a day, which isn't enough for "every few minutes" without a paid plan.
  On a **private** repo, a 15-minute interval comfortably fits GitHub
  Actions' 2000 free minutes/month; on a public repo, minutes are unlimited,
  so you can drop to 5 minutes. Known trade-off: GitHub disables scheduled
  workflows after 60 days without a commit to the repo, and can run them a
  bit late during peak load — acceptable for this kind of use case.
- **Google Calendar via a service account**, not OAuth: unattended
  automation without having to keep a refresh token alive. You share your
  calendar with the service account's email, granting "make changes to
  events". Trade-off: **no attendees are added to events** — service
  accounts without Domain-Wide Delegation can't invite attendees on
  calendars they don't own; the client's confirmation goes out by email with
  the `.ics` attached instead.
- **Idempotency without a submission-id column**: since Notion is read
  directly (not an external payload), the `page_id` each query already
  returns works as the unique key. Each step is tracked separately
  (`Calendar Event ID`, `Email Sent`), so if a run fails halfway through, the
  next run only retries what's missing — nothing gets duplicated.
- **Incomplete rows don't break the run**: if a row is still missing
  `Name`/`Email`/`Type`/`Date` (e.g. while the form builder is still writing
  it, or a manually created test row), it's logged as an invalid row and the
  run continues with the rest. That row will keep showing up on every run
  until the fields are filled in or the row is deleted — expected behavior,
  not a bug.
- **Zoho Mail instead of a transactional email provider**: sending through a
  provider like Resend/SendGrid usually requires a verified custom domain to
  send to arbitrary recipients — fine for many setups, but not always
  available. A personal Zoho Mail account (`@zohomail.com`, via SMTP over
  SSL + an App Password) sends to any recipient without a domain, for free.
  Trade-offs: the sender looks like a personal address rather than a branded
  one, and free accounts have a daily sending cap (far above what a small
  intake form needs). If you later get a verified domain, swap providers by
  editing only `lib/email_client.py` — nothing else depends on it.
- **Invoice numbers come from Notion itself, not a separate counter**: the
  next number is "the highest `Invoice Number` already in the database,
  plus one", queried fresh every time a row is actually invoiced (not at
  row-creation time). That keeps it gapless — a row that's deleted before
  ever being invoiced (a duplicate, a typo) never consumes a number — without
  needing a second database to hold a counter. This assumes a single worker
  processes rows sequentially, one at a time; the GitHub Actions workflow
  has a `concurrency` guard so two runs can never overlap and race on the
  same "next number".
- **HTML/CSS + WeasyPrint for the invoice, not LaTeX**: the layout
  (`templates/invoice.html`) is a from-scratch reproduction of a reference
  `.tex` design, using inline SVG for the geometric background decoration
  since that renders reliably in WeasyPrint. It won't be pixel-identical to
  the original, but it's close.
- **Invoicing is optional per row**: a row only gets invoiced once it has a
  price (`Precio`) set. Rows without a price just skip that step — not every
  request necessarily needs to be billed.

This repo also currently powers a real deployment for a Spanish-speaking
solo consulting business. A few literal values reflect that live setup and
are meant to be adjusted for your own case:
- The confirmation email copy (`lib/email_client.py`) is in Spanish.
- The Notion `Type` select options are matched by substring (`"asesor"` /
  `"clase"` in `lib/schemas.py`) against Spanish option values. If your
  Notion select uses different option values, update that matching logic.
- The Calendar event titles/descriptions are in Spanish.
- The default `TIMEZONE` in `.env.example` is `America/Costa_Rica`.

## Notion database schema

Example property names used by `lib/schemas.py` (adjust `PROP_*` constants
there if your database uses different ones):

| Property             | Type         | Notes                                          |
|------------------------|--------------|---------------------------------------------------|
| Nombre (Name)           | title        |                                                     |
| Tipo (Type)              | select       | two option values, matched by substring            |
| Correo (Email)            | email        |                                                     |
| `Fecha ` (Date)            | date         | deadline (advisory type) or date/time (class type) |
| Estado (Status)              | select       | e.g. `nuevo` (new) / `confirmado` (confirmed)      |
| Calendar Event ID              | rich text    | written by this service, don't edit by hand        |
| Email Enviado (Email Sent)        | checkbox     | written by this service                             |
| Precio (Price)                       | number       | optional; a row is only invoiced if this is set     |
| Detalle (Detail)                       | rich text    | optional; shown as the invoice line item description |
| Número de Factura (Invoice Number)       | rich text    | written by this service — also the source of truth for the next number, don't edit by hand |
| Factura Enviada (Invoice Sent)             | checkbox     | written by this service                             |
| Creado (Created)                             | created time |                                                       |

**Important**: whatever form builder writes to your database, make sure it
targets *this* database (the one with the columns above), not a separate
auto-generated one some form builders create by default when you first
connect a Notion integration.

## Finding the Notion data source id

This project targets the current Notion API, where a database's properties
and rows live in a **data source** (not directly on the "database"
container). To find the id, with `NOTION_TOKEN` already set in your
environment:

```bash
python3 - <<'EOF'
import os
from notion_client import Client

client = Client(auth=os.environ["NOTION_TOKEN"])
results = client.search(
    query="<your database name>",
    filter={"property": "object", "value": "data_source"},
)["results"]
for r in results:
    title = "".join(t["plain_text"] for t in r["title"])
    print(title, "->", r["id"])
EOF
```

That id is `NOTION_DATA_SOURCE_ID`.

## Setting up credentials

### Notion

1. Create an internal integration at
   [notion.so/my-integrations](https://www.notion.so/my-integrations), copy
   its secret as `NOTION_TOKEN`.
2. Share your target database with that integration (`...` menu on the
   database → `Connections` → add the integration).
3. Get `NOTION_DATA_SOURCE_ID` with the script above.

### Google Calendar (service account)

1. In [Google Cloud Console](https://console.cloud.google.com/), enable the
   **Google Calendar API** on a project.
2. `IAM & Admin` → `Service Accounts` → create one (no project roles
   needed).
3. On that service account → `Keys` → `Add key` → JSON. Download the file.
4. Minify that JSON to a single line (or base64-encode it) and paste it as
   `GOOGLE_SERVICE_ACCOUNT_JSON`. `lib/config.py` accepts either format.
5. In Google Calendar, share the calendar you want events created on with
   the service account's email (`client_email` in the JSON, ends in
   `...iam.gserviceaccount.com`), granting "Make changes to events".
6. `GOOGLE_CALENDAR_ID` is that calendar's id — for your primary calendar
   it's just your email address.

### Zoho Mail (sending email)

1. On the Zoho Mail account that will send the emails, open your Zoho
   account's security settings and create an App Password (any name works).
   Zoho shows the code once.
2. That code goes in `ZOHO_APP_PASSWORD`. `ZOHO_ADDRESS` is the full Zoho
   address (e.g. `you@zohomail.com`).
3. The service connects to `smtp.zoho.com:465` over SSL — the server for
   personal `@zohomail.com` and free-plan accounts. Paid organization
   accounts on a custom domain use `smtppro.zoho.com` instead: change
   `_SMTP_HOST` in `lib/email_client.py`.
4. No sandbox mode, no recipient restriction — you can send to anyone from
   the first send.

The email copy lives in `lib/email_client.py`, in the `_advisory_email` and
`_class_email` functions — those are the only two places to edit if you want
to change the wording.

## Invoicing

Once a row has a `Precio` set, the next run generates a one-line-item
invoice PDF (name, detail, price, issue date, a 7-digit consecutive number)
and emails it via Zoho Mail, then marks the row `Factura Enviada`.

The letterhead (issuer name, tagline, contact info, payment method,
currency symbol) comes entirely from the `INVOICE_*` environment variables
— see `.env.example`. None of it is hardcoded in the template, so the same
`templates/invoice.html` works for any business.

To tweak the design, edit `templates/invoice.html` (plain HTML/CSS, plus an
inline `<svg>` for the corner/footer decoration) and regenerate the sample:

```bash
python scripts/generate_sample_invoice.py   # writes sample_invoice.pdf, no external calls
```

Open `sample_invoice.pdf` after every change to check it visually — it uses
the same `INVOICE_*` variables from your `.env`, with made-up client/price
data, and never touches Notion, Calendar or Zoho Mail.

### WeasyPrint system dependencies (local setup)

WeasyPrint renders HTML/CSS to PDF using Pango and cairo, which are system
libraries, not Python packages — `pip install` alone isn't enough.

**macOS:**
```bash
brew install pango cairo gdk-pixbuf libffi
# Homebrew's libs aren't on the default library search path; export this
# in any shell where you run pytest, poll_notion.py or generate_sample_invoice.py:
export DYLD_LIBRARY_PATH="$(brew --prefix)/lib:$DYLD_LIBRARY_PATH"
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt-get install -y libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
  libgdk-pixbuf2.0-0 libffi-dev shared-mime-info fonts-liberation
```

GitHub Actions already does the Linux install automatically (see
`.github/workflows/poll.yml`) — this section is only for running things on
your own machine.

## Testing locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# fill in .env with real credentials

# macOS only, see "WeasyPrint system dependencies" above:
export DYLD_LIBRARY_PATH="$(brew --prefix)/lib:$DYLD_LIBRARY_PATH"

pytest              # runs the full suite (mocks every external API)

python scripts/poll_notion.py   # runs one real cycle against Notion/Calendar/Zoho Mail
```

## Scheduling the run (GitHub Actions)

1. In the GitHub repo: `Settings` → `Secrets and variables` → `Actions` →
   add as secrets: `NOTION_TOKEN`, `NOTION_DATA_SOURCE_ID`, `ZOHO_ADDRESS`,
   `ZOHO_APP_PASSWORD`, `GOOGLE_SERVICE_ACCOUNT_JSON`,
   `GOOGLE_CALENDAR_ID`, `BUSINESS_NAME`, `TIMEZONE`, `INVOICE_ISSUER_NAME`,
   `INVOICE_TAGLINE`, `INVOICE_PHONE`, `INVOICE_EMAIL`, `INVOICE_ADDRESS`,
   `INVOICE_WEBSITE`, `INVOICE_PAYMENT_METHOD`, `INVOICE_CURRENCY_SYMBOL`.
2. `.github/workflows/poll.yml` is already set up to run every 15 minutes
   (`workflow_dispatch` also lets you trigger it manually from the Actions
   tab to test).
3. Push the repo to GitHub — the cron starts on its own from there, no
   separate "deploy" step needed.

## Deploying

There's nothing to deploy: GitHub Actions **is** the runtime. Just push the
repo with the workflow and secrets configured (previous section).
