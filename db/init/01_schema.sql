-- Collections agent schema
-- Runs automatically the first time the Postgres container starts.

CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector: adds the VECTOR type

-- Businesses Meera sells to
CREATE TABLE buyers (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    contact_name  TEXT,
    phone         TEXT,
    email         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Bills Meera sends. Money is NUMERIC, never FLOAT (floats can't store 0.10 exactly).
CREATE TABLE invoices (
    id          SERIAL PRIMARY KEY,
    buyer_id    INT  NOT NULL REFERENCES buyers(id) ON DELETE CASCADE,
    invoice_no  TEXT NOT NULL UNIQUE,
    issue_date  DATE NOT NULL,
    due_date    DATE NOT NULL,
    amount      NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    CHECK (due_date >= issue_date)
);
CREATE INDEX idx_invoices_buyer ON invoices(buyer_id);
CREATE INDEX idx_invoices_due   ON invoices(due_date);

-- Money received. One invoice can have many (partial) payments.
CREATE TABLE payments (
    id          SERIAL PRIMARY KEY,
    invoice_id  INT  NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    paid_on     DATE NOT NULL,
    amount      NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    reference   TEXT                      -- e.g. UPI / NEFT reference
);
CREATE INDEX idx_payments_invoice ON payments(invoice_id);

-- Contract text split into chunks, each with its embedding.
-- 384 = output size of the small local embedding model we'll use.
CREATE TABLE contract_chunks (
    id           SERIAL PRIMARY KEY,
    buyer_id     INT  NOT NULL REFERENCES buyers(id) ON DELETE CASCADE,
    source_file  TEXT NOT NULL,
    chunk_index  INT  NOT NULL,
    content      TEXT NOT NULL,
    embedding    VECTOR(384) NOT NULL,
    UNIQUE (source_file, chunk_index)     -- re-running ingestion can't duplicate chunks
);
CREATE INDEX idx_chunks_buyer ON contract_chunks(buyer_id);
-- No vector (HNSW) index: a few hundred rows are searched exactly in milliseconds.

-- Reminder drafts waiting for Meera's decision
CREATE TABLE reminders (
    id          SERIAL PRIMARY KEY,
    invoice_id  INT  NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    tone        TEXT NOT NULL CHECK (tone IN ('gentle', 'firm', 'final')),
    draft_text  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at  TIMESTAMPTZ
);
-- At most ONE pending draft per invoice, so a retried request can't create duplicates
CREATE UNIQUE INDEX uq_one_pending_reminder
    ON reminders(invoice_id) WHERE status = 'pending';

-- Append-only record of everything the agent and the owner did
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor       TEXT  NOT NULL,           -- 'agent' or 'owner'
    action      TEXT  NOT NULL,           -- e.g. 'draft_reminder', 'approve'
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Derived, never stored: status is always computed from payments (one source of truth).
-- "Days overdue" is NOT here: it depends on today's date, so tools compute it
-- with an explicit as_of date. That keeps eval answers stable from day to day.
CREATE VIEW invoice_balances AS
SELECT
    i.id                                AS invoice_id,
    i.invoice_no,
    i.buyer_id,
    b.name                              AS buyer_name,
    i.issue_date,
    i.due_date,
    i.amount,
    COALESCE(SUM(p.amount), 0)          AS amount_paid,
    i.amount - COALESCE(SUM(p.amount), 0) AS balance,
    CASE
        WHEN COALESCE(SUM(p.amount), 0) >= i.amount THEN 'paid'
        WHEN COALESCE(SUM(p.amount), 0) > 0         THEN 'partial'
        ELSE 'unpaid'
    END                                 AS status
FROM invoices i
JOIN buyers b        ON b.id = i.buyer_id
LEFT JOIN payments p ON p.invoice_id = i.id
GROUP BY i.id, b.name;
