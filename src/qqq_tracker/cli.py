from __future__ import annotations

import argparse

from qqq_tracker.pipeline import daily_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Nasdaq-100 / QQQ daily tracker")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "refresh-target", "report"], help="Pipeline stage to run")
    parser.add_argument("--as-of", default="auto", help="Report date YYYY-MM-DD or auto")
    args = parser.parse_args()
    if args.command == "refresh-target":
        daily_run.run_target_refresh(args.as_of)
    elif args.command == "report":
        daily_run.run_report(args.as_of)
    else:
        daily_run.run_daily(args.as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
