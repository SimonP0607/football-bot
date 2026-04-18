-- 011: add sync_tier to tracked_competitions
-- Values: 'tier_1_daily' | 'tier_2_matchday' | 'tier_3_light'
-- Existing rows default to tier_1_daily (re-seeded by seed_tracked_competitions.sql).

ALTER TABLE tracked_competitions
    ADD COLUMN IF NOT EXISTS sync_tier TEXT NOT NULL DEFAULT 'tier_1_daily';
