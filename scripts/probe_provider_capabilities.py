#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qqq_tracker.pipeline.capability_probe import run_capability_probe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe configured market-data provider capabilities")
    parser.add_argument("--as-of", default="auto", help="Run date YYYY-MM-DD or auto")
    args = parser.parse_args()
    manifest = run_capability_probe(args.as_of)
    return 0 if manifest.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
