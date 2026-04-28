"""Handler for /start — welcome message and command overview."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth

logger = logging.getLogger(__name__)

_WELCOME = """\
⚽ <b>Football Picks Bot</b>

Pronósticos basados en modelo estadístico Poisson + Elo + Value Engine calibrado.

<b>Comandos principales</b>

/hoy — picks publicables del día (oficial)
/top — mejores candidatos por calidad, con sección de observados
/partido &lt;id o equipo&gt; — análisis completo de un partido concreto
/ligas — ligas activas por tier (paginado: /ligas 1 · /ligas 2 · /ligas 3)
/valor — métricas del Value Engine del último sync
/estado — salud del sistema: Supabase, API-Football, último sync

<b>Flujo recomendado</b>

1. Sincroniza datos del día:
   <code>python scripts/sync_today.py</code>
2. Verifica el sistema: /estado
3. Consulta picks: /top · /hoy
4. Analiza un partido: /partido &lt;equipo o ID&gt;
5. Revisa value engine: /valor

<b>Notas</b>

· Los picks son informativos. No son consejos de inversión.
· El Value Engine (VE) evalúa valor calibrado con probabilidades DuckDB.
  Cuando el VE y el modelo de cuotas divergen, prevalece el modelo de cuotas.
· /top muestra picks oficiales; si no hay, muestra candidatos en observación.
"""


@require_auth
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Comando /start desde user_id=%s", update.effective_user.id)
    await update.message.reply_text(_WELCOME, parse_mode="HTML")
