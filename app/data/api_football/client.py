import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class APIFootballClient:
    def __init__(self) -> None:
        self.base_url = settings.api_football_base_url
        self._headers = {"x-apisports-key": settings.api_football_key}

    async def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        logger.debug("API-Football GET %s | params=%s", path, params)
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=30.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP %s al llamar API-Football %s", exc.response.status_code, path
            )
            raise
        except httpx.TimeoutException:
            logger.error("Timeout al llamar API-Football %s", path)
            raise

        data: dict = response.json()

        # API-Football returns HTTP 200 even on errors; check the errors field.
        if data.get("errors"):
            logger.error("Error de API-Football en %s: %s", path, data["errors"])
            raise RuntimeError(f"API-Football error en {path}: {data['errors']}")

        results = data.get("results", 0)
        logger.debug("API-Football %s → %d resultados", path, results)
        return data


api_client = APIFootballClient()
