#!/usr/bin/env python
"""Diagnóstico de configuración del Value Engine.

Lee el .env directamente, muestra os.environ y el objeto settings.
Read-only. No escribe nada.

Usage:
    python scripts/check_value_engine_config.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_ENV  = _ROOT / ".env"
sys.path.insert(0, str(_ROOT))

SEP = "=" * 64


def _yn(v: bool) -> str:
    return "SI" if v else "NO"


def main() -> None:
    print(f"\n{SEP}")
    print("  CHECK VALUE ENGINE CONFIG")
    print(SEP)

    # ── 1. Archivo .env (lectura directa, sin cargar en os.environ) ───────────
    print(f"\n  ARCHIVO .env")
    print(f"  Ruta : {_ENV}")
    print(f"  Existe: {_yn(_ENV.exists())}")

    if not _ENV.exists():
        print("\n  ERROR: El archivo .env no existe en la ruta esperada.")
        print(f"  Crea .env copiando .env.example y completa los valores.")
        print(f"\n{SEP}\n")
        sys.exit(1)

    try:
        from dotenv import dotenv_values
        raw = dotenv_values(_ENV)
        ve_raw = {k: v for k, v in raw.items() if "VALUE_ENGINE" in k}
        if ve_raw:
            print(f"\n  Contenido VALUE_ENGINE en .env (dotenv_values):")
            for k, v in ve_raw.items():
                print(f"    {k} = {repr(v)}")
        else:
            print(
                "\n  ADVERTENCIA: ninguna clave VALUE_ENGINE encontrada en .env.\n"
                "  Agrega al final de .env:\n"
                "    VALUE_ENGINE_ENABLED=true\n"
                "    VALUE_ENGINE_MODE=shadow"
            )
    except Exception as exc:
        print(f"  Error leyendo .env con dotenv_values: {exc}")

    # ── 2. os.environ ANTES de importar config ────────────────────────────────
    print(f"\n  OS.ENVIRON (antes de importar config.py)")
    _ve_keys = [
        "VALUE_ENGINE_ENABLED",
        "VALUE_ENGINE_MODE",
        "VALUE_ENGINE_LOCAL_DB_PATH",
    ]
    for k in _ve_keys:
        v = os.environ.get(k)
        tag = " ← ya existía en env, puede interferir" if v is not None else ""
        print(f"    {k} = {repr(v) if v is not None else '<no establecido>'}{tag}")

    # ── 3. Importar config (dispara load_dotenv con override=True) ────────────
    try:
        from app.core.config import settings
    except Exception as exc:
        print(f"\n  ERROR importando settings: {exc}")
        print(f"\n{SEP}\n")
        sys.exit(1)

    # ── 4. Valores del objeto settings ────────────────────────────────────────
    print(f"\n  SETTINGS (tras load_dotenv + dataclass)")
    print(f"    value_engine_enabled          = {settings.value_engine_enabled}")
    print(f"    value_engine_mode             = {repr(settings.value_engine_mode)}")
    print(f"    value_engine_local_db_path    = {repr(settings.value_engine_local_db_path)}")
    print(f"    value_engine_min_quality      = {settings.value_engine_min_quality}")
    print(f"    value_engine_min_edge         = {settings.value_engine_min_edge}")
    print(f"    value_engine_min_ev_adj       = {settings.value_engine_min_ev_adj}")
    print(f"    value_engine_require_odds     = {settings.value_engine_require_odds}")
    print(f"    value_engine_max_picks_per_league = {settings.value_engine_max_picks_per_league}")
    print(f"    value_engine_fallback_to_current  = {settings.value_engine_fallback_to_current}")

    # ── 5. Mismatch detection ─────────────────────────────────────────────────
    try:
        from dotenv import dotenv_values
        raw = dotenv_values(_ENV)
        file_enabled = (raw.get("VALUE_ENGINE_ENABLED") or "false").strip().lower()
        file_mode    = (raw.get("VALUE_ENGINE_MODE") or "off").strip()
        if file_enabled == "true" and not settings.value_engine_enabled:
            print(
                f"\n  CONFIG MISMATCH:\n"
                f"    .env tiene VALUE_ENGINE_ENABLED=true\n"
                f"    pero settings.value_engine_enabled = False\n"
                f"    Causa: la variable ya estaba en os.environ con valor anterior.\n"
                f"    Solución: verifica que load_dotenv usa override=True en app/core/config.py"
            )
        if file_mode != settings.value_engine_mode:
            print(
                f"\n  CONFIG MISMATCH:\n"
                f"    .env tiene VALUE_ENGINE_MODE={file_mode!r}\n"
                f"    pero settings.value_engine_mode = {settings.value_engine_mode!r}"
            )
    except Exception:
        pass

    # ── 6. DuckDB ─────────────────────────────────────────────────────────────
    db_path = Path(settings.value_engine_local_db_path)
    if not db_path.is_absolute():
        db_path = _ROOT / db_path
    print(f"\n  DUCKDB")
    print(f"    Ruta resuelta : {db_path}")
    db_ok = db_path.exists()
    print(f"    Existe        : {_yn(db_ok)}")
    if db_ok:
        size_mb = db_path.stat().st_size / 1_048_576
        print(f"    Tamaño        : {size_mb:.1f} MB")

    # ── 7. Conclusión ─────────────────────────────────────────────────────────
    print(f"\n  CONCLUSIÓN")
    mode_active = (
        settings.value_engine_enabled
        and settings.value_engine_mode in ("shadow", "assist")
    )

    if not settings.value_engine_enabled:
        print("  VALUE_ENGINE_ENABLED = false → motor INACTIVO")
        print("  Agrega VALUE_ENGINE_ENABLED=true a tu .env para activar.")
        verdict = "INACTIVO — config faltante"
    elif settings.value_engine_mode not in ("shadow", "assist"):
        print(f"  VALUE_ENGINE_ENABLED=true pero VALUE_ENGINE_MODE={settings.value_engine_mode!r}")
        print("  Cambia VALUE_ENGINE_MODE=shadow o assist para activar.")
        verdict = "INACTIVO — modo inválido"
    elif not db_ok:
        print("  Motor activado en configuración pero DuckDB no encontrado.")
        print("  El pipeline continuará sin value engine (fallback seguro).")
        verdict = "BLOQUEADO — DuckDB faltante"
    else:
        print(f"  Motor ACTIVO en modo {settings.value_engine_mode!r}.")
        if settings.value_engine_mode == "shadow":
            print("  En shadow: evalúa candidatos, guarda métricas, NO cambia picks.")
        elif settings.value_engine_mode == "assist":
            print("  En assist: filtra/reordena candidatos por value score.")
        verdict = f"LISTO para shadow real (modo={settings.value_engine_mode!r})"

    print(f"\n  Veredicto: {verdict}")
    print(f"\n{SEP}\n")

    sys.exit(0 if mode_active and db_ok else 1)


if __name__ == "__main__":
    main()
