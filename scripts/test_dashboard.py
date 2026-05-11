"""Smoke tests for the Phase 16 dashboard.

Tests cover: imports, safe helpers, connection guard, no-secrets leak,
missing table tolerance, formatters, components API surface.

Usage:
    python scripts/test_dashboard.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_passed = 0
_failed = 0
_skipped = 0

# Detect optional dependencies
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import streamlit  # noqa: F401
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False

try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


def ok(label: str) -> None:
    global _passed
    _passed += 1
    print(f"  PASS  {label}")


def fail(label: str, reason: str) -> None:
    global _failed
    _failed += 1
    snippet = repr(reason)[:200] if isinstance(reason, str) else repr(reason)[:200]
    print(f"  FAIL  {label} -- {snippet}")


def skip(label: str, reason: str = "") -> None:
    global _skipped
    _skipped += 1
    print(f"  SKIP  {label}" + (f" -- {reason}" if reason else ""))


def assert_true(label: str, condition: bool, reason: str = "") -> None:
    if condition:
        ok(label)
    else:
        fail(label, reason or "condition is False")


def assert_eq(label: str, actual, expected) -> None:
    if actual == expected:
        ok(label)
    else:
        fail(label, f"expected {expected!r}, got {actual!r}")


def assert_in(label: str, item, container) -> None:
    if item in container:
        ok(label)
    else:
        snippet = repr(container)[:120] if isinstance(container, str) else repr(container)
        fail(label, f"{item!r} not in {snippet}")


def group(name: str) -> None:
    print(f"\n[{name}]")


# ---------------------------------------------------------------------------
# 1. Core import: data_sources (needs pandas)
# ---------------------------------------------------------------------------
group("1. Import data_sources")
ds = None
if not HAS_PANDAS:
    skip("import data_sources", "pandas not installed")
elif not HAS_DUCKDB:
    skip("import data_sources", "duckdb not installed")
else:
    try:
        import app.dashboard.data_sources as ds
        ok("import data_sources")
    except Exception as exc:
        fail("import data_sources", str(exc))

group("2. Import formatters")
fmt_mod = None
try:
    import app.dashboard.formatters as fmt_mod
    ok("import formatters")
except Exception as exc:
    fail("import formatters", str(exc))

group("3. Import components")
comp_mod = None
if not HAS_STREAMLIT:
    skip("import components", "streamlit not installed")
elif not HAS_PANDAS:
    skip("import components", "pandas not installed")
else:
    try:
        import app.dashboard.components as comp_mod
        ok("import components")
    except Exception as exc:
        fail("import components", str(exc))

# ---------------------------------------------------------------------------
# 4. Pages import (needs streamlit)
# ---------------------------------------------------------------------------
group("4. Import pages")
_pages = [
    "overview", "picks", "performance", "governance", "bankroll",
    "parlays", "api_budget", "live_prematch", "entities_players", "system_health",
]
if not HAS_STREAMLIT:
    for _page in _pages:
        skip(f"  pages.{_page}", "streamlit not installed")
elif not HAS_PANDAS:
    for _page in _pages:
        skip(f"  pages.{_page}", "pandas not installed")
else:
    for _page in _pages:
        try:
            mod = __import__(f"app.dashboard.pages.{_page}", fromlist=["render"])
            assert_true(f"  pages.{_page} has render()", callable(getattr(mod, "render", None)))
        except Exception as exc:
            fail(f"  import pages.{_page}", str(exc))

# ---------------------------------------------------------------------------
# 5. DuckDB helpers with in-memory connection
# ---------------------------------------------------------------------------
group("5. safe_query and table_exists with in-memory DuckDB")
if ds is None:
    skip("duckdb helpers", "data_sources not imported")
elif not HAS_DUCKDB:
    skip("duckdb helpers", "duckdb not installed")
else:
    try:
        import duckdb as _ddb
        _conn = _ddb.connect(":memory:")
        _conn.execute("CREATE TABLE test_tbl (id INTEGER, val TEXT)")
        _conn.execute("INSERT INTO test_tbl VALUES (1, 'hello')")

        assert_true("table_exists true", ds.table_exists(_conn, "test_tbl"))
        assert_true("table_exists false for missing", not ds.table_exists(_conn, "nonexistent_xyz"))

        count = ds.get_table_count(_conn, "test_tbl")
        assert_eq("get_table_count", count, 1)

        count_missing = ds.get_table_count(_conn, "nonexistent_xyz")
        assert_true("get_table_count missing returns None or 0", count_missing in (None, 0))

        _conn.close()
    except Exception as exc:
        fail("duckdb helpers", str(exc))

# ---------------------------------------------------------------------------
# 6. safe_df returns empty DataFrame on bad query
# ---------------------------------------------------------------------------
group("6. safe_df returns empty DataFrame on bad SQL")
if ds is None or not HAS_PANDAS or not HAS_DUCKDB:
    skip("safe_df graceful error handling", "missing dependencies")
else:
    try:
        import duckdb as _ddb2
        _conn2 = _ddb2.connect(":memory:")
        result = ds.safe_df(_conn2, "SELECT * FROM definitely_does_not_exist")
        assert_true("safe_df is DataFrame", isinstance(result, pd.DataFrame))
        assert_true("safe_df is empty", result.empty)
        _conn2.close()
        ok("safe_df graceful error handling")
    except Exception as exc:
        fail("safe_df graceful error handling", str(exc))

# ---------------------------------------------------------------------------
# 7. safe_query returns default on bad query
# ---------------------------------------------------------------------------
group("7. safe_query returns default on bad SQL")
if ds is None or not HAS_DUCKDB:
    skip("safe_query graceful error handling", "missing dependencies")
else:
    try:
        import duckdb as _ddb3
        _conn3 = _ddb3.connect(":memory:")
        result = ds.safe_query(_conn3, "SELECT * FROM no_such_table", [], None)
        assert_true("safe_query returns None default", result is None)
        result2 = ds.safe_query(_conn3, "SELECT * FROM no_such_table", [], [])
        assert_true("safe_query returns [] default", result2 == [])
        _conn3.close()
    except Exception as exc:
        fail("safe_query graceful error handling", str(exc))

# ---------------------------------------------------------------------------
# 8. get_config_summary masks secrets
# ---------------------------------------------------------------------------
group("8. get_config_summary masks secrets")
if ds is None:
    skip("get_config_summary", "data_sources not imported")
else:
    try:
        config = ds.get_config_summary()
        assert_true("get_config_summary returns dict", isinstance(config, dict))
        if "telegram_token" in config:
            val = config["telegram_token"]
            assert_true("telegram_token masked", "****" in str(val) or str(val) == "--")
        ok("config secrets check complete")
    except Exception as exc:
        fail("get_config_summary", str(exc))

# ---------------------------------------------------------------------------
# 9. get_config_summary does not expose raw secrets
# ---------------------------------------------------------------------------
group("9. No raw secrets in config values")
if ds is None:
    skip("no raw secrets check", "data_sources not imported")
else:
    try:
        import os
        config = ds.get_config_summary()
        raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if raw_token and len(raw_token) > 8:
            for key, val in config.items():
                assert_true(
                    f"config[{key!r}] does not contain raw token",
                    raw_token not in str(val),
                )
        else:
            ok("no TELEGRAM_BOT_TOKEN set -- skipping raw token check")
    except Exception as exc:
        fail("no raw secrets check", str(exc))

# ---------------------------------------------------------------------------
# 10–17. Formatters (no external deps)
# ---------------------------------------------------------------------------
if fmt_mod is not None:
    group("10. fmt_pct")
    assert_eq("fmt_pct(0.5)", fmt_mod.fmt_pct(0.5), "50.0%")
    assert_eq("fmt_pct(None)", fmt_mod.fmt_pct(None), "N/A")
    assert_eq("fmt_pct(1.0)", fmt_mod.fmt_pct(1.0), "100.0%")

    group("11. fmt_roi")
    assert_eq("fmt_roi(0.1)", fmt_mod.fmt_roi(0.1), "+10.00%")
    assert_eq("fmt_roi(-0.05)", fmt_mod.fmt_roi(-0.05), "-5.00%")
    assert_eq("fmt_roi(None)", fmt_mod.fmt_roi(None), "N/A")

    group("12. fmt_float")
    assert_eq("fmt_float(3.14)", fmt_mod.fmt_float(3.14), "3.14")
    assert_eq("fmt_float(None)", fmt_mod.fmt_float(None), "N/A")

    group("13. fmt_int")
    assert_eq("fmt_int(1000)", fmt_mod.fmt_int(1000), "1,000")
    assert_eq("fmt_int(None)", fmt_mod.fmt_int(None), "N/A")

    group("14. badge_recommendation")
    assert_in("badge SAFE_TO_USE", "SAFE TO USE", fmt_mod.badge_recommendation("SAFE_TO_USE_FOR_SELECTION"))
    assert_in("badge DO_NOT_ACTIVATE", "DO NOT ACTIVATE", fmt_mod.badge_recommendation("DO_NOT_ACTIVATE"))
    assert_in("badge None", "N/A", fmt_mod.badge_recommendation(None))

    group("15. traffic_light")
    assert_eq("traffic_light green", fmt_mod.traffic_light(0.8, 0.7, 0.3), "🟢")
    assert_eq("traffic_light red", fmt_mod.traffic_light(0.1, 0.7, 0.3), "🔴")
    assert_eq("traffic_light yellow", fmt_mod.traffic_light(0.5, 0.7, 0.3), "🟡")
    assert_eq("traffic_light None", fmt_mod.traffic_light(None, 0.7, 0.3), "⚪")

    group("16. mask_secret")
    assert_eq("mask_secret short", fmt_mod.mask_secret("abc"), "****")
    assert_in("mask_secret long", "****", fmt_mod.mask_secret("abcdef123456"))

    group("17. truncate")
    assert_eq("truncate short", fmt_mod.truncate("hello"), "hello")
    long_str = "a" * 50
    assert_true("truncate long ends with ellipsis", fmt_mod.truncate(long_str, 20).endswith("...") or fmt_mod.truncate(long_str, 20).endswith("…"))
else:
    for g in ["10", "11", "12", "13", "14", "15", "16", "17"]:
        group(f"{g}. (formatters)")
        skip("formatters tests", "formatters not imported")

# ---------------------------------------------------------------------------
# 18. gate_bar (needs components)
# ---------------------------------------------------------------------------
group("18. gate_bar")
if comp_mod is not None:
    bar = comp_mod.gate_bar(7, 10)
    assert_true("gate_bar has blocks", "█" in bar)
    assert_true("gate_bar has fraction", "7/10" in bar)
    assert_eq("gate_bar 0/0", comp_mod.gate_bar(0, 0), "N/A")
elif not HAS_STREAMLIT:
    skip("gate_bar", "streamlit not installed")
else:
    skip("gate_bar", "components not imported")

# ---------------------------------------------------------------------------
# 19. Script files exist
# ---------------------------------------------------------------------------
group("19. Script files")
_scripts = ["run_dashboard.py", "audit_dashboard.py", "test_dashboard.py"]
for _s in _scripts:
    path = ROOT / "scripts" / _s
    assert_true(f"scripts/{_s} exists", path.exists())

group("20. Dashboard module files")
_mods = [
    "app/dashboard/__init__.py",
    "app/dashboard/main.py",
    "app/dashboard/data_sources.py",
    "app/dashboard/formatters.py",
    "app/dashboard/components.py",
]
for _m in _mods:
    assert_true(f"{_m} exists", (ROOT / _m).exists())

group("21. Dashboard page files")
for _page in _pages:
    path = ROOT / "app" / "dashboard" / "pages" / f"{_page}.py"
    assert_true(f"pages/{_page}.py exists", path.exists())

# ---------------------------------------------------------------------------
# 21. get_recent_rows with missing order column
# ---------------------------------------------------------------------------
group("22. get_recent_rows with missing order_col")
if ds is None or not HAS_DUCKDB or not HAS_PANDAS:
    skip("get_recent_rows missing order_col", "missing dependencies")
else:
    try:
        import duckdb as _ddb4
        _conn4 = _ddb4.connect(":memory:")
        _conn4.execute("CREATE TABLE sample (id INTEGER, val TEXT)")
        _conn4.execute("INSERT INTO sample VALUES (1, 'a'), (2, 'b')")
        result = ds.get_recent_rows(_conn4, "sample", limit=10, order_col="nonexistent_col")
        assert_true("get_recent_rows returns DataFrame", isinstance(result, pd.DataFrame))
        assert_true("get_recent_rows returns rows", len(result) > 0)
        _conn4.close()
    except Exception as exc:
        fail("get_recent_rows missing order_col", str(exc))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total = _passed + _failed
print(f"\n{'=' * 50}")
print(f"  Results: {_passed}/{total} passed  ({_skipped} skipped)")
if _failed > 0:
    print(f"  FAILED: {_failed}")
    sys.exit(1)
else:
    print("  All tests passed.")
print(f"{'=' * 50}")
