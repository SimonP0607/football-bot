-- DuckDB schema for football-bot local history database.
-- Apply with: python scripts/init_local_db.py
-- All statements are idempotent (CREATE ... IF NOT EXISTS).
-- Foreign key relationships are noted in comments (not enforced — analytical use).

-- ── Sequences (auto-increment for DuckDB-native tables) ───────────────────────

CREATE SEQUENCE IF NOT EXISTS seq_standings_history   START 1;
CREATE SEQUENCE IF NOT EXISTS seq_team_stats_history  START 1;
CREATE SEQUENCE IF NOT EXISTS seq_odds_history        START 1;
CREATE SEQUENCE IF NOT EXISTS seq_backtest_runs       START 1;
CREATE SEQUENCE IF NOT EXISTS seq_backtest_metrics    START 1;

-- ── fixtures_history ──────────────────────────────────────────────────────────
-- Completed matches with final scores.
-- id mirrors Supabase fixtures.id for traceability.

CREATE TABLE IF NOT EXISTS fixtures_history (
    id                  BIGINT PRIMARY KEY,
    provider_fixture_id BIGINT  NOT NULL,
    provider_league_id  INTEGER NOT NULL,
    league_name         VARCHAR,
    season              INTEGER NOT NULL,
    home_team_id        BIGINT  NOT NULL,
    home_team_name      VARCHAR,
    away_team_id        BIGINT  NOT NULL,
    away_team_name      VARCHAR,
    kickoff_at          TIMESTAMPTZ NOT NULL,
    date_local          DATE,
    status_short        VARCHAR,
    goals_home          INTEGER,
    goals_away          INTEGER,
    halftime_home       INTEGER,
    halftime_away       INTEGER,
    venue_name          VARCHAR,
    archived_at         TIMESTAMPTZ DEFAULT current_timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS fixtures_history_provider_uq
    ON fixtures_history (provider_fixture_id);

CREATE INDEX IF NOT EXISTS fixtures_history_league_season_idx
    ON fixtures_history (provider_league_id, season);

CREATE INDEX IF NOT EXISTS fixtures_history_kickoff_idx
    ON fixtures_history (kickoff_at);

-- ── standings_history ─────────────────────────────────────────────────────────
-- League table snapshots captured at a specific date.

CREATE TABLE IF NOT EXISTS standings_history (
    id                  BIGINT PRIMARY KEY DEFAULT nextval('seq_standings_history'),
    provider_league_id  INTEGER NOT NULL,
    league_name         VARCHAR,
    season              INTEGER NOT NULL,
    team_id             BIGINT  NOT NULL,
    team_name           VARCHAR,
    rank                INTEGER,
    points              INTEGER,
    played              INTEGER,
    won                 INTEGER,
    drawn               INTEGER,
    lost                INTEGER,
    goals_for           INTEGER,
    goals_against       INTEGER,
    goal_diff           INTEGER,
    form                VARCHAR,
    snapshot_date       DATE    NOT NULL,
    archived_at         TIMESTAMPTZ DEFAULT current_timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS standings_history_uq
    ON standings_history (provider_league_id, season, team_id, snapshot_date);

-- ── team_stats_history ────────────────────────────────────────────────────────
-- Aggregated per-team per-league statistics snapshots.

CREATE TABLE IF NOT EXISTS team_stats_history (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_team_stats_history'),
    provider_league_id  INTEGER NOT NULL,
    season              INTEGER NOT NULL,
    team_id             BIGINT  NOT NULL,
    team_name           VARCHAR,
    games_played        INTEGER,
    wins                INTEGER,
    draws               INTEGER,
    losses              INTEGER,
    goals_for           DOUBLE,
    goals_against       DOUBLE,
    clean_sheets        INTEGER,
    avg_goals_scored    DOUBLE,
    avg_goals_conceded  DOUBLE,
    form_last5          VARCHAR,
    raw_stats           JSON,
    snapshot_date       DATE    NOT NULL,
    archived_at         TIMESTAMPTZ DEFAULT current_timestamp
);

CREATE UNIQUE INDEX IF NOT EXISTS team_stats_history_uq
    ON team_stats_history (provider_league_id, season, team_id, snapshot_date);

-- ── odds_history ──────────────────────────────────────────────────────────────
-- Best available odds per market/selection at time of pick generation.
-- fixture_history_id references fixtures_history(id).

CREATE TABLE IF NOT EXISTS odds_history (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_odds_history'),
    fixture_history_id  BIGINT,
    provider_fixture_id BIGINT  NOT NULL,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    bookmaker_name      VARCHAR,
    odd                 DOUBLE  NOT NULL,
    implied_probability DOUBLE,
    scope               VARCHAR DEFAULT 'prematch',
    captured_at         TIMESTAMPTZ,
    archived_at         TIMESTAMPTZ DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS odds_history_fixture_idx
    ON odds_history (fixture_history_id);

CREATE INDEX IF NOT EXISTS odds_history_market_idx
    ON odds_history (market_key, selection);

-- ── published_picks_history ───────────────────────────────────────────────────
-- Official picks that were published to Telegram.
-- id mirrors Supabase pick_candidates.id.
-- fixture_history_id references fixtures_history(id).

CREATE TABLE IF NOT EXISTS published_picks_history (
    id                  BIGINT  PRIMARY KEY,
    fixture_history_id  BIGINT,
    provider_fixture_id BIGINT  NOT NULL,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    model_probability   DOUBLE,
    implied_probability DOUBLE,
    edge                DOUBLE,
    confidence_score    DOUBLE,
    best_odd            DOUBLE,
    best_bookmaker      VARCHAR,
    published_at        TIMESTAMPTZ,
    archived_at         TIMESTAMPTZ DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS picks_history_fixture_idx
    ON published_picks_history (fixture_history_id);

CREATE INDEX IF NOT EXISTS picks_history_market_idx
    ON published_picks_history (market_key, selection);

-- ── pick_results_history ──────────────────────────────────────────────────────
-- Settlement outcomes for published picks.
-- id mirrors Supabase pick_results.id.
-- published_pick_id references published_picks_history(id).

CREATE TABLE IF NOT EXISTS pick_results_history (
    id                  BIGINT  PRIMARY KEY,
    published_pick_id   BIGINT,
    fixture_history_id  BIGINT,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    odd_taken           DOUBLE  NOT NULL,
    result_status       VARCHAR NOT NULL,
    settled_at          TIMESTAMPTZ,
    profit_units        DOUBLE,
    archived_at         TIMESTAMPTZ DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS results_history_status_idx
    ON pick_results_history (result_status);

CREATE INDEX IF NOT EXISTS results_history_fixture_idx
    ON pick_results_history (fixture_history_id);

-- ── backtest_runs ─────────────────────────────────────────────────────────────
-- Metadata for each backtest execution.

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_backtest_runs'),
    run_name            VARCHAR NOT NULL,
    description         VARCHAR,
    started_at          TIMESTAMPTZ DEFAULT current_timestamp,
    finished_at         TIMESTAMPTZ,
    leagues             JSON,
    seasons             JSON,
    min_edge            DOUBLE,
    min_confidence      DOUBLE,
    max_daily_picks     INTEGER,
    config_json         JSON,
    status              VARCHAR DEFAULT 'running',
    total_picks         INTEGER,
    total_profit_units  DOUBLE,
    roi_pct             DOUBLE
);

-- ── backtest_metrics ──────────────────────────────────────────────────────────
-- Per-pick results for a backtest run.
-- backtest_run_id references backtest_runs(id).

CREATE TABLE IF NOT EXISTS backtest_metrics (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_backtest_metrics'),
    backtest_run_id     BIGINT  NOT NULL,
    fixture_history_id  BIGINT,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    model_probability   DOUBLE,
    implied_probability DOUBLE,
    edge                DOUBLE,
    confidence_score    DOUBLE,
    odd_taken           DOUBLE,
    result_status       VARCHAR,
    profit_units        DOUBLE,
    kickoff_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS backtest_metrics_run_idx
    ON backtest_metrics (backtest_run_id);

CREATE INDEX IF NOT EXISTS backtest_metrics_market_idx
    ON backtest_metrics (market_key, result_status);

-- ── history_metadata ──────────────────────────────────────────────────────────
-- Internal key-value state for the historical ingest policy.
-- Keys used:
--   first_rollover_done   'true' | 'false'   set after first pruning is skipped

CREATE TABLE IF NOT EXISTS history_metadata (
    key        VARCHAR     PRIMARY KEY,
    value      VARCHAR     NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT current_timestamp
);
