-- 007_live_monitor_schema.sql
-- Phase 7: Live Monitoring tables
-- All idempotent — safe to run multiple times (CREATE IF NOT EXISTS).
-- Uses TIMESTAMP (not TIMESTAMPTZ) for DuckDB compatibility without pytz.

-- Latest known state of a tracked live fixture.
CREATE TABLE IF NOT EXISTS live_fixture_snapshots (
    provider_fixture_id BIGINT PRIMARY KEY,
    status_short        VARCHAR,
    status_elapsed      INTEGER,
    goals_home          INTEGER,
    goals_away          INTEGER,
    is_finished         BOOLEAN DEFAULT FALSE,
    last_seen_at        TIMESTAMP,
    created_at          TIMESTAMP DEFAULT current_timestamp
);

-- Key events captured for live fixtures (goals, red cards).
-- event_id is "{provider_fixture_id}_{minute}_{type}_{team_side}".
CREATE TABLE IF NOT EXISTS live_fixture_events (
    event_id            VARCHAR PRIMARY KEY,
    provider_fixture_id BIGINT NOT NULL,
    event_minute        INTEGER,
    event_type          VARCHAR,
    team_side           VARCHAR,
    player_name         VARCHAR,
    detail              VARCHAR,
    captured_at         TIMESTAMP DEFAULT current_timestamp
);

-- Live state of each tracked pick (one row per pick_candidate_id).
-- live_state: 'winning' | 'losing' | 'open'
CREATE TABLE IF NOT EXISTS live_pick_tracking (
    pick_candidate_id   INTEGER PRIMARY KEY,
    provider_fixture_id BIGINT NOT NULL,
    market_key          VARCHAR NOT NULL,
    selection           VARCHAR NOT NULL,
    live_state          VARCHAR,
    goals_home          INTEGER,
    goals_away          INTEGER,
    status_elapsed      INTEGER,
    status_short        VARCHAR,
    last_updated_at     TIMESTAMP DEFAULT current_timestamp
);

-- Anti-spam deduplication: each notification is sent at most once per log_id.
-- log_id pattern: "{pick_candidate_id}_{notification_type}_{state}"
CREATE TABLE IF NOT EXISTS live_notifications_log (
    log_id              VARCHAR PRIMARY KEY,
    pick_candidate_id   INTEGER,
    provider_fixture_id BIGINT,
    notification_type   VARCHAR,
    state               VARCHAR,
    sent_at             TIMESTAMP DEFAULT current_timestamp
);
