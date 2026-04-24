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

## Retención de datos

### Tablas permanentes (nunca se borran)

| Tabla | Razón |
|-------|-------|
| `competitions` | Catálogo de ligas |
| `competition_seasons` | Temporadas por liga |
| `teams` | Catálogo de equipos |
| `venues` | Estadios |
| `ref_bookmakers` | Referencia de bookmakers |
| `ref_bet_types` | Referencia de mercados |
| `tracked_competitions` | Configuración de sync por tier |
| `bot_users` | Usuarios autorizados del bot |

### Tablas operativas con TTL

| Tabla | TTL por defecto | Variable de entorno |
|-------|-----------------|---------------------|
| `fixtures` | 3 días | `RETENTION_FIXTURES_DAYS` |
| `odds_snapshots` | 3 días | `RETENTION_ODDS_DAYS` |
| `fixture_contexts` | 3 días | `RETENTION_CONTEXT_DAYS` |
| `pick_candidates` | 3 días (solo huérfanos) | `RETENTION_CANDIDATES_DAYS` |
| `published_picks` | 45 días | `RETENTION_PUBLISHED_PICKS_DAYS` |
| `pick_results` | 90 días (resueltos) | `RETENTION_SETTLEMENT_DAYS` |
| `api_sync_runs` | 14 días | `RETENTION_SYNC_RUNS_DAYS` |
| `api_usage_snapshots` | 14 días | `RETENTION_USAGE_DAYS` |
| `h2h_cache` | 30 días | `RETENTION_H2H_DAYS` |
| `team_competition_metrics` | 30 días | `RETENTION_TEAM_METRICS_DAYS` |
| `market_availability_cache` | 30 días | `RETENTION_MARKET_CACHE_DAYS` |
| `cron.job_run_details` | 14 días | `RETENTION_CRON_HISTORY_DAYS` |

> `pick_candidates` solo se borran cuando ya no tienen `published_picks` ni `pick_results` asociados.
> `pick_results` con `result_status = 'pending'` nunca se borran automáticamente.

### Frecuencia de limpieza

El cron nocturno corre una vez al día a las **03:10 Bogota (08:10 UTC)**.
No hay resets durante `sync_today.py` ni `sync_reference.py`.

---

### Pasos para activar la retención

#### 1. Aplicar migración 013

En **Supabase → SQL Editor**:

```sql
-- Copiar y pegar el contenido de:
sql/migrations/013_retention_cleanup.sql
```

Crea las funciones `cleanup_retention()` y `cleanup_retention_preview()`.

#### 2. Script manual

```bash
# Preview: ver cuántas filas se eliminarían (sin tocar nada)
python scripts/cleanup.py --dry-run

# Limpieza real
python scripts/cleanup.py
```

Los valores de TTL se leen automáticamente de `.env`.

#### 3. Cron nocturno (Supabase SQL Editor)

```sql
-- Copiar y pegar el contenido de:
sql/cron_schedule_cleanup.sql
```

Crea el job `football-bot-cleanup-nightly` que corre a las 08:10 UTC.

Verificar que el job está activo:
```sql
SELECT jobid, jobname, schedule, active FROM cron.job;
```

> pg_cron requiere que la extensión esté habilitada en tu proyecto Supabase.
> En Supabase Free, pg_cron puede no estar disponible — usa el script manual con Task Scheduler o cron local.

#### 4. Archivado local (opcional)

Exporta filas antiguas a Parquet en tu PC antes de borrarlas de Supabase.

```bash
# Instalar dependencia
pip install duckdb

# Activar en .env
LOCAL_ARCHIVE_ENABLED=true
LOCAL_ARCHIVE_DIR=./data/archive
LOCAL_ARCHIVE_BEFORE_DELETE=false   # true = borra de Supabase tras archivar

# Preview
python scripts/archive_local.py --dry-run

# Archivar
python scripts/archive_local.py
```

Archivos generados: `data/archive/<tabla>/<YYYY-MM-DD>.parquet`

---

### Qué revisar si Supabase sigue creciendo

1. Verificar que la migración 013 está aplicada:
   ```sql
   SELECT routine_name FROM information_schema.routines
   WHERE routine_name IN ('cleanup_retention', 'cleanup_retention_preview');
   ```

2. Ver cuántas filas hay por tabla:
   ```sql
   SELECT 'odds_snapshots', COUNT(*) FROM odds_snapshots
   UNION ALL SELECT 'fixture_contexts', COUNT(*) FROM fixture_contexts
   UNION ALL SELECT 'fixtures', COUNT(*) FROM fixtures
   UNION ALL SELECT 'pick_candidates', COUNT(*) FROM pick_candidates;
   ```

3. Revisar historial del cron:
   ```sql
   SELECT start_time, end_time, status, return_message
   FROM cron.job_run_details ORDER BY start_time DESC LIMIT 10;
   ```

4. Forzar cleanup manual:
   ```sql
   SELECT * FROM cleanup_retention();
   ```

---

### Borrar filas vs. reclamar espacio vs. VACUUM

| Operación | Qué hace | Cuándo |
|-----------|----------|--------|
| `DELETE` (cleanup) | Marca filas como borradas; el espacio **no** se libera inmediatamente | En cada cleanup |
| `VACUUM` | Reclama el espacio marcado; no bloquea lecturas | Automático en Supabase Free (autovacuum) |
| `VACUUM FULL` | Reescribe la tabla y devuelve espacio al SO; bloquea la tabla | Solo en emergencias, con cuidado |
| `ANALYZE` | Actualiza estadísticas del query planner | Automático o post-cleanup masivo |

En Supabase Free, **autovacuum está activo** — no necesitas correr `VACUUM` manualmente.
Después de borrados masivos, el autovacuum recupera el espacio en los siguientes minutos.

---

## Diagnóstico rápido

### Primer comando a ejecutar

Antes de cualquier otra cosa, verifica que las dos integraciones externas funcionen:

```bash
python scripts/check_supabase.py       # Verifica Supabase + schema
python scripts/check_api_football.py   # Verifica API-Football + credenciales
```

---

### Error: `Error/Missing application key`

Este error lo devuelve API-Football cuando la key es inválida, está vacía o falta en el header.

**Pasos para diagnosticar:**

1. Verifica que `.env` existe en la raíz del proyecto (al lado de `requirements.txt`):
   ```bash
   ls .env
   ```

2. Verifica que `API_FOOTBALL_KEY` tiene un valor real (no `<COMPLETAR>`):
   ```bash
   python -c "from dotenv import load_dotenv; load_dotenv(); import os; k=os.getenv('API_FOOTBALL_KEY',''); print('OK:', k[-4:] if len(k)>=4 else 'VACÍA')"
   ```

3. Ejecuta el check oficial:
   ```bash
   python scripts/check_api_football.py
   ```
   Debe mostrar `✓ Conectado correctamente`. Si muestra `✗`, la key en `.env` no es válida.

4. Si la key parece correcta pero falla: copia la key directamente desde
   [dashboard.api-football.com](https://dashboard.api-football.com) y pégala de nuevo en `.env`.
   Asegúrate de que no tenga espacios al inicio ni al final.

---

### Cómo validar que `.env` cargó correctamente

```bash
python -c "
from app.core.config import settings
print('API key (últimos 4):', settings.masked_api_key)
print('Supabase URL:', settings.supabase_url[:30] + '...')
print('Timezone:', settings.default_timezone)
"
```

Si aparece `(not set)` para algún campo, el `.env` no tiene esa variable o tiene el placeholder `<COMPLETAR>`.

---

### Cómo probar API-Football manualmente

```bash
# Sin fixtures de ejemplo (solo credenciales):
python scripts/check_api_football.py

# Con fixtures de una liga específica:
python scripts/check_api_football.py --league 39 --season 2025
```

---

### Cómo probar Supabase

```bash
python scripts/check_supabase.py
```

Verifica conexión, tablas requeridas y muestra conteos actuales.

---

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| `Error/Missing application key` | `API_FOOTBALL_KEY` inválida o vacía | Ver sección Diagnóstico arriba |
| `/hoy` sin picks | No se corrió Phase C o no hay odds | `sync_today.py` |
| Phase C: "tracked_competitions vacío" | No se corrió el seed | Ejecutar `seed_tracked_competitions.sql` |
| Phase B: "no hay ligas" | `DEFAULT_LEAGUE_IDS` vacío | Configurar en `.env` |
| Odds sin picks | Edge < `MIN_EDGE` o confianza < `MIN_CONFIDENCE` | Revisar thresholds en `.env` |
| `/estado` muestra schema faltante | Migración 010 no aplicada | Ejecutar en Supabase SQL Editor |
| `42P10` en `upsert_bet_types` | `on_conflict` usaba solo `provider_bet_id`; la constraint es `(provider_bet_id, scope)` | Corregido en código — sin migración necesaria |
