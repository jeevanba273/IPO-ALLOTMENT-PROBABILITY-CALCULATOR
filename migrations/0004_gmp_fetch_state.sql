-- 0004_gmp_fetch_state.sql: bookkeeping for ipoalerts GMP calls, which are
-- scarce (12 a day): when each IPO was last asked for, what failed, and
-- whether its history has been backfilled.

CREATE TABLE gmp_fetch_state (
    ipo_id           TEXT        PRIMARY KEY REFERENCES ipos (id) ON DELETE CASCADE,
    -- The symbol last asked for. A new symbol (a manual override) starts the
    -- failure count again.
    symbol           TEXT,
    last_attempt_at  TIMESTAMPTZ,
    last_success_at  TIMESTAMPTZ,
    failures         SMALLINT    NOT NULL DEFAULT 0 CHECK (failures >= 0),
    last_error       TEXT,
    backfilled_at    TIMESTAMPTZ
);
