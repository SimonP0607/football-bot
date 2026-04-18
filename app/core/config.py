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
    max_daily_picks: int = _int("MAX_DAILY_PICKS", 3)
    min_edge: float = _float("MIN_EDGE", 0.05)
    min_confidence: float = _float("MIN_CONFIDENCE", 0.60)

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

    def __post_init__(self) -> None:
        missing: list[str] = []

        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_key:
            missing.append("SUPABASE_KEY")
        if not self.api_football_key:
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
        return key[:6] + "..." if len(key) > 6 else "***"

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
