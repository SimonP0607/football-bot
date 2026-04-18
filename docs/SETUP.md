# Setup — football-bot

Bot privado de picks pre-match con API-Football Pro + Supabase + Telegram.

---

## Requisitos

- Python 3.11+
- Cuenta [Supabase](https://supabase.com)
- Cuenta [API-Football](https://www.api-football.com) — **plan Pro o superior**
- Telegram + [@BotFather](https://t.me/BotFather)

---

## Arquitectura de base de datos

| Capa | Tablas principales | Retención |
|------|--------------------|-----------|
| A — Catálogo | `competitions`, `competition_seasons`, `tracked_competitions`, `teams`, `ref_bookmakers`, `ref_bet_types` | Permanente |
| B — Hot | `fixtures`, `fixture_contexts`, `odds_snapshots`, `pick_candidates`, `published_picks`, `api_sync_runs`, `api_usage_snapshots` | 7–14 días (odds/contextos) |
| C — Analítico | `team_competition_metrics`, `h2h_cache`, `market_availability_cache` | Recomputado |

**Relación clave:** `fixtures.league_id → competition_seasons.id` (no al ID de `competitions`).

---

## Niveles de sync (sync_tier)

| Tier | Valor | Comportamiento |
|------|-------|----------------|
| 1 | `tier_1_daily` | Standings + enrichment completo todos los días |
| 2 | `tier_2_matchday` | Enrichment completo solo cuando hay fixtures |
| 3 | `tier_3_light` | Solo fixtures + odds (sin standings, stats, injuries) |

---

## Instalación inicial

```bash
# 1. Clonar y configurar entorno
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 2. Configurar variables
cp .env.example .env
# Editar .env con tus credenciales
```

---

## Base de datos (Supabase)

En **Supabase → SQL Editor**, ejecutar en orden:

```sql
-- Paso 1: schema principal
sql/migrations/010_production_schema.sql

-- Paso 2: sync_tier (si ya tenías la 010 aplicada)
sql/migrations/011_sync_tier.sql
```

> Las migraciones 001–004 son históricas. Instalaciones nuevas solo necesitan 010 + 011.

---

## Flujo de ejecución desde cero

### Paso 1 — Phase A + B: datos de referencia y coverage

```bash
# Configura DEFAULT_LEAGUE_IDS en .env con todos los IDs que quieras rastrear
# tier_1: 2,3,39,40,61,71,78,88,94,98,128,135,140,144,239
# tier_2: 11,13,16,17,22,32,34,45,62,66,73,79,81,103,106,113,119,130,137,141,143,241
# tier_3: 29,30,31,95,96,108,115,121,147

python scripts/sync_reference.py
```

Esto llama `/leagues?current=true`, guarda coverage en `competition_seasons`,
y popula `ref_bookmakers` y `ref_bet_types`.

### Paso 2 — Seed: configurar qué ligas rastrear y con qué tier

En **Supabase → SQL Editor**:

```sql
sql/seed_tracked_competitions.sql
```

Solo funciona para ligas que ya pasaron por Phase B. Idempotente — se puede re-ejecutar.

### Paso 3 — Phase C: sync diario

```bash
python scripts/sync_today.py
```

Lee `tracked_competitions` para saber qué ligas sincronizar. Aplica tier-awareness:
- T1: standings + enrichment completo
- T2: equal a T1 solo si hay fixtures
- T3: fixtures + odds, sin enrichment

### Paso 4 — Bot

```bash
python -m app.main
```

Enviar `/id` para descubrir el Telegram user ID, luego configurar `TELEGRAM_ALLOWED_USER_ID` en `.env`.

### Paso 5 — Phase D (opcional, prematch)

```bash
# Correr 60-90 min antes del kickoff
python scripts/sync_prematch.py --window 90
```

---

## Comandos del bot

| Comando | Descripción |
|---------|-------------|
| `/start` | Menú de ayuda |
| `/ligas` | Ligas activas, tier, coverage |
| `/hoy` | Picks publicables del día |
| `/top` | Top 5 picks por confianza |
| `/partido <id o equipo>` | Análisis completo de un partido |
| `/estado` | Estado del sistema, cuota API, último sync |

---

## Flujo de ejecución diario (producción)

```
07:00  sync_reference.py --phase b   (opcional, 1× por semana)
08:00  sync_today.py                 (Phase C: fixtures + odds + picks)
12:00  sync_today.py                 (re-sync si hubo cambios de odds)
T-90m  sync_prematch.py --window 90  (odds frescos + lineups)
```

---

## Limpieza de retención

```bash
python scripts/cleanup.py --dry-run    # ver qué se eliminaría
python scripts/cleanup.py              # ejecutar limpieza real
```

Política por defecto: odds → 7 días, fixture_contexts → 14 días, sync_runs → 90 días.

---

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| `/hoy` sin picks | No se corrió Phase C o no hay odds | `sync_today.py` |
| Phase C: "tracked_competitions vacío" | No se corrió el seed | Ejecutar `seed_tracked_competitions.sql` |
| Phase B: "no hay ligas" | `DEFAULT_LEAGUE_IDS` vacío | Configurar en `.env` |
| Odds sin picks | Edge < `MIN_EDGE` o confianza < `MIN_CONFIDENCE` | Revisar thresholds en `.env` |
| `/estado` muestra schema faltante | Migración 010 no aplicada | Ejecutar en Supabase SQL Editor |
