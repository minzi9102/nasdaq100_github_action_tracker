#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in [
    ROOT / "reports/latest/model_input_metrics.csv",
    ROOT / "reports/latest/price_metrics.csv",
    ROOT / "reports/latest/macro_daily.csv",
    ROOT / "reports/latest/macro_metrics.csv",
    ROOT / "reports/latest/nasdaq100_qqq_daily_tracker.xlsx",
    ROOT / "state/latest_manifest.json",
]:
    print(f"{path.relative_to(ROOT)}: {'exists' if path.exists() else 'missing'}")
