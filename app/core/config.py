import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


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

    # Sync configuration — optional; required only when running sync_today.py.
    # Comma-separated API-Football league IDs (e.g. "39,140" for PL + La Liga).
    default_league_ids: str = os.getenv("DEFAULT_LEAGUE_IDS", "")
    # Four-digit season year (e.g. 2025). 0 means not configured.
    default_season: int = _int("DEFAULT_SEASON", 0)

    def __post_init__(self) -> None:
        missing: list[str] = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_allowed_user_id:
            missing.append("TELEGRAM_ALLOWED_USER_ID")
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_key:
            missing.append("SUPABASE_KEY")
        if not self.api_football_key:
            missing.append("API_FOOTBALL_KEY")
        if missing:
            raise ValueError(
                f"Variables de entorno requeridas no configuradas: {', '.join(missing)}"
            )

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


settings = Settings()
