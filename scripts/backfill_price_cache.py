#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qqq_tracker.pipeline.cache_backfill import run_backfill  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill QQQ component historical price cache")
    parser.add_argument("--as-of", default="auto", help="Run date YYYY-MM-DD or auto")
    parser.add_argument("--max-calls", type=int, default=None, help="Override Tiingo max calls for this run")
    args = parser.parse_args()
    manifest = run_backfill(args.as_of, max_calls=args.max_calls)
    return 0 if manifest.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
