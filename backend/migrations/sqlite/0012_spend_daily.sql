-- What the paid APIs cost per US Pacific day (services/spend.py): usd:<provider> and calls:<provider> counters,
-- added to by every worker, and alert:<threshold> rows that let one worker send each day's alert. 0012, not 0011:
-- PR #149 (trip log) adds 0011.
CREATE TABLE IF NOT EXISTS spend_daily (
    day TEXT NOT NULL,
    key TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);
