"""Launch the Streamlit dashboard.

Usage:
    python scripts/run_dashboard.py [--port PORT] [--browser]

Examples:
    python scripts/run_dashboard.py
    python scripts/run_dashboard.py --port 8502
    python scripts/run_dashboard.py --port 8501 --browser
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the football-bot local dashboard")
    parser.add_argument("--port", type=int, default=8501, help="Port to run on (default: 8501)")
    parser.add_argument("--browser", action="store_true", help="Auto-open browser")
    args = parser.parse_args()

    dashboard = ROOT / "app" / "dashboard" / "main.py"
    if not dashboard.exists():
        print(f"ERROR: dashboard not found at {dashboard}", file=sys.stderr)
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(dashboard),
        "--server.port", str(args.port),
        "--server.headless", "true" if not args.browser else "false",
        "--browser.gatherUsageStats", "false",
    ]

    print(f"Starting dashboard on http://localhost:{args.port}")
    print(f"Command: {' '.join(cmd)}")
    print("Press Ctrl+C to stop.")

    try:
        subprocess.run(cmd, cwd=str(ROOT), check=True)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    except FileNotFoundError:
        print("ERROR: streamlit not found. Run: pip install streamlit", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
