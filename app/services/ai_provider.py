"""Phase 9: External AI provider abstraction for the conversational router.

Supports:
  provider=rules  — pure local rules, no external API needed (default)
  provider=openai — optional, reads OPENAI_API_KEY; falls back to rules on failure

All providers must return the standard intent contract:
  {
    "intent": str,
    "confidence": float,
    "args": {...},
    "needs_clarification": bool,
    "clarification_question": str | None,
    "safety_note": str | None,
  }
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Eres un asistente de análisis de fútbol. Tu única tarea es clasificar el mensaje \
del usuario y devolver un JSON con este contrato exacto:
{
  "intent": "<intent_key>",
  "confidence": <0.0-1.0>,
  "args": {
    "fixture_id": null,
    "team_query": null,
    "player_query": null,
    "parlay_legs": null,
    "risk_level": null,
    "days": null
  },
  "needs_clarification": false,
  "clarification_question": null,
  "safety_note": null
}

Intents válidos: top_picks, today_picks, fixture_analysis, team_lookup,
player_lookup, league_list, system_status, value_metrics, results,
performance, parlay, parlay_risk, live, follow_fixture, help, unknown.

Responde SOLO con JSON válido, sin texto adicional.
"""

_EMPTY_RESULT = {
    "intent": "unknown",
    "confidence": 0.0,
    "args": {
        "fixture_id": None,
        "team_query": None,
        "player_query": None,
        "parlay_legs": None,
        "risk_level": None,
        "days": None,
    },
    "needs_clarification": False,
    "clarification_question": None,
    "safety_note": None,
}


def call_openai_provider(text: str, model: str, timeout: int, max_tokens: int) -> dict | None:
    """Call OpenAI API to classify the message. Returns None on any failure."""
    import os
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.debug("ai_provider: OPENAI_API_KEY not set, cannot use openai provider")
        return None

    try:
        import urllib.request
        payload = json.dumps({
            "model": model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        content = raw["choices"][0]["message"]["content"].strip()
        # Strip markdown code blocks if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = json.loads(content)
        return _validate_contract(result)

    except Exception as exc:
        logger.warning("ai_provider: openai call failed — %s", exc)
        return None


def _validate_contract(raw: dict) -> dict | None:
    """Validate and normalize the provider's JSON against the contract."""
    if not isinstance(raw, dict):
        return None
    intent = raw.get("intent", "")
    if not isinstance(intent, str) or not intent:
        return None

    _VALID_INTENTS = {
        "top_picks", "today_picks", "fixture_analysis", "team_lookup",
        "player_lookup", "league_list", "system_status", "value_metrics",
        "results", "performance", "parlay", "parlay_risk", "live",
        "follow_fixture", "help", "unknown",
    }
    if intent not in _VALID_INTENTS:
        return None

    args_raw = raw.get("args") or {}
    return {
        "intent": intent,
        "confidence": float(raw.get("confidence") or 0.5),
        "args": {
            "fixture_id":   args_raw.get("fixture_id"),
            "team_query":   args_raw.get("team_query"),
            "player_query": args_raw.get("player_query"),
            "parlay_legs":  args_raw.get("parlay_legs"),
            "risk_level":   args_raw.get("risk_level"),
            "days":         args_raw.get("days"),
        },
        "needs_clarification": bool(raw.get("needs_clarification", False)),
        "clarification_question": raw.get("clarification_question"),
        "safety_note": raw.get("safety_note"),
    }


def classify_with_external_provider(text: str) -> dict | None:
    """Attempt classification with the configured external provider.

    Returns a validated result dict, or None if unavailable/failed.
    The caller should fall back to the rules engine on None.
    """
    from app.core.config import settings

    if settings.ai_router_provider != "openai":
        return None

    return call_openai_provider(
        text=text,
        model=settings.ai_router_model,
        timeout=settings.ai_router_timeout_seconds,
        max_tokens=settings.ai_router_max_tokens,
    )
