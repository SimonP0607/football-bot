-- Migration 002: unique constraints and indexes missing from initial schema.
-- Safe to run multiple times (IF NOT EXISTS / DO NOTHING patterns).

-- ── leagues ──────────────────────────────────────────────────────────────────
-- Each external league+season pair must be unique to prevent duplicates on re-sync.
ALTER TABLE leagues
    ADD CONSTRAINT IF NOT EXISTS leagues_provider_season_uq
    UNIQUE (provider_league_id, season);

-- ── teams ─────────────────────────────────────────────────────────────────────
ALTER TABLE teams
    ADD CONSTRAINT IF NOT EXISTS teams_provider_id_uq
    UNIQUE (provider_team_id);

-- ── fixtures ──────────────────────────────────────────────────────────────────
-- Index for querying fixtures by date (the most frequent access pattern).
CREATE INDEX IF NOT EXISTS fixtures_kickoff_at_idx ON fixtures (kickoff_at);
-- Index for filtering by status (e.g. "NS" = Not Started).
CREATE INDEX IF NOT EXISTS fixtures_status_idx ON fixtures (status);

-- ── odds_snapshots ────────────────────────────────────────────────────────────
-- Prevent duplicate rows on re-sync; the odd value is updated when it changes.
ALTER TABLE odds_snapshots
    ADD CONSTRAINT IF NOT EXISTS odds_snapshots_uq
    UNIQUE (fixture_id, bookmaker, market, selection);

-- Composite index: all look-ups are by fixture_id then filtered by market.
CREATE INDEX IF NOT EXISTS odds_fixture_market_idx ON odds_snapshots (fixture_id, market);

-- ── predictions ───────────────────────────────────────────────────────────────
-- One prediction per fixture per market (upserted on re-run of the pipeline).
ALTER TABLE predictions
    ADD CONSTRAINT IF NOT EXISTS predictions_fixture_market_uq
    UNIQUE (fixture_id, market);

-- Index for fetching publishable picks of the day.
CREATE INDEX IF NOT EXISTS predictions_publishable_idx ON predictions (is_publishable);
CREATE INDEX IF NOT EXISTS predictions_fixture_id_idx  ON predictions (fixture_id);

-- ── prediction_results ────────────────────────────────────────────────────────
-- Add default timestamp consistent with all other tables.
ALTER TABLE prediction_results
    ALTER COLUMN evaluated_at SET DEFAULT now();

-- ── FK support indexes (PostgreSQL does not auto-create them) ─────────────────
CREATE INDEX IF NOT EXISTS fixtures_league_id_idx    ON fixtures (league_id);
CREATE INDEX IF NOT EXISTS fixtures_home_team_id_idx ON fixtures (home_team_id);
CREATE INDEX IF NOT EXISTS fixtures_away_team_id_idx ON fixtures (away_team_id);
CREATE INDEX IF NOT EXISTS odds_fixture_id_idx       ON odds_snapshots (fixture_id);
CREATE INDEX IF NOT EXISTS predictions_fixture_idx   ON predictions (fixture_id);
CREATE INDEX IF NOT EXISTS pred_results_pred_idx     ON prediction_results (prediction_id);
