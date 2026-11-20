-- Accounts, sessions and conversation ownership.
-- Every statement is idempotent (IF NOT EXISTS), so this file is safe to run on every start.

-- People who can sign in. Emails are stored lower-case so login is case-insensitive.
CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,            -- Argon2 hash, never the password itself
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per signed-in browser. We store a HASH of the cookie's token, so someone who
-- reads this table still can't use the sessions.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Which user owns which conversation (LangGraph's thread_id). Used for authorization.
CREATE TABLE IF NOT EXISTS threads (
    thread_id   TEXT PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Who approved or rejected each reminder
ALTER TABLE reminders ADD COLUMN IF NOT EXISTS decided_by INT REFERENCES users(id) ON DELETE SET NULL;
