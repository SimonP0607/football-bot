import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Resolve .env relative to this file's location so it works regardless of the
# working directory from which the process was launched.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_ENV_FILE)


def _int(key: str, default: int) -> int:
    v = os.getenv(key, "").strip()
    return int(v) if v else default


def _float(key: str, default: float) -> float:
    v = os.getenv(key, "").strip()
    return float(v) if v else default


@dataclass
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # 0 means "not configured yet" — bootstrap mode active (local only)
    telegram_allowed_user_id: int = _int("TELEGRAM_ALLOWED_USER_ID", 0)

    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_KEY", "")

    api_football_base_url: str = os.getenv(
        "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
    )
    api_football_key: str = os.getenv("API_FOOTBALL_KEY", "")

    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "America/Bogota")
    default_markets: str = os.getenv("DEFAULT_MARKETS", "1X2,OU25,BTTS")
    max_daily_picks: int = _int("MAX_DAILY_PICKS", 5)
    # edge = model_prob (fair consensus) - implied_prob (1/best_odd).
    # In efficient markets with 7-10% overround, fair probs are ~7pp below raw
    # implied. Edge of 0.0 means the best available odd is at or above consensus
    # fair break-even — a genuinely meaningful threshold. Use 0.05 only if you
    # want to target genuine soft lines (rare in liquid markets).
    min_edge: float = _float("MIN_EDGE", 0.0)
    # confidence_score == model_probability (fair consensus prob). 0.52 means
    # the market consensus considers this outcome a slight majority — the lowest
    # bar that avoids picking against the market. Set higher for more selectivity.
    min_confidence: float = _float("MIN_CONFIDENCE", 0.52)

    # Prematch guard: fixtures that kick off within this many minutes are skipped
    # when generating or displaying picks.
    prematch_min_lead_minutes: int = _int("PREMATCH_MIN_LEAD_MINUTES", 15)

    # Maximum number of picks shown by /top (separate from MAX_DAILY_PICKS cap).
    top_picks_limit: int = _int("TOP_PICKS_LIMIT", 3)

    # Sync configuration
    default_league_ids: str = os.getenv("DEFAULT_LEAGUE_IDS", "")
    default_season: int = _int("DEFAULT_SEASON", 0)
    league_seasons: str = os.getenv("LEAGUE_SEASONS", "")

    # Preferred bookmaker for odds selection (optional).
    # When set, the system favours this bookmaker's odds when available.
    # Use the bookmaker name as returned by /odds/bookmakers (e.g. "Bet365").
    preferred_bookmaker: str = os.getenv("PREFERRED_BOOKMAKER", "")

    # Preferred bookmaker ID (numeric). Takes priority over preferred_bookmaker
    # name when set.  Find the ID from /odds/bookmakers or ref_bookmakers table.
    preferred_bookmaker_id: int = _int("PREFERRED_BOOKMAKER_ID", 0)

    # ── Retención de datos ────────────────────────────────────────────────────
    # Hot tier (TTL corto — 3 días por defecto)
    retention_fixtures_days: int = _int("RETENTION_FIXTURES_DAYS", 3)
    retention_odds_days: int = _int("RETENTION_ODDS_DAYS", 3)
    retention_context_days: int = _int("RETENTION_CONTEXT_DAYS", 3)
    retention_candidates_days: int = _int("RETENTION_CANDIDATES_DAYS", 3)
    # Warm tier
    retention_published_picks_days: int = _int("RETENTION_PUBLISHED_PICKS_DAYS", 45)
    retention_settlement_days: int = _int("RETENTION_SETTLEMENT_DAYS", 90)
    retention_sync_runs_days: int = _int("RETENTION_SYNC_RUNS_DAYS", 14)
    retention_usage_days: int = _int("RETENTION_USAGE_DAYS", 14)
    # Cache tier
    retention_h2h_days: int = _int("RETENTION_H2H_DAYS", 30)
    retention_team_metrics_days: int = _int("RETENTION_TEAM_METRICS_DAYS", 30)
    retention_market_cache_days: int = _int("RETENTION_MARKET_CACHE_DAYS", 30)
    # Cron history
    retention_cron_history_days: int = _int("RETENTION_CRON_HISTORY_DAYS", 14)
    # Archivado local (desactivado por defecto)
    local_archive_enabled: bool = (
        os.getenv("LOCAL_ARCHIVE_ENABLED", "false").lower() == "true"
    )
    local_archive_dir: str = os.getenv("LOCAL_ARCHIVE_DIR", "./data/archive")
    local_archive_before_delete: bool = (
        os.getenv("LOCAL_ARCHIVE_BEFORE_DELETE", "false").lower() == "true"
    )

    # ── Base histórica local (DuckDB) ─────────────────────────────────────────
    local_db_path: str = os.getenv("LOCAL_DB_PATH", "./data/local/football_history.duckdb")

    # ── Política de ingestión histórica (Phase 3) ─────────────────────────────
    # Máximo de temporadas cerradas a conservar por liga en DuckDB.
    history_max_closed_seasons: int = _int("HISTORY_MAX_CLOSED_SEASONS", 4)
    # Si True, la primera vez que se supere el máximo se omite el borrado.
    history_skip_prune_on_first_rollover: bool = (
        os.getenv("HISTORY_SKIP_PRUNE_ON_FIRST_ROLLOVER", "true").lower() == "true"
    )
    # Fracción mínima de fixtures en estado terminal para considerar la temporada cerrada.
    history_min_terminal_fraction: float = _float("HISTORY_MIN_TERMINAL_FRACTION", 0.95)

    def __post_init__(self) -> None:
        # Strip whitespace to catch copy-paste errors (trailing newlines, spaces)
        self.api_football_key = self.api_football_key.strip()
        self.supabase_key = self.supabase_key.strip()
        self.supabase_url = self.supabase_url.strip()
        self.telegram_bot_token = self.telegram_bot_token.strip()

        _placeholder = "<COMPLETAR>"

        def _is_missing(val: str) -> bool:
            return not val or _placeholder in val

        missing: list[str] = []

        if _is_missing(self.telegram_bot_token):
            missing.append("TELEGRAM_BOT_TOKEN")
        if _is_missing(self.supabase_url):
            missing.append("SUPABASE_URL")
        if _is_missing(self.supabase_key):
            missing.append("SUPABASE_KEY")
        if _is_missing(self.api_football_key):
            missing.append("API_FOOTBALL_KEY")

        # TELEGRAM_ALLOWED_USER_ID is only required in production.
        # In local/dev mode a missing value (0) activates bootstrap mode instead
        # of crashing, so the owner can run /id to discover their numeric ID.
        if self.app_env == "production" and not self.telegram_allowed_user_id:
            missing.append("TELEGRAM_ALLOWED_USER_ID")

        if missing:
            raise ValueError(
                f"Variables de entorno requeridas no configuradas: {', '.join(missing)}"
            )

    # ── Derived properties ─────────────────────────────────────────────────────

    @property
    def is_bootstrap_mode(self) -> bool:
        """True when the owner has not yet set TELEGRAM_ALLOWED_USER_ID."""
        return self.telegram_allowed_user_id == 0

    @property
    def is_local(self) -> bool:
        return self.app_env != "production"

    @property
    def masked_token(self) -> str:
        """Return bot token with the secret part hidden — safe for logs."""
        token = self.telegram_bot_token
        if not token or ":" not in token:
            return "(not set)"
        bot_id, secret = token.split(":", 1)
        return f"{bot_id}:{'*' * min(len(secret), 8)}..."

    @property
    def masked_supabase_key(self) -> str:
        key = self.supabase_key
        if not key:
            return "(not set)"
        return key[:8] + "..." if len(key) > 8 else "***"

    @property
    def masked_api_key(self) -> str:
        key = self.api_football_key
        if not key:
            return "(not set)"
        return "..." + key[-4:] if len(key) >= 4 else "***"

    @property
    def markets_list(self) -> list[str]:
        return [m.strip() for m in self.default_markets.split(",") if m.strip()]

    @property
    def league_ids_list(self) -> list[int]:
        """Parse DEFAULT_LEAGUE_IDS into a list of ints. Empty if not configured."""
        return [
            int(x.strip())
            for x in self.default_league_ids.split(",")
            if x.strip().isdigit()
        ]

    @property
    def league_seasons_map(self) -> dict[int, int]:
        """Parse LEAGUE_SEASONS into a {league_id: season} dict.

        Format: "39:2025,140:2025,253:2026"
        """
        result: dict[int, int] = {}
        for entry in self.league_seasons.split(","):
            entry = entry.strip()
            if ":" not in entry:
                continue
            league_str, season_str = entry.split(":", 1)
            if league_str.strip().isdigit() and season_str.strip().isdigit():
                result[int(league_str.strip())] = int(season_str.strip())
        return result


settings = Settings()
