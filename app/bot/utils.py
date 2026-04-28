"""Shared utilities for bot handlers.

Provides:
  esc()       — HTML-escape dynamic content for parse_mode="HTML" messages.
  send_html() — Send an HTML message safely: auto-splits > 4096 chars,
                falls back to plain text if Telegram rejects the HTML.
"""

from __future__ import annotations

import html
import logging
import re

from telegram import Message

logger = logging.getLogger(__name__)

_MAX_LEN = 4096


def esc(text: str | int | float | None) -> str:
    """Escape a string for Telegram HTML mode.

    Call on every piece of dynamic content (team names, league names,
    user-provided strings) before embedding it in an HTML message.
    """
    if text is None:
        return ""
    return html.escape(str(text))


async def send_html(message: Message, text: str) -> None:
    """Send text with parse_mode='HTML', splitting and falling back gracefully.

    Splits at paragraph boundaries when text exceeds 4096 chars.
    If Telegram returns Bad Request (malformed HTML), retries as plain text.
    """
    for chunk in _split(text):
        try:
            await message.reply_text(chunk, parse_mode="HTML")
        except Exception as exc:
            logger.warning("HTML send failed (%s) — degradando a texto plano", exc)
            try:
                await message.reply_text(_plain(chunk))
            except Exception as exc2:
                logger.error("Plain-text send also failed: %s", exc2)


def _split(text: str, max_len: int = _MAX_LEN) -> list[str]:
    """Split text into chunks of at most max_len, preferring paragraph breaks."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while len(text) > max_len:
        cut = text.rfind("\n\n", 0, max_len)
        if cut == -1:
            cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


def _plain(text: str) -> str:
    """Strip HTML tags and unescape entities for plain-text fallback."""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = clean.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return clean
