-- Workspaces: each business gets its own isolated set of buyers, invoices, payments,
-- contracts and reminders (multi-tenancy). Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS workspaces (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    is_demo     BOOLEAN NOT NULL DEFAULT false,   -- the demo is read-only for imports
    as_of_date  DATE,                             -- fixed "today" (demo only); NULL = real today
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The sample business becomes workspace 1, the shared demo
INSERT INTO workspaces (id, name, is_demo, as_of_date)
VALUES (1, 'Meera Packaging Solutions', true, '2026-11-15')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('workspaces', 'id'), GREATEST((SELECT max(id) FROM workspaces), 1));

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id  INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
-- Everyone who already has an account can use the demo
INSERT INTO workspace_members (workspace_id, user_id, role)
SELECT 1, id, 'member' FROM users
ON CONFLICT DO NOTHING;

-- Which workspace each signed-in browser is looking at, and which workspace a conversation is in
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE SET NULL;
ALTER TABLE threads  ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE;
UPDATE threads SET workspace_id = 1 WHERE workspace_id IS NULL;

-- Buyers belong to a workspace. Names are unique per workspace (not globally), ignoring case.
ALTER TABLE buyers ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE;
UPDATE buyers SET workspace_id = 1 WHERE workspace_id IS NULL;
ALTER TABLE buyers ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE buyers DROP CONSTRAINT IF EXISTS buyers_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_buyers_workspace_name ON buyers (workspace_id, lower(name));
CREATE UNIQUE INDEX IF NOT EXISTS uq_buyers_id_workspace ON buyers (id, workspace_id);

-- Invoices carry the workspace too (fast filtering, per-workspace invoice numbers). The
-- composite foreign key makes it IMPOSSIBLE to attach an invoice to another workspace's buyer.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS workspace_id INT;
UPDATE invoices i SET workspace_id = b.workspace_id
FROM buyers b WHERE b.id = i.buyer_id AND i.workspace_id IS NULL;
ALTER TABLE invoices ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE invoices DROP CONSTRAINT IF EXISTS invoices_invoice_no_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_invoices_workspace_no ON invoices (workspace_id, upper(invoice_no));
CREATE INDEX IF NOT EXISTS idx_invoices_workspace ON invoices (workspace_id);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'invoices_buyer_same_workspace') THEN
        ALTER TABLE invoices ADD CONSTRAINT invoices_buyer_same_workspace
            FOREIGN KEY (buyer_id, workspace_id) REFERENCES buyers (id, workspace_id) ON DELETE CASCADE;
    END IF;
END $$;

-- Importing the same payments file twice must not count the money twice
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_dedupe
    ON payments (invoice_id, paid_on, amount, COALESCE(reference, ''));

-- Two businesses may both upload a file called "contract.pdf"
ALTER TABLE contract_chunks DROP CONSTRAINT IF EXISTS contract_chunks_source_file_chunk_index_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_chunks_buyer_file_index ON contract_chunks (buyer_id, source_file, chunk_index);

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE;

-- The view gains a workspace column (added at the end, which CREATE OR REPLACE allows)
CREATE OR REPLACE VIEW invoice_balances AS
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
    END                                 AS status,
    i.workspace_id
FROM invoices i
JOIN buyers b        ON b.id = i.buyer_id
LEFT JOIN payments p ON p.invoice_id = i.id
GROUP BY i.id, b.name;
