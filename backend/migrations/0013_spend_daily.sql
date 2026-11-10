-- What the paid APIs cost per US Pacific day (services/spend.py): usd:<provider> and calls:<provider> counters,
-- added to by every worker, and alert:<threshold> rows that let one worker send each day's alert. 0013: #149 takes 0011 and #150 takes 0012.
--
CREATE TABLE IF NOT EXISTS spend_daily (
    day TEXT NOT NULL,
    key TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);
