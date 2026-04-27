-- Phase 5 backtest schema additions.
-- Apply with: python scripts/init_local_db.py  (idempotent)

-- ── Sequences ─────────────────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS seq_team_elo          START 1;
CREATE SEQUENCE IF NOT EXISTS seq_training_samples  START 1;

-- ── competition_context ───────────────────────────────────────────────────────
-- Classifies every league present in fixtures_history.
-- Auto-seeded on first backtest run via seed_competition_context() in
-- backtest_service.py. Unknown leagues default to club / domestic_league.
--
-- entity_scope : 'club' | 'national_team'
--   Defines the Elo pool. Clubs and national teams NEVER share ratings.
--
-- competition_type : 'domestic_league' | 'domestic_cup' | 'continental' | 'international'
--   Used for display and to derive elo_weight.
--
-- elo_weight : multiplier applied to K-factor during Elo updates (0.0 – 1.0).
--   Domestic cups (0.4) contribute less than league matches (1.0).

CREATE TABLE IF NOT EXISTS competition_context (
    provider_league_id  INTEGER PRIMARY KEY,
    league_name         VARCHAR,
    country             VARCHAR,
    entity_scope        VARCHAR NOT NULL DEFAULT 'club',
    competition_type    VARCHAR NOT NULL DEFAULT 'domestic_league',
    elo_weight          DOUBLE  NOT NULL DEFAULT 1.0,
    notes               VARCHAR
);

-- ── team_elo_history ──────────────────────────────────────────────────────────
-- One row per team per completed fixture: elo_before → elo_after.
-- entity_scope column enforces pool separation — never query across scopes.
-- The UNIQUE index on (team_id, entity_scope, fixture_id) allows INSERT OR IGNORE
-- so re-running a backtest does not create duplicate rows.
-- The lookup index on (team_id, entity_scope, kickoff_at) supports the
-- "latest Elo before date X" query used during walk-forward evaluation.

CREATE TABLE IF NOT EXISTS team_elo_history (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_team_elo'),
    team_id             BIGINT  NOT NULL,
    entity_scope        VARCHAR NOT NULL DEFAULT 'club',
    provider_league_id  INTEGER,
    season              INTEGER,
    fixture_id          BIGINT,
    kickoff_at          TIMESTAMPTZ,
    elo_before          DOUBLE  NOT NULL,
    elo_after           DOUBLE  NOT NULL,
    opponent_id         BIGINT,
    was_home            BOOLEAN,
    goals_for           INTEGER,
    goals_against       INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS team_elo_fixture_uq
    ON team_elo_history (team_id, entity_scope, fixture_id);

CREATE INDEX IF NOT EXISTS team_elo_lookup_idx
    ON team_elo_history (team_id, entity_scope, kickoff_at);

-- ── training_samples ──────────────────────────────────────────────────────────
-- One row per fixture / market / selection for a backtest run.
-- Designed to be regenerable: delete rows WHERE backtest_run_id = ? and
-- re-insert to rebuild with updated features or model parameters.
--
-- implied_probability, edge, ev_value are NULL in Phase 5 (no historical odds).
-- Phase 5.5 will populate them once /odds backfill is added.

CREATE TABLE IF NOT EXISTS training_samples (
    id                  BIGINT  PRIMARY KEY DEFAULT nextval('seq_training_samples'),
    backtest_run_id     BIGINT  NOT NULL,
    fixture_history_id  BIGINT  NOT NULL,
    provider_league_id  INTEGER,
    season              INTEGER,
    kickoff_at          TIMESTAMPTZ,
    home_team_id        BIGINT,
    away_team_id        BIGINT,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    -- features
    home_elo            DOUBLE,
    away_elo            DOUBLE,
    elo_diff            DOUBLE,
    lambda_home         DOUBLE,
    lambda_away         DOUBLE,
    -- model output
    model_probability   DOUBLE,
    -- odds-dependent (NULL until Phase 5.5 historical odds backfill)
    implied_probability DOUBLE,
    edge                DOUBLE,
    ev_value            DOUBLE,
    -- ground truth
    goals_home          INTEGER,
    goals_away          INTEGER,
    actual_outcome      VARCHAR,
    model_correct       BOOLEAN,
    -- walk-forward split tag
    split               VARCHAR DEFAULT 'test'
);

-- ── Extend backtest_metrics ───────────────────────────────────────────────────
-- is_selected = TRUE  → pick was within the daily cap (simulated official pick)
-- is_selected = FALSE → passed threshold but cut by daily cap

ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS is_selected BOOLEAN DEFAULT TRUE;
