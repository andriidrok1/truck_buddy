-- The live dialog of each session (services/dialog_state.py), so any worker can continue it and a restart
-- keeps a question that was just asked.
CREATE TABLE IF NOT EXISTS dialog_sessions (
    session_id TEXT PRIMARY KEY,
    driver TEXT,
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS dialog_sessions_updated ON dialog_sessions (updated_at);
