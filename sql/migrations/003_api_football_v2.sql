-- Migration 003: API-Football v3 professional integration
-- Run AFTER migrations 001 and 002.
-- Safe to re-run: all statements use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

-- ═══════════════════════════════════════════════════════════════════════════════
-- leagues: coverage tracking and season metadata
-- ═══════════════════════════════════════════════════════════════════════════════
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage     jsonb;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS season_start date;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS season_end   date;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS current      boolean NOT NULL DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS type         text;

-- ═══════════════════════════════════════════════════════════════════════════════
-- fixtures: rich status, venue, timezone, and match context blob
-- ═══════════════════════════════════════════════════════════════════════════════
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS timezone        text;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS date_local      date;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS status_short    text;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS status_long     text;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS elapsed         smallint;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS venue_id        bigint;
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS last_api_update timestamptz;
-- Stores standings, form, team stats, injuries, h2h, and provider predictions.
-- Populated during daily sync; read by prediction pipeline.
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS context_json    jsonb;

-- ═══════════════════════════════════════════════════════════════════════════════
-- odds_snapshots: bookmaker_id, bet_id, scope, and update timestamp
-- ═══════════════════════════════════════════════════════════════════════════════
ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS bookmaker_id bigint;
ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS bet_id       bigint;
-- scope distinguishes prematch odds from live odds (never mix /odds/bets with /odds/live/bets)
ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS scope       text NOT NULL DEFAULT 'prematch';
ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS last_update timestamptz;

-- ═══════════════════════════════════════════════════════════════════════════════
-- ref_bookmakers: canonical bookmaker registry (from /odds/bookmakers)
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS ref_bookmakers (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_bookmaker_id bigint NOT NULL UNIQUE,
    name                  text   NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- ref_bet_types: canonical bet type registry (from /odds/bets, prematch only)
-- NOTE: never mix with /odds/live/bets — live bet IDs are a separate namespace.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS ref_bet_types (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_bet_id bigint NOT NULL UNIQUE,
    name            text   NOT NULL,
    scope           text   NOT NULL DEFAULT 'prematch',
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- api_sync_runs: one row per sync job execution (audit trail + usage tracking)
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS api_sync_runs (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phase            text    NOT NULL,   -- 'reference' | 'bootstrap' | 'daily' | 'prematch'
    status           text    NOT NULL,   -- 'running' | 'completed' | 'failed'
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    leagues_synced   integer DEFAULT 0,
    fixtures_synced  integer DEFAULT 0,
    odds_rows_synced integer DEFAULT 0,
    api_calls_made   integer DEFAULT 0,
    error_message    text,
    summary_json     jsonb
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- api_usage_snapshots: rate-limit header snapshots (one per notable API call)
-- Tracks x-ratelimit-requests-remaining and x-ratelimit-remaining (per minute).
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS api_usage_snapshots (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at        timestamptz NOT NULL DEFAULT now(),
    requests_limit     integer,     -- x-ratelimit-requests-limit (daily)
    requests_remaining integer,     -- x-ratelimit-requests-remaining (daily)
    minute_limit       integer,     -- x-ratelimit-limit (per-minute)
    minute_remaining   integer,     -- x-ratelimit-remaining (per-minute)
    endpoint           text,
    sync_run_id        bigint REFERENCES api_sync_runs(id)
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- Indexes for new tables and columns
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS sync_runs_phase_idx       ON api_sync_runs (phase);
CREATE INDEX IF NOT EXISTS sync_runs_started_idx     ON api_sync_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS usage_caps_run_id_idx     ON api_usage_snapshots (sync_run_id);
CREATE INDEX IF NOT EXISTS usage_caps_captured_idx   ON api_usage_snapshots (captured_at DESC);
CREATE INDEX IF NOT EXISTS odds_bookmaker_id_idx     ON odds_snapshots (bookmaker_id);
CREATE INDEX IF NOT EXISTS odds_bet_id_idx           ON odds_snapshots (bet_id);
CREATE INDEX IF NOT EXISTS fixtures_status_short_idx ON fixtures (status_short);
CREATE INDEX IF NOT EXISTS leagues_current_idx       ON leagues (current);
