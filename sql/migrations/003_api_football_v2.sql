-- 003: columnas API-Football v3 + tablas de referencia y auditoría (superseded by 010)

ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage     JSONB;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS season_start DATE;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS season_end   DATE;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS current      BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS type         TEXT;

ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS timezone        TEXT;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS date_local      DATE;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS status_short    TEXT;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS status_long     TEXT;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS elapsed         SMALLINT;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS venue_id        BIGINT;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS last_api_update TIMESTAMPTZ;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS context_json    JSONB;

ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS bookmaker_id BIGINT;
ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS bet_id       BIGINT;
ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS scope        TEXT NOT NULL DEFAULT 'prematch';
ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS last_update  TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS ref_bookmakers (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_bookmaker_id BIGINT      NOT NULL UNIQUE,
    name                  TEXT        NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ref_bet_types (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_bet_id BIGINT      NOT NULL UNIQUE,
    name            TEXT        NOT NULL,
    scope           TEXT        NOT NULL DEFAULT 'prematch',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_sync_runs (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phase            TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'running',
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    leagues_synced   INTEGER     NOT NULL DEFAULT 0,
    fixtures_synced  INTEGER     NOT NULL DEFAULT 0,
    odds_rows_synced INTEGER     NOT NULL DEFAULT 0,
    api_calls_made   INTEGER     NOT NULL DEFAULT 0,
    error_message    TEXT,
    summary_json     JSONB
);

CREATE TABLE IF NOT EXISTS api_usage_snapshots (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sync_run_id        BIGINT      REFERENCES api_sync_runs(id),
    endpoint           TEXT,
    requests_limit     INTEGER,
    requests_remaining INTEGER,
    minute_limit       INTEGER,
    minute_remaining   INTEGER,
    captured_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_phase      ON api_sync_runs (phase, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_snapshots_run  ON api_usage_snapshots (sync_run_id);
CREATE INDEX IF NOT EXISTS idx_odds_bookmaker_id    ON odds_snapshots (bookmaker_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_status_short ON fixtures (status_short);
CREATE INDEX IF NOT EXISTS idx_leagues_current       ON leagues (current);
