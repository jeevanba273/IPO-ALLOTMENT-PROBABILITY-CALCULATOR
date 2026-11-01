-- 0001_init.sql: initial schema for the IPO Intelligence Dashboard.
--
-- Conventions
--   * TIMESTAMPTZ columns are instants; the app writes and reads them in UTC.
--   * DATE columns are Indian market calendar days (Asia/Kolkata).
--   * Quantities are counts of shares (BIGINT), never lots.
--   * Money is NUMERIC rupees. Formatted strings from the APIs are never stored.
--   * No signed document URLs (RHP/DRHP expire) and no registrar contact details.
--   * Natural keys are primary keys, so re-running an ingestion upserts instead
--     of duplicating.

-- One row per IPO. id is the IndianAPI slug.
CREATE TABLE ipos (
    id                 TEXT PRIMARY KEY,
    name               TEXT           NOT NULL,
    symbol             TEXT,
    isin               TEXT,
    issue_type         TEXT           NOT NULL CHECK (issue_type IN ('mainboard', 'sme')),
    industry           TEXT,
    price_min          NUMERIC(12, 2) CHECK (price_min >= 0),
    price_max          NUMERIC(12, 2) CHECK (price_max >= 0),
    cutoff_price       NUMERIC(12, 2) CHECK (cutoff_price >= 0),
    face_value         NUMERIC(12, 2) CHECK (face_value >= 0),
    issue_size_cr      NUMERIC(14, 2) CHECK (issue_size_cr >= 0),
    lot_size           INTEGER        CHECK (lot_size > 0),
    min_qty            INTEGER        CHECK (min_qty > 0),
    -- Smallest application in rupees. SME minimums are often 2 lots, so this
    -- uses min_qty rather than lot_size.
    min_investment_rs  NUMERIC(16, 2) GENERATED ALWAYS AS (min_qty * cutoff_price) STORED,
    bid_start          DATE,
    bid_end            DATE,
    allotment_date     DATE,
    listing_date       DATE,
    registrar_name     TEXT,
    allotment_link     TEXT,
    listing_price      NUMERIC(12, 2) CHECK (listing_price >= 0),
    listing_gain_pct   NUMERIC(9, 2),
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT ipos_price_band_order CHECK (price_min <= price_max),
    CONSTRAINT ipos_bid_window_order CHECK (bid_start <= bid_end)
);

CREATE INDEX ipos_bid_window_idx ON ipos (bid_start, bid_end);
CREATE INDEX ipos_listing_date_idx ON ipos (listing_date);

-- Shares reserved per category, from GET /ipo/{id} (zeros before bidding).
CREATE TABLE ipo_categories (
    ipo_id           TEXT        NOT NULL REFERENCES ipos (id) ON DELETE CASCADE,
    category         TEXT        NOT NULL CHECK (category IN ('retail', 'nii', 'qib')),
    reserved_shares  BIGINT      NOT NULL CHECK (reserved_shares >= 0),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ipo_id, category)
);

-- Cumulative subscription at the end of each bidding day, per category.
CREATE TABLE subscription_snapshots (
    ipo_id           TEXT           NOT NULL REFERENCES ipos (id) ON DELETE CASCADE,
    bid_date         DATE           NOT NULL,
    category         TEXT           NOT NULL CHECK (category IN ('retail', 'nii', 'qib', 'overall')),
    applied_shares   BIGINT         NOT NULL CHECK (applied_shares >= 0),
    reserved_shares  BIGINT         NOT NULL CHECK (reserved_shares >= 0),
    times            NUMERIC(14, 4) NOT NULL CHECK (times >= 0),
    fetched_at       TIMESTAMPTZ    NOT NULL DEFAULT now(),
    PRIMARY KEY (ipo_id, bid_date, category)
);

-- Grey market premium in rupees per share. Can be negative.
-- ipoalerts points carry interval 'hourly' or 'daily'; manual entries use
-- interval 'manual'.
CREATE TABLE gmp_snapshots (
    ipo_id      TEXT           NOT NULL REFERENCES ipos (id) ON DELETE CASCADE,
    ts_utc      TIMESTAMPTZ    NOT NULL,
    interval    TEXT           NOT NULL CHECK (interval IN ('hourly', 'daily', 'manual')),
    gmp_rs      NUMERIC(12, 2) NOT NULL,
    source      TEXT           NOT NULL CHECK (source IN ('ipoalerts', 'manual')),
    fetched_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    PRIMARY KEY (ipo_id, ts_utc, interval, source),
    CONSTRAINT gmp_manual_interval CHECK ((source = 'manual') = (interval = 'manual'))
);

-- Exchange symbol per IPO and where it came from. A 'manual' row overrides the rest.
CREATE TABLE symbols (
    ipo_id      TEXT        NOT NULL REFERENCES ipos (id) ON DELETE CASCADE,
    symbol      TEXT        NOT NULL CHECK (symbol <> ''),
    source      TEXT        NOT NULL CHECK (source IN ('indianapi_v2', 'indianapi_list', 'manual')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ipo_id, source)
);

CREATE INDEX symbols_symbol_idx ON symbols (symbol);

-- One row per attempt of every external call, skipped attempts included.
-- endpoint is the path and query only; credentials never reach this table.
CREATE TABLE source_runs (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT        NOT NULL,
    endpoint     TEXT        NOT NULL,
    purpose      TEXT,
    attempt      SMALLINT    NOT NULL CHECK (attempt >= 1),
    status       TEXT        NOT NULL CHECK (status IN (
                     'ok', 'http_error', 'rate_limited', 'timeout', 'network_error',
                     'decode_error', 'skipped_budget', 'skipped_blocked')),
    http_status  SMALLINT,
    duration_ms  INTEGER     NOT NULL CHECK (duration_ms >= 0),
    error        TEXT,
    ran_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX source_runs_source_ran_at_idx ON source_runs (source, ran_at DESC);

-- Calls spent per source, IST day and purpose. The monthly total is the sum
-- over an IST calendar month. A new IST day starts new rows, which is the
-- 00:00 IST reset.
CREATE TABLE api_budget (
    source      TEXT        NOT NULL,
    day         DATE        NOT NULL,
    month       DATE        NOT NULL,
    purpose     TEXT        NOT NULL CHECK (purpose IN ('live', 'backfill')),
    count       INTEGER     NOT NULL DEFAULT 0 CHECK (count >= 0),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, day, purpose),
    CONSTRAINT api_budget_month_of_day CHECK (month = date_trunc('month', day::timestamp)::date)
);

CREATE INDEX api_budget_month_idx ON api_budget (source, month);

-- A source that answered 429 is blocked until its quota resets.
CREATE TABLE api_blocks (
    source         TEXT PRIMARY KEY,
    blocked_until  TIMESTAMPTZ NOT NULL,
    reason         TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Telegram alerts already sent; the primary key is the dedupe. Alerts that
-- are not about a single IPO (quota warnings) use a pseudo id such as 'system'.
CREATE TABLE alerts_log (
    ipo_id   TEXT        NOT NULL,
    kind     TEXT        NOT NULL,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ipo_id, kind)
);
