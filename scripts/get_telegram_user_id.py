#!/usr/bin/env python
"""Telegram user ID discovery tool.

Calls the Telegram Bot API directly (no PTB, no async) to show:
  - The bot's own information (getMe)
  - User IDs from recent messages (getUpdates)

Requires only TELEGRAM_BOT_TOKEN in .env. Does NOT require TELEGRAM_ALLOWED_USER_ID.

Usage:
    python scripts/get_telegram_user_id.py

Workflow:
    1. Run this script → copy your Telegram user ID from the output
    2. Set TELEGRAM_ALLOWED_USER_ID=<your_id> in .env
    3. Restart the bot

Exit codes:
    0 — success
    1 — error (TELEGRAM_BOT_TOKEN missing or API error)
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Resolve project root and load .env before anything else
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")


def _mask_token(token: str) -> str:
    if not token or ":" not in token:
        return "(invalid)"
    bot_id, secret = token.split(":", 1)
    return f"{bot_id}:{'*' * min(len(secret), 8)}..."


def _api_call(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        import urllib.parse
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def main() -> bool:
    print("\n=== get_telegram_user_id.py ===\n")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or "<COMPLETAR>" in token:
        print("✗ TELEGRAM_BOT_TOKEN no está configurado en .env")
        print("  Completa el token antes de ejecutar este script.")
        return False

    print(f"  Token: {_mask_token(token)}\n")

    # ── getMe ─────────────────────────────────────────────────────────────────
    print("[ 1 ] Información del bot (getMe)")
    try:
        me = _api_call(token, "getMe")
        if not me.get("ok"):
            print(f"  ✗ Respuesta inesperada: {me}")
            return False
        bot = me["result"]
        print(f"  ✓ Bot name:     {bot.get('first_name')}")
        print(f"  ✓ Bot username: @{bot.get('username')}")
        print(f"  ✓ Bot ID:       {bot.get('id')}")
    except RuntimeError as exc:
        print(f"  ✗ Error: {exc}")
        print("  Verifica que TELEGRAM_BOT_TOKEN sea válido.")
        return False

    # ── getUpdates ────────────────────────────────────────────────────────────
    print("\n[ 2 ] Mensajes recientes (getUpdates)")
    print("  Si no aparece nadie, envía cualquier mensaje al bot primero.\n")
    try:
        data = _api_call(token, "getUpdates", {"limit": 20, "allowed_updates": '["message"]'})
        updates = data.get("result", [])

        if not updates:
            print("  (sin mensajes recientes)")
            print("\n  → Abre Telegram → escribe /id al bot → vuelve a ejecutar este script.")
        else:
            seen: dict[int, dict] = {}
            for update in updates:
                msg = update.get("message", {})
                user = msg.get("from", {})
                uid = user.get("id")
                if uid and uid not in seen:
                    seen[uid] = user

            print(f"  Usuarios únicos encontrados: {len(seen)}\n")
            for uid, user in seen.items():
                username = f"@{user['username']}" if user.get("username") else "(sin username)"
                name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
                print(f"  ┌ User ID:   {uid}   ← usa este valor")
                print(f"  │ Nombre:    {name or '(no disponible)'}")
                print(f"  └ Username:  {username}\n")

            print("  Copia tu user ID y ponlo en .env:")
            print(f"  TELEGRAM_ALLOWED_USER_ID=<tu_id>")

    except RuntimeError as exc:
        print(f"  ✗ Error: {exc}")
        return False

    print("\n✅ Listo. Configura .env y reinicia el bot.")
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
