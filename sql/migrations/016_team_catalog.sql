-- 016: extend teams table with entity metadata
-- Safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS guards).

ALTER TABLE teams ADD COLUMN IF NOT EXISTS is_national BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS code         TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS founded      INTEGER;
