CREATE TABLE IF NOT EXISTS dialog_sessions (
    session_id TEXT PRIMARY KEY,
    driver TEXT,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dialog_sessions_updated ON dialog_sessions (updated_at);
