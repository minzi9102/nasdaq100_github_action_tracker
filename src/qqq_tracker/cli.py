from __future__ import annotations

import argparse

from qqq_tracker.pipeline.daily_run import run_daily


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Nasdaq-100 / QQQ daily tracker")
    parser.add_argument("run", nargs="?", default="run", help="Command, currently only 'run'")
    parser.add_argument("--as-of", default="auto", help="Report date YYYY-MM-DD or auto")
    args = parser.parse_args()
    if args.run != "run":
        raise SystemExit(f"Unknown command: {args.run}")
    run_daily(args.as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
