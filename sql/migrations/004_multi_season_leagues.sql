-- 004: permite múltiples temporadas por liga (superseded by 010)

DO $$ BEGIN
    ALTER TABLE leagues DROP CONSTRAINT leagues_provider_league_id_key;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE leagues ADD CONSTRAINT leagues_provider_season_uq
        UNIQUE (provider_league_id, season);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
