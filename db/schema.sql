-- Schema for the services ledger (see issue #1). Idempotent: every statement
-- is IF NOT EXISTS, so scripts/migrate.py can run it on every deploy.

CREATE TABLE IF NOT EXISTS collaborators (
  id         SERIAL PRIMARY KEY,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL UNIQUE,
  -- Deactivate instead of deleting: past receipts keep pointing here.
  active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS services (
  id                   SERIAL PRIMARY KEY,
  -- "Fecha" column of the workbook; decides the month and the cuatrimestre.
  service_date         DATE NOT NULL,
  client_name          TEXT NOT NULL,
  -- "Trabajo" column of the workbook.
  job                  TEXT NOT NULL,
  -- NULL only on imported rows: the workbook never recorded it.
  kind                 TEXT CHECK (kind IN ('clase', 'trabajo')),
  scheduled_at         TIMESTAMPTZ,
  deadline             DATE,
  price                NUMERIC(12, 2) NOT NULL CHECK (price > 0),
  -- NULL = no collaborator hired.
  collaborator_id      INT REFERENCES collaborators (id),
  collaborator_pay     NUMERIC(12, 2) NOT NULL DEFAULT 0
                       CHECK (collaborator_pay >= 0 AND collaborator_pay <= price),
  profit               NUMERIC(12, 2) GENERATED ALWAYS AS (price - collaborator_pay) STORED,
  -- Invoice line-item text, or the free-text note column on imported rows.
  notes                TEXT,
  invoice_number       TEXT UNIQUE,
  receipt_number       TEXT UNIQUE,
  calendar_event_id    TEXT,
  confirmation_sent_at TIMESTAMPTZ,
  invoice_sent_at      TIMESTAMPTZ,
  receipt_sent_at      TIMESTAMPTZ,
  -- Snapshot of who the receipt was issued to, so editing or deactivating
  -- the collaborator later doesn't rewrite history.
  receipt_to_name      TEXT,
  receipt_to_email     TEXT,
  -- Guards against a double-submitted form creating two services.
  submission_token     UUID UNIQUE,
  last_error           TEXT,
  source               TEXT NOT NULL CHECK (source IN ('app', 'import')),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at           TIMESTAMPTZ,
  -- In the app, paying someone requires picking who. Imported rows carry the
  -- pay (Precio - Ganancia) but the workbook never said to whom.
  CHECK (collaborator_id IS NOT NULL OR collaborator_pay = 0 OR source = 'import'),
  CHECK (source = 'import' OR kind IS NOT NULL),
  CHECK (kind IS DISTINCT FROM 'clase' OR scheduled_at IS NOT NULL),
  CHECK (kind IS DISTINCT FROM 'trabajo' OR deadline IS NOT NULL)
);

-- Lease that keeps two requests (a double-tapped "Reintentar", or a retry
-- racing the first run) from sending the same emails twice. A lease rather
-- than SELECT ... FOR UPDATE because Neon's pooled connections don't keep a
-- row lock alive across the separate commits each step makes.
ALTER TABLE services ADD COLUMN IF NOT EXISTS processing_until TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS services_date_idx ON services (service_date) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS service_emails (
  service_id INT NOT NULL REFERENCES services (id) ON DELETE CASCADE,
  email      TEXT NOT NULL,
  PRIMARY KEY (service_id, email)
);

-- Gapless numbering: a number is taken with
--   UPDATE counters SET last_value = last_value + 1 WHERE name = ... RETURNING last_value
-- inside the same transaction that inserts the service, so the row lock
-- serializes concurrent submissions and a rolled-back insert burns nothing.
CREATE TABLE IF NOT EXISTS counters (
  name       TEXT PRIMARY KEY CHECK (name IN ('invoice', 'receipt')),
  last_value INT NOT NULL CHECK (last_value >= 0)
);

CREATE TABLE IF NOT EXISTS login_attempts (
  ip      TEXT NOT NULL,
  at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  success BOOLEAN NOT NULL
);
