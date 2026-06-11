from types import SimpleNamespace

import pandas as pd

from qqq_tracker import cli
from qqq_tracker.pipeline.daily_run import (
    PRICE_DAILY_COLUMNS,
    load_report_price_inputs,
    validate_cache_target_alignment,
)


def price_frame(symbol, end="2026-06-10", rows=220):
    return pd.DataFrame(
        {
            "date": pd.bdate_range(end=end, periods=rows).astype(str),
            "adjusted_close": [100.0 + i for i in range(rows)],
            "symbol": [symbol] * rows,
            "source": ["test"] * rows,
        }
    )


def settings(tmp_path):
    latest_dir = tmp_path / "latest"
    cache_dir = tmp_path / "cache" / "prices"
    latest_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    return SimpleNamespace(paths=SimpleNamespace(root=tmp_path, reports_latest_dir=latest_dir, price_cache_dir=cache_dir, tiingo_price_cache_dir=cache_dir))


def test_report_price_inputs_use_latest_price_daily_target(tmp_path):
    cfg = settings(tmp_path)
    price_daily = price_frame("QQQ", end="2026-06-10")
    price_daily.to_csv(cfg.paths.reports_latest_dir / "price_daily.csv", index=False)
    pd.DataFrame([{"symbol": "QQQ", "date": "2026-06-10", "latest_close": 319.0}]).to_csv(
        cfg.paths.reports_latest_dir / "price_metrics.csv",
        index=False,
    )

    loaded_daily, loaded_metrics, target_date, source = load_report_price_inputs(cfg)

    assert list(loaded_daily.columns) == PRICE_DAILY_COLUMNS
    assert target_date == "2026-06-10"
    assert source == "reports/latest/price_daily.csv"
    assert loaded_metrics.loc[0, "symbol"] == "QQQ"


def test_report_cache_alignment_fails_when_tiingo_cache_lags_target(tmp_path):
    cfg = settings(tmp_path)
    price_frame("AAPL", end="2026-06-09").to_csv(cfg.paths.price_cache_dir / "AAPL.csv", index=False)
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.7}])

    try:
        validate_cache_target_alignment(cfg, holdings, "2026-06-11", "2026-06-10")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected stale cache to fail report alignment")

    assert "target_date=2026-06-10" in message
    assert "AAPL: latest=2026-06-09" in message


def test_report_cache_alignment_passes_when_cache_matches_target(tmp_path):
    cfg = settings(tmp_path)
    price_frame("AAPL", end="2026-06-10").to_csv(cfg.paths.price_cache_dir / "AAPL.csv", index=False)
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.7}])

    validate_cache_target_alignment(cfg, holdings, "2026-06-11", "2026-06-10")


def test_cli_dispatches_daily_stages(monkeypatch):
    calls = []

    monkeypatch.setattr(cli.daily_run, "run_daily", lambda as_of: calls.append(("run", as_of)))
    monkeypatch.setattr(cli.daily_run, "run_target_refresh", lambda as_of: calls.append(("refresh-target", as_of)))
    monkeypatch.setattr(cli.daily_run, "run_report", lambda as_of: calls.append(("report", as_of)))
    monkeypatch.setattr("sys.argv", ["run_daily.py", "refresh-target", "--as-of", "2026-06-11"])
    assert cli.main() == 0
    monkeypatch.setattr("sys.argv", ["run_daily.py", "report", "--as-of", "2026-06-12"])
    assert cli.main() == 0
    monkeypatch.setattr("sys.argv", ["run_daily.py"])
    assert cli.main() == 0

    assert calls == [("refresh-target", "2026-06-11"), ("report", "2026-06-12"), ("run", "auto")]
