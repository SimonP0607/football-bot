"""Centralized API-Football v3 HTTP client.

Responsibilities:
  - Attach x-apisports-key header to every request.
  - Parse and validate the standard response wrapper {get, parameters, errors, results, paging, response}.
  - Handle 204 (no content), 429 (rate limit), and provider-level errors[].
  - Retry up to MAX_RETRIES times on transient failures (429, timeout).
  - Track rate-limit headers in memory (available via get_rate_limit_state()).
  - Provide get_paginated() async generator for paginated endpoints.

What this client does NOT do:
  - Persist rate-limit data to the database (that is sync_runs_repo's job).
  - Know anything about fixtures, leagues, or business logic.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import AsyncGenerator

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Per-call hook (optional, registered externally to avoid circular imports) ──
# Signature: hook(endpoint: str, duration_ms: int, status_code: int, results: int)
_call_hook: Callable | None = None


def register_call_hook(fn: Callable) -> None:
    """Register a callback invoked after every successful API response.

    The hook receives (endpoint, duration_ms, status_code, results_count).
    Hook failures are swallowed so they never break an API call.
    """
    global _call_hook
    _call_hook = fn


# ── In-memory rate-limit state (updated from every response) ──────────────────
# Reflects the most recently observed headers; reset to None on startup.
_rate_state: dict = {
    "requests_limit": None,      # x-ratelimit-requests-limit  (daily quota)
    "requests_remaining": None,  # x-ratelimit-requests-remaining
    "minute_limit": None,        # x-ratelimit-limit  (per-minute quota)
    "minute_remaining": None,    # x-ratelimit-remaining
    "last_endpoint": None,
    "last_updated_at": None,
}


def get_rate_limit_state() -> dict:
    """Return a snapshot of the last known API rate-limit state.

    Returns None for each field until the first API call is made.
    """
    return dict(_rate_state)


class APIFootballError(Exception):
    """Raised when the API response body contains a non-empty errors[] field."""

    def __init__(self, endpoint: str, errors: dict | list) -> None:
        self.endpoint = endpoint
        self.errors = errors
        super().__init__(f"API-Football error on {endpoint}: {errors}")


class APIFootballClient:
    """Async HTTP client for API-Football v3."""

    MAX_RETRIES = 2
    RETRY_DELAY_429 = 5.0   # seconds to wait after a 429
    RETRY_DELAY_TIMEOUT = 2.0

    def __init__(self) -> None:
        self._base_url = settings.api_football_base_url
        key = settings.api_football_key
        masked = ("..." + key[-4:]) if len(key) >= 4 else "***"
        logger.debug(
            "API-Football client inicializado — key=%s base_url=%s", masked, self._base_url
        )
        self._headers = {
            "x-apisports-key": key,
            "Accept": "application/json",
        }

    # ── Public interface ──────────────────────────────────────────────────────

    async def get(self, path: str, params: dict | None = None) -> dict:
        """Make a single GET request and return the full API-Football response dict.

        Handles:
          - 204 No Content → returns an empty but valid response wrapper.
          - 429 Too Many Requests → retries up to MAX_RETRIES with a short delay.
          - errors[] in body → raises APIFootballError (never exposes the API key).
          - Rate-limit headers → updates the in-memory state on every call.

        Raises:
            APIFootballError: Provider returned a non-empty errors[] payload.
            httpx.HTTPStatusError: Unrecoverable HTTP error (4xx/5xx).
            httpx.TimeoutException: Request timed out after all retries.
        """
        url = f"{self._base_url}{path}"
        logger.debug("API-Football GET %s | params=%s", path, params)

        for attempt in range(1, self.MAX_RETRIES + 2):
            _t0 = time.monotonic()
            try:
                async with httpx.AsyncClient(
                    headers=self._headers, timeout=30.0
                ) as client:
                    response = await client.get(url, params=params)

                # Update rate-limit state from every response (even errors)
                self._update_rate_state(response, path)

                # 204 = valid empty response (league has no fixtures today, etc.)
                if response.status_code == 204:
                    logger.debug("API-Football %s → 204 No Content", path)
                    return _empty_wrapper(path, params)

                # 429 = rate limited → retry with delay
                if response.status_code == 429:
                    if attempt <= self.MAX_RETRIES:
                        logger.warning(
                            "API-Football 429 en %s — reintentando en %.0fs (intento %d/%d)",
                            path, self.RETRY_DELAY_429, attempt, self.MAX_RETRIES,
                        )
                        await asyncio.sleep(self.RETRY_DELAY_429)
                        continue
                    logger.error("API-Football 429 persistente en %s — abortando", path)
                    response.raise_for_status()

                response.raise_for_status()

            except httpx.HTTPStatusError:
                raise
            except httpx.TimeoutException:
                logger.warning(
                    "Timeout al llamar API-Football %s (intento %d/%d)",
                    path, attempt, self.MAX_RETRIES + 1,
                )
                if attempt <= self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY_TIMEOUT)
                    continue
                raise

            # Parse JSON body
            try:
                data: dict = response.json()
            except Exception:
                logger.error("API-Football %s: cuerpo no es JSON válido", path)
                raise

            # Check provider-level errors (API always returns HTTP 200, errors in body)
            errors = data.get("errors")
            if errors:
                safe_errors = _sanitize_errors(errors)
                if _is_auth_error(safe_errors):
                    logger.error(
                        "Error de autenticación en API-Football (%s): %s\n"
                        "  → API_FOOTBALL_KEY en .env es inválida, está vacía o tiene espacios.\n"
                        "  → Verifica ejecutando: python scripts/check_api_football.py",
                        path, safe_errors,
                    )
                else:
                    logger.error("Error de API-Football en %s: %s", path, safe_errors)
                raise APIFootballError(path, safe_errors)

            results = data.get("results", 0)
            logger.debug("API-Football %s → %d resultado(s)", path, results)

            # Fire per-call hook (budget logging, etc.) — never raises
            if _call_hook is not None:
                try:
                    duration_ms = int((time.monotonic() - _t0) * 1000)
                    _call_hook(path, duration_ms, response.status_code, results)
                except Exception:
                    pass

            return data

        raise RuntimeError(f"API-Football: máximo de reintentos superado para {path}")

    async def get_paginated(
        self,
        path: str,
        params: dict | None = None,
    ) -> AsyncGenerator[list, None]:
        """Async generator that yields each page's response[] list.

        Automatically follows paging.current / paging.total to fetch all pages.
        Use this for endpoints that may return many results (e.g. /odds).

        Args:
            path: API endpoint path (e.g. "/odds").
            params: Query parameters; a ``page`` key will be injected automatically.

        Yields:
            The ``response`` list from each page.
        """
        params = dict(params or {})
        page = 1
        while True:
            params["page"] = page
            data = await self.get(path, params)
            items: list = data.get("response", [])
            yield items

            paging = data.get("paging", {})
            current = paging.get("current", 1)
            total = paging.get("total", 1)
            if current >= total or not items:
                break
            page += 1

    # ── Private helpers ────────────────────────────────────────────────────────

    def _update_rate_state(self, response: httpx.Response, endpoint: str) -> None:
        """Parse rate-limit headers and store them in the module-level state dict."""
        import datetime
        h = response.headers
        _rate_state["requests_limit"] = _safe_int(h.get("x-ratelimit-requests-limit"))
        _rate_state["requests_remaining"] = _safe_int(
            h.get("x-ratelimit-requests-remaining")
        )
        _rate_state["minute_limit"] = _safe_int(h.get("x-ratelimit-limit"))
        _rate_state["minute_remaining"] = _safe_int(h.get("x-ratelimit-remaining"))
        _rate_state["last_endpoint"] = endpoint
        _rate_state["last_updated_at"] = datetime.datetime.utcnow().isoformat()

        remaining = _rate_state["requests_remaining"]
        if remaining is not None and remaining < 20:
            logger.warning(
                "API-Football cuota BAJA: %d llamadas diarias restantes", remaining
            )


# ── Module-level helpers ──────────────────────────────────────────────────────


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _sanitize_errors(errors: dict | list) -> dict | list:
    """Strip any field that looks like it could contain a credential."""
    if isinstance(errors, dict):
        return {k: v for k, v in errors.items() if "key" not in k.lower()}
    return errors


def _is_auth_error(errors: dict | list) -> bool:
    """Return True if errors indicate a missing/invalid API key."""
    text = str(errors).lower()
    return "application key" in text or (
        isinstance(errors, dict) and "token" in errors
    )


def _empty_wrapper(path: str, params: dict | None) -> dict:
    return {
        "get": path,
        "parameters": params or {},
        "errors": [],
        "results": 0,
        "paging": {"current": 1, "total": 1},
        "response": [],
    }


# ── Singleton ─────────────────────────────────────────────────────────────────
api_client = APIFootballClient()
