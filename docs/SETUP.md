# Setup — football-bot v2

Guía de configuración completa para dejar el bot operativo desde cero.

---

## Requisitos

- Python 3.11 o superior (desarrollado con Python 3.13)
- Cuenta en [Supabase](https://supabase.com) (plan gratuito es suficiente)
- Cuenta en [API-Football](https://www.api-football.com) (plan gratuito: 100 req/día)
- Una cuenta de Telegram y acceso a [@BotFather](https://t.me/BotFather)

---

## Arquitectura de sync (4 fases)

| Fase | Script | Cuándo ejecutar |
|------|--------|-----------------|
| A — Reference | `sync_reference.py --phase a` | Una sola vez (o cuando cambien bookmakers) |
| B — Bootstrap | `sync_reference.py --phase b` | Una vez por temporada, por liga |
| C — Daily | `sync_today.py` | Cada mañana (fixtures + contexto + odds) |
| D — Prematch | `sync_prematch.py --window 120` | ~2h antes del KO |

---

## Orden de bootstrap (primer arranque)

> Sigue exactamente este orden. Cada paso depende del anterior.

```
1.  Entorno Python + pip install
2.  Configurar .env
3.  Aplicar migraciones SQL en Supabase (001 → 002 → 003)
4.  Arrancar bot en modo bootstrap → obtener Telegram user ID
5.  Actualizar .env con TELEGRAM_ALLOWED_USER_ID y ligas
6.  Fase A: sync_reference.py --phase a
7.  Fase B: sync_reference.py --phase b --leagues 39:2025,140:2025,...
8.  Fase C: sync_today.py  (fixtures + contexto + odds)
9.  Arrancar bot definitivo: python run.py
10. Probar /estado /hoy /top /partido /ligas en Telegram
```

---

## Paso 1 — Entorno Python

```bash
# Crear y activar virtualenv
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (CMD)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

---

## Paso 2 — Configurar .env

```bash
cp .env.example .env
```

Abre `.env` y completa todos los campos marcados con `<COMPLETAR>`:

| Variable | De dónde obtenerla |
|----------|--------------------|
| `TELEGRAM_BOT_TOKEN` | @BotFather → /newbot |
| `SUPABASE_URL` | Supabase → Project Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase → Project Settings → API → service_role (secret) |
| `API_FOOTBALL_KEY` | api-football.com → Dashboard → Credenciales |
| `DEFAULT_LEAGUE_IDS` | IDs de ligas separados por coma (ej. `39,140,253`) |
| `LEAGUE_SEASONS` | Mapa liga:temporada (ej. `39:2025,140:2025,253:2026`) |

**Deja `TELEGRAM_ALLOWED_USER_ID=0` por ahora** — el bot arrancará en modo bootstrap para ayudarte a descubrir tu ID numérico.

> Usa la `service_role` key de Supabase, **NO la `anon` key**.

### Variables opcionales

| Variable | Default | Descripción |
|----------|---------|-------------|
| `APP_ENV` | `local` | `local` \| `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `DEFAULT_TIMEZONE` | `America/Bogota` | Zona horaria IANA |
| `DEFAULT_MARKETS` | `1X2,OU25,BTTS` | Mercados habilitados |
| `MAX_DAILY_PICKS` | `3` | Máximo picks publicables por día |
| `MIN_EDGE` | `0.05` | Edge mínimo para publicar (5%) |
| `MIN_CONFIDENCE` | `0.60` | Probabilidad mínima del modelo (60%) |
| `PREFERRED_BOOKMAKER` | _(vacío)_ | Nombre exacto del bookmaker preferido (ej. `Bet365`) |
| `PREFERRED_BOOKMAKER_ID` | `0` | ID numérico del bookmaker (0 = sin preferencia) |
| `DEFAULT_SEASON` | `0` | Temporada de respaldo para ligas no en LEAGUE_SEASONS |

### IDs de ligas comunes

| Liga | ID |
|------|----|
| Premier League | 39 |
| La Liga | 140 |
| Serie A | 135 |
| Bundesliga | 78 |
| Ligue 1 | 61 |
| Liga BetPlay (Colombia) | 253 |

---

## Paso 3 — Crear el bot en BotFather

1. Abre Telegram → busca **@BotFather**
2. Envía `/newbot`
3. Elige nombre y username (debe terminar en `bot`)
4. Copia el token → ponlo en `.env` como `TELEGRAM_BOT_TOKEN`

---

## Paso 4 — Aplicar migraciones SQL

Las migraciones se aplican **manualmente** en el SQL Editor de Supabase.
La API REST no permite ejecutar DDL directamente.

**Orden obligatorio:**

1. Supabase → **SQL Editor** → New query
2. Pega `sql/migrations/001_init.sql` → **Run**
3. Nueva query → pega `sql/migrations/002_constraints_and_indexes.sql` → **Run**
4. Nueva query → pega `sql/migrations/003_api_football_v2.sql` → **Run**

> Las sentencias usan `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` — son idempotentes y pueden ejecutarse varias veces sin daño.

La migración 003 añade:
- Columnas a `leagues`: `coverage`, `season_start`, `season_end`, `current`, `type`
- Columnas a `fixtures`: `timezone`, `date_local`, `status_short`, `status_long`, `elapsed`, `venue_id`, `last_api_update`, `context_json`
- Columnas a `odds_snapshots`: `bookmaker_id`, `bet_id`, `scope`, `last_update`
- Tablas nuevas: `ref_bookmakers`, `ref_bet_types`, `api_sync_runs`, `api_usage_snapshots`

---

## Paso 5 — Obtener tu Telegram user ID

```bash
python run.py
# El bot arranca con un banner MODO BOOTSTRAP en los logs
# Abre Telegram → envía /id al bot → copia el número
# Detén el bot (Ctrl+C)
# Edita .env: TELEGRAM_ALLOWED_USER_ID=<número>
```

---

## Paso 6 — Fase A: sync de referencia

Sincroniza bookmakers y tipos de apuesta desde la API.

```bash
python scripts/sync_reference.py --phase a
```

Salida esperada:
```
[Phase A] Syncing reference data...
[Phase A] Bookmakers upserted: 42
[Phase A] Bet types upserted: 89
[Phase A] Done.
```

---

## Paso 7 — Fase B: bootstrap de cobertura

Descarga la metadata de cobertura de cada liga. Necesario antes del sync diario para que el sistema sepa qué endpoints tiene disponibles (standings, injuries, odds, predictions).

```bash
python scripts/sync_reference.py --phase b --leagues 39:2025,140:2025,253:2026
```

El argumento `--leagues` acepta el mismo formato que `LEAGUE_SEASONS` en `.env`.
Si omites `--leagues`, usa el valor de `LEAGUE_SEASONS` del `.env`.

```bash
# Ejecutar ambas fases de una vez:
python scripts/sync_reference.py --phase ab --leagues 39:2025,140:2025,253:2026
```

---

## Paso 8 — Fase C: sync diario

Descarga fixtures del día, construye el contexto de cada partido (standings, estadísticas, lesiones, predicción del proveedor, h2h) y las cuotas.

```bash
python scripts/sync_today.py
```

Salida esperada:
```
[Phase C] Syncing daily data for 2025-04-14...
[Phase C] League 39 (Premier League 2025): 6 fixtures
[Phase C] League 140 (La Liga 2025): 4 fixtures
[Phase C] Done. Fixtures: 10, Odds rows: 312
```

---

## Paso 9 — Arrancar el bot

```bash
python run.py
```

El bot:
1. Valida las variables de entorno (falla rápido si faltan valores)
2. Verifica el schema de la DB (bloquea comandos si faltan tablas críticas)
3. Registra los comandos en Telegram
4. Empieza a escuchar mensajes

### Probar localmente

Abre Telegram → busca tu bot → envía:

| Comando | Descripción |
|---------|-------------|
| `/estado` | **Empieza aquí** — muestra DB, cuota API, último sync |
| `/id` | Tu user ID (sin autenticación, útil en bootstrap) |
| `/start` | Lista de comandos |
| `/hoy` | Picks publicables del día |
| `/top` | Top picks por confianza |
| `/partido 1060362` | Análisis completo de un fixture por ID |
| `/partido Atletico` | Busca partido de hoy por nombre de equipo |
| `/ligas` | Lista de ligas activas con cobertura |

---

## Sync prematch (Fase D)

Refresca odds y lineups ~2 horas antes del kickoff.

```bash
# Todos los fixtures que arrancan en las próximas 2 horas:
python scripts/sync_prematch.py --window 120

# Un fixture específico:
python scripts/sync_prematch.py --fixture 1060362
```

---

## Qué muestra /estado en cada fase del bootstrap

### Migraciones no aplicadas
```
Estado del sistema

Telegram:           OK
Supabase:           OK
Schema:             NO APLICADO
  Faltantes: fixtures  predictions  ref_bookmakers  ...

Aplica las migraciones en Supabase → SQL Editor:
  sql/migrations/001_init.sql
  sql/migrations/002_constraints_and_indexes.sql
  sql/migrations/003_api_football_v2.sql
```

### Schema OK, sin datos
```
Estado del sistema

Telegram:           OK
Supabase:           OK
Schema:             OK

Fixtures hoy:       0
Cuotas:             0
Picks publicables:  0

API: 97/100 req restantes hoy

Sin datos para hoy. Ejecuta:
  python scripts/sync_today.py
```

### Todo operativo
```
Estado del sistema

Telegram:           OK
Supabase:           OK
Schema:             OK

Fixtures hoy:       10
Cuotas:             312
Picks publicables:  3

API: 63/100 req restantes hoy
Último sync: daily  2025-04-14 08:30 UTC  (10 fixtures, 312 odds)
```

---

## Correr tests

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
# o en macOS/Linux:
.venv/bin/python -m pytest tests/ -v
```

Resultado esperado: **67 passed**.

Los tests cubren:
- `tests/test_config.py` — validación de Settings
- `tests/test_endpoints_parsing.py` — parsing de respuestas de API-Football
- `tests/test_formatter.py` — formato de mensajes Telegram
- `tests/test_odds_predictor.py` — modelo de consenso de cuotas
- `tests/test_pick_filter.py` — filtro de picks

---

## Referencia rápida de scripts

```bash
# Fase A — reference data (bookmakers, bet types)
python scripts/sync_reference.py --phase a

# Fase B — bootstrap de cobertura por liga/temporada
python scripts/sync_reference.py --phase b --leagues 39:2025,140:2025,253:2026

# Fases A+B juntas
python scripts/sync_reference.py --phase ab --leagues 39:2025,140:2025,253:2026

# Fase C — sync diario (fixtures + contexto + odds)
python scripts/sync_today.py

# Fase D — sync prematch (odds + lineups cerca del KO)
python scripts/sync_prematch.py --window 120
python scripts/sync_prematch.py --fixture <provider_fixture_id>

# Arrancar bot
python run.py

# Tests
.venv/Scripts/python.exe -m pytest tests/ -v
```

---

## Diagnóstico de errores frecuentes

### `PGRST205 — Could not find the table 'public.ref_bookmakers'`
**Causa:** La migración 003 no se aplicó.
**Solución:**
1. Supabase → SQL Editor → pega `sql/migrations/003_api_football_v2.sql` → Run
2. Reinicia el bot

### Bot arranca en MODO BOOTSTRAP
**Causa:** `TELEGRAM_ALLOWED_USER_ID` es `0`.
**Solución:** Envía `/id` al bot → copia el número → actualiza `.env` → reinicia.

### `ValueError: TELEGRAM_BOT_TOKEN no puede estar vacío`
**Causa:** Falta completar `.env`.
**Solución:** Abre `.env`, busca `<COMPLETAR>` y rellena todos los valores.

### `/hoy` responde "La base de datos no está inicializada"
**Causa:** Tablas faltantes (probablemente migración 003).
**Solución:** Aplica las tres migraciones en orden y reinicia el bot.

### `/hoy` responde "No hay picks publicables para hoy"
**Causa:** No se ha ejecutado el sync diario o no hay partidos con edge suficiente.
**Solución:** `python scripts/sync_today.py`

### Fase B no encuentra cobertura
**Causa:** El ID de liga o la temporada son incorrectos.
**Solución:** Verifica en API-Football que la liga existe en esa temporada. Para Colombia usa `253:2026`.

### Cuota API agotada (100 req/día en plan gratuito)
**Causa:** Demasiadas ligas o fixtures para el plan gratuito.
**Solución:** Reduce `DEFAULT_LEAGUE_IDS` a 1-2 ligas mientras pruebas. La fase C usa ~3-5 req por fixture.

---

## Notas de seguridad

- `.env` está en `.gitignore` — nunca lo subas al repo
- Usa la `service_role` key de Supabase, nunca la `anon` key
- El acceso al bot es exclusivo: solo `TELEGRAM_ALLOWED_USER_ID` puede usarlo (excepto `/id` en modo bootstrap)
- Los logs nunca imprimen keys ni tokens en claro (siempre enmascarados)
- En producción: `APP_ENV=production` rechaza el bot si `TELEGRAM_ALLOWED_USER_ID=0`

---

## Modelo de predicción v2

El modelo combina **consenso de cuotas** con **señales de contexto**:

**Paso 1 — OddsPredictor (señal de mercado):**
1. Recopila odds de todos los bookmakers disponibles
2. Por bookmaker: calcula probabilidades justas eliminando el margen (overround)
3. Promedia → `model_probability`
4. Compara contra la mejor cuota → `edge`

**Paso 2 — MatchContextScorer (ajuste contextual, ±5pp máx):**
- Standings: posición y puntos en tabla
- Forma reciente: últimos 5 partidos (W=1, D=0.5, L=0)
- Estadísticas: goles promedio, clean sheets, fallos de anotación
- H2H: historial de enfrentamientos directo desde la DB
- Lesiones: jugadores clave fuera
- Predicción del proveedor: porcentajes de API-Football

**Paso 3 — Filtro:**
Publica si: `edge ≥ MIN_EDGE` Y `model_probability ≥ MIN_CONFIDENCE` Y `picks_hoy < MAX_DAILY_PICKS`
