-- Phase 4: Player Availability & Lineups Intelligence
-- DuckDB local schema — idempotent (IF NOT EXISTS / INSERT OR IGNORE throughout)

CREATE SEQUENCE IF NOT EXISTS seq_injury_row;
CREATE SEQUENCE IF NOT EXISTS seq_lineup_row;
CREATE SEQUENCE IF NOT EXISTS seq_availability_signal;
CREATE SEQUENCE IF NOT EXISTS seq_team_avail_summary;

-- ── Injuries per fixture ───────────────────────────────────────────────────────
-- One row per (fixture, team, player_name, type, reason).
-- Populated by sync_availability_today.py via /injuries?fixture=<id>.
-- player_id is nullable — not all API responses include it.

CREATE TABLE IF NOT EXISTS fixture_injuries_history (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_injury_row'),
    provider_fixture_id BIGINT  NOT NULL,
    fixture_id      BIGINT,
    league_id       INTEGER,
    season          INTEGER,
    team_id         INTEGER  NOT NULL,
    player_id       INTEGER,
    player_name     TEXT     NOT NULL,
    type            TEXT,       -- 'injured' | 'suspended'
    reason          TEXT,
    source          TEXT     DEFAULT 'api_football',
    fetched_at      TEXT     NOT NULL,
    raw_json        TEXT,
    UNIQUE (provider_fixture_id, team_id, player_name, type, reason)
);

-- ── Lineups per fixture ────────────────────────────────────────────────────────
-- One row per (fixture, team). Replaced on re-sync (upsert with ON CONFLICT).
-- raw_json holds the full startXI array for future use.

CREATE TABLE IF NOT EXISTS fixture_lineups_history (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_lineup_row'),
    provider_fixture_id BIGINT  NOT NULL,
    fixture_id      BIGINT,
    league_id       INTEGER,
    season          INTEGER,
    team_id         INTEGER  NOT NULL,
    formation       TEXT,
    coach_name      TEXT,
    fetched_at      TEXT     NOT NULL,
    raw_json        TEXT,
    UNIQUE (provider_fixture_id, team_id)
);

-- ── Per-player signals ─────────────────────────────────────────────────────────
-- Derived from injuries. One row per (fixture, team, player_name, signal_type).
-- Confidence is boosted when player_id is found in squad_membership.

CREATE TABLE IF NOT EXISTS player_availability_signals (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_availability_signal'),
    provider_fixture_id BIGINT  NOT NULL,
    fixture_id      BIGINT,
    team_id         INTEGER  NOT NULL,
    player_id       INTEGER,
    player_name     TEXT     NOT NULL,
    signal_type     TEXT     NOT NULL,  -- 'injury' | 'suspension'
    severity        TEXT,               -- 'low' | 'medium' | 'high'
    confidence      TEXT,               -- 'low' | 'medium' | 'high'
    reason          TEXT,
    position        TEXT,               -- filled when player found in squad catalog
    created_at      TEXT     NOT NULL,
    UNIQUE (provider_fixture_id, team_id, player_name, signal_type)
);

-- ── Team-level availability summary ───────────────────────────────────────────
-- One row per (fixture, team). Replaced on re-sync.
-- impact_label: 'none' | 'low' | 'medium' | 'high' | 'unknown'
-- coverage_status: 'data' | 'no_data' | 'api_error'

CREATE TABLE IF NOT EXISTS team_availability_summary (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_team_avail_summary'),
    provider_fixture_id BIGINT  NOT NULL,
    fixture_id      BIGINT,
    team_id         INTEGER  NOT NULL,
    missing_count   INTEGER  DEFAULT 0,
    suspended_count INTEGER  DEFAULT 0,
    injury_count    INTEGER  DEFAULT 0,
    severity_score  FLOAT    DEFAULT 0.0,
    impact_label    TEXT     DEFAULT 'unknown',
    coverage_status TEXT     DEFAULT 'unknown',
    updated_at      TEXT     NOT NULL,
    UNIQUE (provider_fixture_id, team_id)
);
