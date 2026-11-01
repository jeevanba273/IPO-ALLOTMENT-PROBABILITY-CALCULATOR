-- 0003_reservation_overrides.sql: reserved shares typed in from the RHP.
-- They replace IndianAPI's figure for that category, and times subscribed is
-- recomputed as applied / reserved.

CREATE TABLE reservation_overrides (
    ipo_id           TEXT        NOT NULL REFERENCES ipos (id) ON DELETE CASCADE,
    category         TEXT        NOT NULL CHECK (category IN ('retail', 'nii', 'qib')),
    reserved_shares  BIGINT      NOT NULL CHECK (reserved_shares > 0),
    note             TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ipo_id, category)
);
