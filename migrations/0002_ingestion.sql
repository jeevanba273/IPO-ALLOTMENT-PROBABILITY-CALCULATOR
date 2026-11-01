-- 0002_ingestion.sql: bookkeeping for IndianAPI ingestion.

ALTER TABLE ipos
    ADD COLUMN pre_apply_start    DATE,
    -- Where listing_price / listing_gain_pct came from. The id-keyed detail
    -- endpoint beats the name-matched GET /ipo list.
    ADD COLUMN listing_source     TEXT CHECK (listing_source IN ('indianapi_list', 'indianapi_detail')),
    -- A listing gain of exactly 0% or -20% often means the source filled in a
    -- default or price-band value instead of the real listing price.
    ADD COLUMN listing_flag       TEXT CHECK (listing_flag IN ('possible_price_band_default')),
    -- Still no bidding dates long after we first saw it.
    ADD COLUMN dormant            BOOLEAN  NOT NULL DEFAULT false,
    -- Last successful GET /ipo/{id}, and failures since then. The backfill
    -- resumes from IPOs whose final numbers were never fetched.
    ADD COLUMN detail_fetched_at  TIMESTAMPTZ,
    ADD COLUMN detail_failures    SMALLINT NOT NULL DEFAULT 0 CHECK (detail_failures >= 0);

-- Symbols can also come from GET /ipo/{id}.
ALTER TABLE symbols DROP CONSTRAINT symbols_source_check;
ALTER TABLE symbols ADD CONSTRAINT symbols_source_check
    CHECK (source IN ('indianapi_detail', 'indianapi_v2', 'indianapi_list', 'manual'));

-- The symbol to use for each IPO: manual beats the id-keyed IndianAPI
-- sources, which beat a name match from GET /ipo. IPOs without a row here
-- have no GMP mapping.
CREATE VIEW effective_symbols AS
SELECT DISTINCT ON (ipo_id) ipo_id, symbol, source
FROM symbols
ORDER BY ipo_id, CASE source
    WHEN 'manual' THEN 0
    WHEN 'indianapi_detail' THEN 1
    WHEN 'indianapi_v2' THEN 2
    ELSE 3
END;

-- Readings of a source's remaining quota (IndianAPI GET /usage).
CREATE TABLE api_usage (
    source          TEXT        NOT NULL,
    checked_at      TIMESTAMPTZ NOT NULL,
    hard_limit      INTEGER,
    remaining       INTEGER     NOT NULL,
    total_requests  INTEGER,
    PRIMARY KEY (source, checked_at)
);

-- One row per scheduled or manual job run.
CREATE TABLE job_runs (
    id           BIGSERIAL PRIMARY KEY,
    job          TEXT        NOT NULL,
    trigger      TEXT        NOT NULL CHECK (trigger IN ('schedule', 'startup', 'cli')),
    status       TEXT        NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'skipped', 'failed')),
    summary      TEXT,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ
);

CREATE INDEX job_runs_job_started_idx ON job_runs (job, started_at DESC);
