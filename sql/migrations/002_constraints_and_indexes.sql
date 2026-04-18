-- 002: constraints e índices (superseded by 010 en instalaciones nuevas)
-- PostgreSQL no soporta ADD CONSTRAINT IF NOT EXISTS; se usa DO $$ para idempotencia.

DO $$ BEGIN
    ALTER TABLE leagues ADD CONSTRAINT leagues_provider_season_uq
        UNIQUE (provider_league_id, season);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE teams ADD CONSTRAINT teams_provider_id_uq
        UNIQUE (provider_team_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE odds_snapshots ADD CONSTRAINT odds_snapshots_uq
        UNIQUE (fixture_id, bookmaker, market, selection);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE predictions ADD CONSTRAINT predictions_fixture_market_uq
        UNIQUE (fixture_id, market);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff_at     ON fixtures (kickoff_at);
CREATE INDEX IF NOT EXISTS idx_fixtures_status         ON fixtures (status);
CREATE INDEX IF NOT EXISTS idx_fixtures_league_id      ON fixtures (league_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_home_team_id   ON fixtures (home_team_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_away_team_id   ON fixtures (away_team_id);
CREATE INDEX IF NOT EXISTS idx_odds_fixture_market     ON odds_snapshots (fixture_id, market);
CREATE INDEX IF NOT EXISTS idx_odds_fixture_id         ON odds_snapshots (fixture_id);
CREATE INDEX IF NOT EXISTS idx_predictions_fixture_id  ON predictions (fixture_id);
CREATE INDEX IF NOT EXISTS idx_predictions_publishable ON predictions (is_publishable);
CREATE INDEX IF NOT EXISTS idx_pred_results_pred_id    ON prediction_results (prediction_id);
