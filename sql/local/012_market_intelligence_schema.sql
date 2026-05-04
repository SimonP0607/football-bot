-- 012_market_intelligence_schema.sql
-- Phase 12: Market Intelligence, Odds History & CLV Engine
-- All idempotent — safe to run multiple times.

-- ── market_odds_history ───────────────────────────────────────────────────────
-- Full history of odds snapshots per fixture/market/selection/bookmaker.
CREATE TABLE IF NOT EXISTS market_odds_history (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    provider_fixture_id BIGINT NOT NULL,
    fixture_id          BIGINT,
    league_id           BIGINT,
    season              INTEGER,
    bookmaker_id        INTEGER,
    bookmaker_name      VARCHAR,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    odds                FLOAT NOT NULL,
    implied_probability FLOAT,
    snapshot_type       VARCHAR DEFAULT 'prematch',  -- opening|prematch|near_kickoff|closing|live
    snapshot_time       TIMESTAMP,
    minutes_to_kickoff  FLOAT,
    source              VARCHAR DEFAULT 'api_football',
    raw_json            VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS market_odds_history_seq START 1;

CREATE INDEX IF NOT EXISTS idx_moh_provider_fixture ON market_odds_history(provider_fixture_id);
CREATE INDEX IF NOT EXISTS idx_moh_fixture_id       ON market_odds_history(fixture_id);
CREATE INDEX IF NOT EXISTS idx_moh_league_season    ON market_odds_history(league_id, season);
CREATE INDEX IF NOT EXISTS idx_moh_bookmaker        ON market_odds_history(bookmaker_id);
CREATE INDEX IF NOT EXISTS idx_moh_market_key       ON market_odds_history(market_key);
CREATE INDEX IF NOT EXISTS idx_moh_selection        ON market_odds_history(selection);
CREATE INDEX IF NOT EXISTS idx_moh_snapshot_time    ON market_odds_history(snapshot_time);
CREATE INDEX IF NOT EXISTS idx_moh_snapshot_type    ON market_odds_history(snapshot_type);


-- ── market_closing_lines ──────────────────────────────────────────────────────
-- Aggregated opening / best / closing odds per fixture/market/selection/bookmaker.
CREATE TABLE IF NOT EXISTS market_closing_lines (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    updated_at          TIMESTAMP DEFAULT current_timestamp,
    provider_fixture_id BIGINT NOT NULL,
    fixture_id          BIGINT,
    league_id           BIGINT,
    season              INTEGER,
    bookmaker_id        INTEGER,
    bookmaker_name      VARCHAR,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    opening_odds        FLOAT,
    best_seen_odds      FLOAT,
    closing_odds        FLOAT,
    opening_implied     FLOAT,
    closing_implied     FLOAT,
    odds_delta          FLOAT,      -- closing_odds - opening_odds
    implied_delta       FLOAT,      -- closing_implied - opening_implied
    movement_label      VARCHAR DEFAULT 'no_data',
    confidence_score    FLOAT,
    raw_json            VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS market_closing_lines_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mcl_unique
    ON market_closing_lines(provider_fixture_id, market_key, selection, bookmaker_id);

CREATE INDEX IF NOT EXISTS idx_mcl_provider_fixture ON market_closing_lines(provider_fixture_id);
CREATE INDEX IF NOT EXISTS idx_mcl_fixture_id       ON market_closing_lines(fixture_id);
CREATE INDEX IF NOT EXISTS idx_mcl_league_season    ON market_closing_lines(league_id, season);
CREATE INDEX IF NOT EXISTS idx_mcl_market_key       ON market_closing_lines(market_key);
CREATE INDEX IF NOT EXISTS idx_mcl_movement_label   ON market_closing_lines(movement_label);


-- ── pick_clv_results ──────────────────────────────────────────────────────────
-- CLV measurement per pick: did we beat the closing line?
CREATE TABLE IF NOT EXISTS pick_clv_results (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    pick_candidate_id   BIGINT,
    published_pick_id   BIGINT,
    fixture_id          BIGINT,
    provider_fixture_id BIGINT,
    league_id           BIGINT,
    market_key          VARCHAR,
    selection           VARCHAR,
    pick_odds           FLOAT,
    closing_odds        FLOAT,
    pick_implied        FLOAT,
    closing_implied     FLOAT,
    clv_percent         FLOAT,      -- (closing_implied - pick_implied) * 100
    clv_result          VARCHAR DEFAULT 'no_data',  -- positive|neutral|negative|no_data
    beat_closing_line   BOOLEAN DEFAULT false,
    bookmaker_name      VARCHAR,
    metadata_json       VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS pick_clv_results_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pclv_unique
    ON pick_clv_results(pick_candidate_id, market_key, selection);

CREATE INDEX IF NOT EXISTS idx_pclv_published_pick  ON pick_clv_results(published_pick_id);
CREATE INDEX IF NOT EXISTS idx_pclv_fixture_id      ON pick_clv_results(fixture_id);
CREATE INDEX IF NOT EXISTS idx_pclv_provider_fixture ON pick_clv_results(provider_fixture_id);
CREATE INDEX IF NOT EXISTS idx_pclv_league          ON pick_clv_results(league_id);
CREATE INDEX IF NOT EXISTS idx_pclv_market_key      ON pick_clv_results(market_key);
CREATE INDEX IF NOT EXISTS idx_pclv_clv_result      ON pick_clv_results(clv_result);
CREATE INDEX IF NOT EXISTS idx_pclv_beat_closing    ON pick_clv_results(beat_closing_line);
CREATE INDEX IF NOT EXISTS idx_pclv_created_at      ON pick_clv_results(created_at);


-- ── market_movement_signals ───────────────────────────────────────────────────
-- Analytical signals derived from odds movement. NOT official picks.
CREATE TABLE IF NOT EXISTS market_movement_signals (
    id                  BIGINT PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT current_timestamp,
    fixture_id          BIGINT,
    provider_fixture_id BIGINT NOT NULL,
    league_id           BIGINT,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    signal_type         VARCHAR,  -- steam|drift|sharp_move|stable|no_liquidity|reverse_line
    movement_label      VARCHAR,
    opening_odds        FLOAT,
    current_odds        FLOAT,
    closing_odds        FLOAT,
    implied_delta       FLOAT,
    confidence_score    FLOAT,
    reason_text         VARCHAR,
    risk_text           VARCHAR,
    status              VARCHAR DEFAULT 'observed',
    metadata_json       VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS market_movement_signals_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mms_unique
    ON market_movement_signals(provider_fixture_id, market_key, selection, signal_type);

CREATE INDEX IF NOT EXISTS idx_mms_provider_fixture ON market_movement_signals(provider_fixture_id);
CREATE INDEX IF NOT EXISTS idx_mms_fixture_id       ON market_movement_signals(fixture_id);
CREATE INDEX IF NOT EXISTS idx_mms_league           ON market_movement_signals(league_id);
CREATE INDEX IF NOT EXISTS idx_mms_market_key       ON market_movement_signals(market_key);
CREATE INDEX IF NOT EXISTS idx_mms_signal_type      ON market_movement_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_mms_created_at       ON market_movement_signals(created_at);
