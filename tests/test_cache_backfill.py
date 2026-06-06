from types import SimpleNamespace

import pandas as pd

from qqq_tracker.pipeline.cache_backfill import CACHE_QUALITY_COLUMNS, backfill_price_cache, prepare_backfill_holdings
from qqq_tracker.pipeline.daily_run import API_USAGE_COLUMNS
from qqq_tracker.providers.base import ProviderResult


def make_settings(tmp_path, holdings):
    processed_dir = tmp_path / "processed"
    latest_dir = tmp_path / "latest"
    raw_dir = tmp_path / "raw"
    cache_dir = tmp_path / "cache"
    state_dir = tmp_path / "state"
    for path in [processed_dir / "2026-06-05", latest_dir, raw_dir, cache_dir, state_dir]:
        path.mkdir(parents=True, exist_ok=True)
    holdings.to_csv(latest_dir / "qqq_equity_holdings.csv", index=False)
    return SimpleNamespace(
        api_limits={"tiingo": {"hourly_requests": 50, "max_calls_per_run": 40}},
        paths=SimpleNamespace(
            root=tmp_path,
            processed_dir=processed_dir,
            reports_latest_dir=latest_dir,
            raw_dir=raw_dir,
            tiingo_price_cache_dir=cache_dir,
            state_dir=state_dir,
        ),
    )


def price_frame(symbol, start="2025-01-01", rows=220, first=100.0):
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=rows, freq="B").astype(str),
            "adjClose": [first + i for i in range(rows)],
            "symbol": [symbol] * rows,
            "source": ["tiingo"] * rows,
        }
    )


class FakeTiingo:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def daily_prices(self, symbol, start_date=None, end_date=None):
        self.calls.append((symbol, start_date, end_date))
        response = self.responses.get(symbol)
        if isinstance(response, str) and response == "429":
            return ProviderResult("tiingo", False, pd.DataFrame(), "429 received", {"rate_limited": True, "retry_after_seconds": 3600})
        if isinstance(response, pd.DataFrame):
            return ProviderResult("tiingo", True, response, f"{symbol}: {len(response)} rows", {"rate_limited": False})
        return ProviderResult("tiingo", False, pd.DataFrame(), "failed", {"rate_limited": False})


def test_prepare_backfill_holdings_sorts_by_weight():
    holdings = pd.DataFrame(
        [
            {"symbol": "MSFT", "weight": 0.2},
            {"symbol": "AAPL", "weight": 0.8},
        ]
    )

    prepared = prepare_backfill_holdings(holdings)

    assert prepared["symbol"].tolist() == ["AAPL", "MSFT"]


def test_complete_cache_is_not_requested(tmp_path):
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.8}])
    settings = make_settings(tmp_path, holdings)
    price_frame("AAPL", rows=220).to_csv(settings.paths.tiingo_price_cache_dir / "AAPL.csv", index=False)
    tiingo = FakeTiingo({"AAPL": price_frame("AAPL", rows=220)})

    cache_quality, api_usage, _ = backfill_price_cache(settings, tiingo, "2026-06-05", max_calls=40)

    assert tiingo.calls == []
    assert list(cache_quality.columns) == CACHE_QUALITY_COLUMNS
    assert bool(cache_quality.loc[0, "is_complete"]) is True
    assert bool(cache_quality.loc[0, "was_requested"]) is False
    assert list(api_usage.columns) == API_USAGE_COLUMNS
    assert api_usage.loc[0, "calls_attempted"] == 0


def test_incomplete_cache_requests_by_weight_and_respects_max_calls(tmp_path):
    holdings = pd.DataFrame(
        [
            {"symbol": "LOW", "weight": 0.1},
            {"symbol": "HIGH", "weight": 0.9},
            {"symbol": "MID", "weight": 0.5},
        ]
    )
    settings = make_settings(tmp_path, holdings)
    tiingo = FakeTiingo(
        {
            "HIGH": price_frame("HIGH", rows=220),
            "MID": price_frame("MID", rows=220),
            "LOW": price_frame("LOW", rows=220),
        }
    )

    cache_quality, api_usage, _ = backfill_price_cache(settings, tiingo, "2026-06-05", max_calls=2)

    assert [call[0] for call in tiingo.calls] == ["HIGH", "MID"]
    assert api_usage.loc[0, "calls_attempted"] == 2
    assert api_usage.loc[0, "calls_success"] == 2
    skipped = cache_quality[cache_quality["symbol"] == "LOW"].iloc[0]
    assert bool(skipped["was_requested"]) is False
    assert skipped["message"] == "skipped after max_calls limit"


def test_rate_limit_stops_later_symbols(tmp_path):
    holdings = pd.DataFrame(
        [
            {"symbol": "HIGH", "weight": 0.9},
            {"symbol": "MID", "weight": 0.5},
            {"symbol": "LOW", "weight": 0.1},
        ]
    )
    settings = make_settings(tmp_path, holdings)
    tiingo = FakeTiingo({"HIGH": "429", "MID": price_frame("MID", rows=220)})

    cache_quality, api_usage, _ = backfill_price_cache(settings, tiingo, "2026-06-05", max_calls=40)

    assert [call[0] for call in tiingo.calls] == ["HIGH"]
    assert bool(api_usage.loc[0, "rate_limited"]) is True
    assert bool(api_usage.loc[0, "stopped_after_429"]) is True
    assert api_usage.loc[0, "retry_after_seconds"] == 3600
    skipped = cache_quality[cache_quality["symbol"] == "MID"].iloc[0]
    assert bool(skipped["rate_limited"]) is True
    assert skipped["message"] == "skipped after Tiingo 429"


def test_successful_backfill_merges_sorts_and_deduplicates_cache(tmp_path):
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.8}])
    settings = make_settings(tmp_path, holdings)
    pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-03"],
            "adjClose": [100.0, 101.0],
            "symbol": ["AAPL", "AAPL"],
            "source": ["tiingo", "tiingo"],
        }
    ).to_csv(settings.paths.tiingo_price_cache_dir / "AAPL.csv", index=False)
    live = pd.DataFrame(
        {
            "date": ["2025-01-03", "2025-01-06"],
            "adjClose": [101.5, 102.0],
            "symbol": ["AAPL", "AAPL"],
            "source": ["tiingo", "tiingo"],
        }
    )
    tiingo = FakeTiingo({"AAPL": live})

    cache_quality, _, _ = backfill_price_cache(settings, tiingo, "2026-06-05", max_calls=1)
    cached = pd.read_csv(settings.paths.tiingo_price_cache_dir / "AAPL.csv")

    assert cached["date"].tolist() == ["2025-01-02", "2025-01-03", "2025-01-06"]
    assert cached["adjClose"].tolist() == [100.0, 101.5, 102.0]
    assert cache_quality.loc[0, "after_rows"] == 3
