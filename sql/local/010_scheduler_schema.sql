-- 010_scheduler_schema.sql
-- Phase 10: Scheduler & Proactive Alerts tables
-- All idempotent — safe to run multiple times (CREATE IF NOT EXISTS).

-- One row per job execution attempt
CREATE TABLE IF NOT EXISTS scheduler_runs (
    id              BIGINT PRIMARY KEY,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    job_key         VARCHAR NOT NULL,
    status          VARCHAR,  -- 'running'|'completed'|'error'|'skipped_budget'|'skipped_disabled'
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    duration_ms     INTEGER,
    api_calls_used  INTEGER DEFAULT 0,
    messages_sent   INTEGER DEFAULT 0,
    error_message   VARCHAR,
    metadata_json   VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS scheduler_runs_seq START 1;

CREATE INDEX IF NOT EXISTS idx_scheduler_runs_job_key  ON scheduler_runs(job_key);
CREATE INDEX IF NOT EXISTS idx_scheduler_runs_created  ON scheduler_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_scheduler_runs_status   ON scheduler_runs(status);

-- Proactive notification log — dedupe_key prevents duplicate sends
CREATE TABLE IF NOT EXISTS scheduler_notifications (
    id                BIGINT PRIMARY KEY,
    created_at        TIMESTAMP DEFAULT current_timestamp,
    job_key           VARCHAR,
    user_id           BIGINT,
    fixture_id        BIGINT,
    notification_type VARCHAR,
    title             VARCHAR,
    message_text      VARCHAR,
    sent_status       VARCHAR DEFAULT 'pending',  -- 'sent'|'failed'|'skipped_dedup'
    sent_at           TIMESTAMP,
    error_message     VARCHAR,
    dedupe_key        VARCHAR UNIQUE,
    metadata_json     VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS scheduler_notifications_seq START 1;

CREATE INDEX IF NOT EXISTS idx_sched_notif_user_id    ON scheduler_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_sched_notif_fixture_id ON scheduler_notifications(fixture_id);
CREATE INDEX IF NOT EXISTS idx_sched_notif_type       ON scheduler_notifications(notification_type);
CREATE INDEX IF NOT EXISTS idx_sched_notif_created    ON scheduler_notifications(created_at);

-- Key-value state store for scheduler (last run times, etc.)
CREATE TABLE IF NOT EXISTS scheduler_state (
    key        VARCHAR PRIMARY KEY,
    value_json VARCHAR,
    updated_at TIMESTAMP DEFAULT current_timestamp
);

-- Pre-seed initial state keys (idempotent)
INSERT OR IGNORE INTO scheduler_state (key, value_json)
VALUES
    ('last_daily_sync',    'null'),
    ('last_prematch',      'null'),
    ('last_live_monitor',  'null'),
    ('last_settlement',    'null'),
    ('last_daily_report',  'null');
