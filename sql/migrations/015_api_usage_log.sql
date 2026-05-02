-- 015: add source_script tracking to api_usage_snapshots
-- Safe to run multiple times (IF NOT EXISTS guards).

ALTER TABLE api_usage_snapshots
    ADD COLUMN IF NOT EXISTS source_script TEXT;
