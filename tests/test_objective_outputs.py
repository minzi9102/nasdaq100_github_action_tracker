import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from qqq_tracker.pipeline.daily_run import (
    API_USAGE_COLUMNS,
    BREADTH_CONSTITUENTS_COLUMNS,
    BREADTH_METRICS_COLUMNS,
    DATA_QUALITY_COLUMNS,
    PRICE_DAILY_COLUMNS,
    QQQ_EQUITY_HOLDINGS_COLUMNS,
    QQQ_HOLDINGS_COLUMNS,
    TOP_HOLDINGS_QUOTES_COLUMNS,
    api_usage_row,
    build_breadth_constituents,
    build_breadth_metrics,
    build_equity_holdings,
    build_macro_metric_rows,
    build_top_holdings_quotes,
    fetch_breadth,
    fetch_qqq_price_history,
    load_price_cache,
    merge_price_history,
    normalize_price_daily,
    quality_row,
    fetch_twelve_data_quotes,
    summarize_price,
    top_holdings_quote_quality,
    write_price_cache,
)
from qqq_tracker.pipeline.report_builder import MODEL_INPUT_COLUMNS, build_model_input_metrics, build_model_input_metrics_v2
from qqq_tracker.providers.base import APIError, ProviderResult, RateLimitError
from qqq_tracker.providers.fmp import FMPProvider
from qqq_tracker.providers.invesco import InvescoProvider


class FakeTwelveDataQuote:
    available = True

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def quote(self, symbol):
        self.calls.append(symbol)
        response = self.responses.get(symbol)
        if response == "429":
            return ProviderResult("twelve_data", False, pd.DataFrame(), "429 received", {"rate_limited": True, "retry_after_seconds": 120})
        if isinstance(response, dict):
            return ProviderResult("twelve_data", True, pd.DataFrame([{**response, "symbol": symbol, "source": "twelve_data"}]), "quote rows=1", {})
        return ProviderResult("twelve_data", False, pd.DataFrame(), "failed", {"rate_limited": False})


def test_price_metrics_do_not_include_signal_fields():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=220, freq="B").astype(str),
            "adjusted_close": range(100, 320),
        }
    )

    row = summarize_price("QQQ", df, "test_source")

    assert set(row) == {
        "symbol",
        "source",
        "date",
        "latest_close",
        "return_20d",
        "return_60d",
        "vol_20d",
        "current_drawdown",
        "max_drawdown",
        "ma_50",
        "ma_200",
    }
    assert not any(key.startswith("signal_") for key in row)


def test_macro_metrics_are_objective_fields_only():
    fred_frames = {
        "DGS10": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=30, freq="B").astype(str), "value": [4.0 + i * 0.01 for i in range(30)]}),
        "DGS2": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=30, freq="B").astype(str), "value": [3.8 + i * 0.01 for i in range(30)]}),
        "CPIAUCSL": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=6, freq="MS").astype(str), "value": [300, 301, 302, 303, 304, 305]}),
        "PCEPI": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=6, freq="MS").astype(str), "value": [120, 121, 122, 123, 124, 125]}),
        "UNRATE": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=6, freq="MS").astype(str), "value": [4.0, 4.1, 4.1, 4.2, 4.2, 4.3]}),
    }

    rows = build_macro_metric_rows(fred_frames)
    metrics = pd.DataFrame(rows)

    assert set(metrics.columns) == {"metric_name", "metric_value", "unit_or_method", "data_date", "source"}
    assert {"status", "threshold", "direction"}.isdisjoint(metrics.columns)
    assert "DGS2_DGS10_SPREAD" in set(metrics["metric_name"])


def test_model_input_metrics_columns_are_fixed():
    price_metrics = pd.DataFrame(
        [
            {
                "symbol": "QQQ",
                "source": "test_source",
                "date": "2025-12-31",
                "latest_close": 100.0,
                "return_20d": 0.01,
                "return_60d": 0.02,
                "vol_20d": 0.15,
                "current_drawdown": -0.03,
                "max_drawdown": -0.10,
                "ma_50": 98.0,
                "ma_200": 95.0,
            }
        ]
    )
    macro_daily = pd.DataFrame(
        [
            {
                "series_id": "DGS10",
                "name": "US 10Y",
                "latest_date": "2025-12-31",
                "latest_value": 4.5,
                "source": "FRED",
            }
        ]
    )
    macro_metrics = pd.DataFrame(
        [
            {
                "metric_name": "DGS10_1M_CHANGE",
                "metric_value": 0.1,
                "unit_or_method": "current minus previous",
                "data_date": "2025-12-31",
                "source": "FRED",
            }
        ]
    )
    fmp_summary = pd.DataFrame([{"symbol": "AAPL", "ok": True, "rows": 1, "message": "ok"}])

    model_input = build_model_input_metrics(price_metrics, macro_daily, macro_metrics, fmp_summary)

    assert list(model_input.columns) == MODEL_INPUT_COLUMNS
    assert MODEL_INPUT_COLUMNS == [
        "metric_name",
        "metric_value",
        "metric_date",
        "source",
        "provider",
        "coverage_ratio",
        "is_missing",
        "quality_message",
    ]
    latest_close = model_input[model_input["metric_name"] == "QQQ_latest_close"].iloc[0]
    assert latest_close["source"] == "price_metrics"
    assert latest_close["provider"] == "test_source"
    assert pd.isna(latest_close["coverage_ratio"])
    macro_value = model_input[model_input["metric_name"] == "DGS10_latest_value"].iloc[0]
    assert macro_value["source"] == "macro_daily"
    assert macro_value["provider"] == "FRED"
    fmp_coverage = model_input[model_input["metric_name"] == "fmp_quote_available_ratio"].iloc[0]
    assert fmp_coverage["source"] == "fmp_summary"
    assert fmp_coverage["provider"] == "fmp"
    assert fmp_coverage["coverage_ratio"] == 1.0


def test_price_daily_standard_columns_and_sorting():
    df = pd.DataFrame(
        {
            "date": ["2025-01-03", "2025-01-02"],
            "symbol": ["QQQ", "QQQ"],
            "open": [101, 100],
            "high": [102, 101],
            "low": [100, 99],
            "close": [101, 100],
            "adjClose": [100.5, 99.5],
            "volume": [20, 10],
        }
    )

    normalized = normalize_price_daily(df, "QQQ", "tiingo")

    assert list(normalized.columns) == PRICE_DAILY_COLUMNS
    assert normalized["date"].tolist() == ["2025-01-02", "2025-01-03"]
    assert normalized["adjusted_close"].tolist() == [99.5, 100.5]


def test_invesco_holdings_normalize_standard_columns():
    provider = InvescoProvider()
    payload = {
        "effectiveDate": "2026-06-03",
        "totalNumberOfHoldings": 4,
        "holdings": [
            {"ticker": "AAPL", "issuerName": "Apple Inc.", "percentageOfTotalNetAssets": 7.184254, "securityTypeCode": "COM", "securityTypeName": "Common Stock"},
            {"ticker": "MSFT", "issuerName": "Microsoft Corp.", "percentageOfTotalNetAssets": 5.015267, "securityTypeCode": "COM", "securityTypeName": "Common Stock"},
            {"ticker": "USD", "issuerName": "Cash", "percentageOfTotalNetAssets": 0.5, "securityTypeCode": "CASH", "securityTypeName": "Cash"},
            {"ticker": "", "issuerName": "Blank", "percentageOfTotalNetAssets": 0.1, "securityTypeCode": "CASH", "securityTypeName": "Cash"},
        ],
    }

    holdings = provider._normalize_payload(payload)  # noqa: SLF001
    holdings = holdings.reindex(columns=QQQ_HOLDINGS_COLUMNS)

    assert list(holdings.columns) == QQQ_HOLDINGS_COLUMNS
    assert holdings["symbol"].tolist() == ["AAPL", "MSFT", "USD"]
    assert holdings["security_type_code"].tolist() == ["COM", "COM", "CASH"]
    assert holdings["weight"].round(8).tolist() == [0.07184254, 0.05015267, 0.005]


def test_equity_holdings_filter_removes_non_equities():
    holdings = pd.DataFrame(
        [
            {"symbol": "AAPL", "weight": 0.08, "security_type_code": "COM", "security_type_name": "Common Stock"},
            {"symbol": "NQM6", "weight": 0.02, "security_type_code": "FUT", "security_type_name": "Future"},
            {"symbol": "USD", "weight": 0.01, "security_type_code": "CASH", "security_type_name": "Cash"},
            {"symbol": "TSLA", "weight": 0.04, "security_type_code": None, "security_type_name": None},
            {"symbol": "NQM6_", "weight": 0.01, "security_type_code": None, "security_type_name": None},
        ]
    )

    equities = build_equity_holdings(holdings)

    assert list(equities.columns) == QQQ_EQUITY_HOLDINGS_COLUMNS
    assert equities["symbol"].tolist() == ["AAPL", "TSLA"]


def test_breadth_metrics_use_quote_overlay_but_remain_objective():
    frames = {
        "AAPL": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=220, freq="B").astype(str), "adjClose": range(100, 320)}),
        "MSFT": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=220, freq="B").astype(str), "adjClose": range(320, 100, -1)}),
    }
    quote_map = {
        "AAPL": {"price": 321.0, "previousClose": 320.0},
        "MSFT": {"price": 99.0, "previousClose": 100.0},
    }

    metrics = build_breadth_metrics(frames, quote_map)

    assert list(metrics.columns) == BREADTH_METRICS_COLUMNS
    assert {"status", "signal", "direction", "建议"}.isdisjoint(metrics.columns)
    assert set(metrics["metric_name"]) == {
        "advancing_count",
        "declining_count",
        "advancing_ratio",
        "above_20d_ma_ratio",
        "above_50d_ma_ratio",
        "above_200d_ma_ratio",
        "new_high_20d_count",
        "new_low_20d_count",
    }
    assert metrics.loc[metrics["metric_name"] == "advancing_count", "metric_value"].iloc[0] == 1


def test_fetch_breadth_daily_mode_uses_cache_only(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        paths=SimpleNamespace(
            tiingo_price_cache_dir=tmp_path / "cache",
            raw_dir=tmp_path / "raw",
        )
    )
    settings.paths.tiingo_price_cache_dir.mkdir(parents=True)
    settings.paths.raw_dir.mkdir(parents=True)
    cache = pd.DataFrame(
        {
            "date": pd.bdate_range(end="2026-06-05", periods=220).astype(str),
            "adjClose": range(100, 320),
            "symbol": ["AAPL"] * 220,
            "source": ["tiingo"] * 220,
        }
    )
    cache.to_csv(settings.paths.tiingo_price_cache_dir / "AAPL.csv", index=False)
    holdings = pd.DataFrame([{"symbol": "AAPL", "company_name": "Apple", "weight": 0.1}])

    def fail_if_live_tiingo_called(*args, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("daily breadth must not call Tiingo live history")

    monkeypatch.setattr("qqq_tracker.pipeline.daily_run.fetch_tiingo_history_for_breadth", fail_if_live_tiingo_called)

    quality_rows = []
    metrics = fetch_breadth(settings, holdings, "2026-06-05", [], quality_rows, {"AAPL": {"price": 321.0, "previousClose": 320.0}})

    assert list(metrics.columns) == BREADTH_METRICS_COLUMNS
    assert metrics.loc[metrics["metric_name"] == "advancing_count", "denominator"].iloc[0] == 1
    assert quality_rows[0]["rate_limited"] is False
    assert quality_rows[0]["stopped_after_429"] is False
    assert "target_date=2026-06-05" in quality_rows[0]["message"]
    assert "target_aligned=1/1" in quality_rows[0]["message"]


def test_breadth_constituents_explains_recent_and_strict_eligibility(tmp_path):
    price_cache_dir = tmp_path / "cache" / "prices"
    price_cache_dir.mkdir(parents=True)
    settings = SimpleNamespace(
        paths=SimpleNamespace(
            price_cache_dir=price_cache_dir,
            tiingo_price_cache_dir=price_cache_dir / "tiingo",
        )
    )
    aapl = pd.DataFrame(
        {
            "date": pd.bdate_range(end="2026-06-05", periods=220).astype(str),
            "adjClose": list(range(100, 320)),
            "symbol": ["AAPL"] * 220,
            "source": ["tiingo"] * 220,
        }
    )
    nvda = pd.DataFrame(
        {
            "date": pd.bdate_range(end="2026-06-04", periods=220).astype(str),
            "adjClose": list(range(320, 100, -1)),
            "symbol": ["NVDA"] * 220,
            "source": ["tiingo"] * 220,
        }
    )
    aapl.to_csv(price_cache_dir / "AAPL.csv", index=False)
    nvda.to_csv(price_cache_dir / "NVDA.csv", index=False)
    holdings = pd.DataFrame(
        [
            {"symbol": "AAPL", "company_name": "Apple", "weight": 0.6},
            {"symbol": "NVDA", "company_name": "NVIDIA", "weight": 0.4},
        ]
    )

    constituents = build_breadth_constituents(settings, holdings, "2026-06-07", "2026-06-05")

    assert list(constituents.columns) == BREADTH_CONSTITUENTS_COLUMNS
    aapl_row = constituents[constituents["symbol"] == "AAPL"].iloc[0]
    nvda_row = constituents[constituents["symbol"] == "NVDA"].iloc[0]
    assert aapl_row["direction"] == "up"
    assert bool(aapl_row["included_in_recent_breadth"]) is True
    assert bool(aapl_row["included_in_strict_breadth"]) is True
    assert nvda_row["direction"] == "down"
    assert bool(nvda_row["included_in_recent_breadth"]) is True
    assert bool(nvda_row["included_in_strict_breadth"]) is False
    assert nvda_row["latest_date"] == "2026-06-04"
    assert nvda_row["previous_date"] < nvda_row["latest_date"]
    assert nvda_row["daily_return"] < 0


def test_fetch_breadth_missing_cache_still_records_quality(tmp_path):
    settings = SimpleNamespace(
        paths=SimpleNamespace(
            tiingo_price_cache_dir=tmp_path / "cache",
            raw_dir=tmp_path / "raw",
        )
    )
    settings.paths.tiingo_price_cache_dir.mkdir(parents=True)
    settings.paths.raw_dir.mkdir(parents=True)
    holdings = pd.DataFrame(
        [
            {"symbol": "AAPL", "company_name": "Apple", "weight": 0.7},
            {"symbol": "MSFT", "company_name": "Microsoft", "weight": 0.3},
        ]
    )

    quality_rows = []
    metrics = fetch_breadth(settings, holdings, "2026-06-05", [], quality_rows, {})

    assert set(metrics["metric_name"]) == {
        "advancing_count",
        "declining_count",
        "advancing_ratio",
        "above_20d_ma_ratio",
        "above_50d_ma_ratio",
        "above_200d_ma_ratio",
        "new_high_20d_count",
        "new_low_20d_count",
    }
    assert quality_rows[0]["symbol_coverage_ratio"] == 0.0
    assert quality_rows[0]["weight_coverage_ratio"] == 0.0
    assert quality_rows[0]["missing_top_weight_symbols"] == "AAPL,MSFT"


def test_fetch_breadth_excludes_stale_and_raw_seed_history(tmp_path):
    price_cache_dir = tmp_path / "cache" / "prices"
    raw_dir = tmp_path / "raw"
    raw_run_dir = raw_dir / "2026-06-05"
    price_cache_dir.mkdir(parents=True)
    raw_run_dir.mkdir(parents=True)
    settings = SimpleNamespace(
        paths=SimpleNamespace(
            price_cache_dir=price_cache_dir,
            tiingo_price_cache_dir=price_cache_dir / "tiingo",
            raw_dir=raw_dir,
        )
    )

    def history(symbol, end):
        return pd.DataFrame(
            {
                "date": pd.bdate_range(end=end, periods=220).astype(str),
                "adjClose": range(100, 320),
                "symbol": [symbol] * 220,
                "source": ["tiingo"] * 220,
            }
        )

    history("FRESH", "2026-06-05").to_csv(price_cache_dir / "FRESH.csv", index=False)
    history("STALE", "2026-05-20").to_csv(price_cache_dir / "STALE.csv", index=False)
    history("RAW", "2026-06-05").to_csv(raw_run_dir / "tiingo_RAW_breadth_daily.csv", index=False)
    holdings = pd.DataFrame(
        [
            {"symbol": "STALE", "company_name": "Stale", "weight": 0.5},
            {"symbol": "RAW", "company_name": "Raw", "weight": 0.3},
            {"symbol": "FRESH", "company_name": "Fresh", "weight": 0.2},
        ]
    )

    quality_rows = []
    metrics = fetch_breadth(settings, holdings, "2026-06-05", [], quality_rows, {})

    assert metrics.loc[metrics["metric_name"] == "advancing_count", "denominator"].iloc[0] == 1
    assert quality_rows[0]["symbol_coverage_ratio"] == 1 / 3
    assert quality_rows[0]["weight_coverage_ratio"] == 0.2
    assert quality_rows[0]["missing_top_weight_symbols"] == "STALE,RAW"
    assert not (price_cache_dir / "RAW.csv").exists()


def test_top_holdings_quotes_uses_batch_quote_map():
    holdings = pd.DataFrame(
        [
            {"symbol": "AAPL", "company_name": "Apple", "weight": 0.7},
            {"symbol": "MSFT", "company_name": "Microsoft", "weight": 0.3},
        ]
    )
    quote_map = {
        "AAPL": {
            "price": 200.0,
            "change": 1.5,
            "changesPercentage": 0.75,
            "marketCap": 3_000_000,
            "pe": 30.0,
            "eps": 6.0,
            "source": "fmp",
            "timestamp": 123456,
        }
    }

    quotes = build_top_holdings_quotes(holdings, quote_map, limit=2)

    assert list(quotes.columns) == TOP_HOLDINGS_QUOTES_COLUMNS
    assert quotes.loc[quotes["symbol"] == "AAPL", "provider"].iloc[0] == "fmp"
    assert bool(quotes.loc[quotes["symbol"] == "MSFT", "is_missing"].iloc[0]) is True
    assert quotes.loc[quotes["symbol"] == "MSFT", "error_type"].iloc[0] == "quote_missing"
    assert bool(quotes.loc[quotes["symbol"] == "AAPL", "was_merge_success"].iloc[0]) is True


def quote_settings(tmp_path, max_credits=40):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    return SimpleNamespace(
        api_limits={
            "twelve_data": {
                "quote_max_credits_per_run": max_credits,
                "quote_batch_size": 100,
                "quote_sleep_seconds_between_batches": 0,
            }
        },
        paths=SimpleNamespace(cache_dir=cache_dir, root=tmp_path),
    )


def test_twelve_data_quotes_requests_top_holdings_and_merges_normalized_symbols(tmp_path):
    settings = quote_settings(tmp_path)
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.7}, {"symbol": "MSFT", "weight": 0.3}, {"symbol": "LOW", "weight": 0.01}])
    twelve = FakeTwelveDataQuote({"AAPL": {"price": 200.0}, "MSFT": {"price": 500.0, "change": 1.0}, "LOW": {"price": 10.0}})

    updated, diagnostics, usage = fetch_twelve_data_quotes(settings, twelve, "2026-06-05", holdings, raw_dir=tmp_path, limit=2)
    quotes = build_top_holdings_quotes(holdings, updated, limit=2)

    assert twelve.calls == ["AAPL", "MSFT"]
    assert usage["calls_success"] == 2
    assert quotes.loc[quotes["symbol"] == "MSFT", "provider"].iloc[0] == "twelve_data"
    assert bool(quotes.loc[quotes["symbol"] == "MSFT", "is_missing"].iloc[0]) is False
    assert diagnostics["final_missing"].tolist() == [False, False]


def test_twelve_data_quotes_caps_requests_to_top20(tmp_path, monkeypatch):
    settings = quote_settings(tmp_path, max_credits=20)
    holdings = pd.DataFrame([{"symbol": f"S{i:02d}", "weight": 100 - i} for i in range(25)])
    twelve = FakeTwelveDataQuote({f"S{i:02d}": {"price": float(i)} for i in range(25)})
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    _, _, usage = fetch_twelve_data_quotes(settings, twelve, "2026-06-05", holdings, raw_dir=tmp_path)

    assert len(twelve.calls) == 20
    assert twelve.calls == [f"S{i:02d}" for i in range(20)]
    assert usage["credits_used"] == 20


def test_twelve_data_quote_429_stops_and_records_diagnostics(tmp_path):
    settings = quote_settings(tmp_path)
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.9}, {"symbol": "MSFT", "weight": 0.8}, {"symbol": "NVDA", "weight": 0.7}])
    twelve = FakeTwelveDataQuote({"AAPL": "429", "MSFT": {"price": 500.0}, "NVDA": {"price": 900.0}})

    updated, diagnostics, usage = fetch_twelve_data_quotes(settings, twelve, "2026-06-05", holdings, raw_dir=tmp_path)

    assert twelve.calls == ["AAPL"]
    assert "AAPL" not in updated
    assert "NVDA" not in updated
    assert bool(usage["rate_limited"]) is True
    assert bool(usage["stopped_after_429"]) is True
    assert usage["retry_after_seconds"] == 120
    assert bool(diagnostics.loc[diagnostics["symbol"] == "AAPL", "final_missing"].iloc[0]) is True


def test_top_holdings_quote_quality_records_coverage():
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.7}, {"symbol": "MSFT", "weight": 0.3}])
    quotes = pd.DataFrame(
        [
            {"symbol": "AAPL", "is_missing": False},
            {"symbol": "MSFT", "is_missing": True},
        ]
    )

    row = top_holdings_quote_quality(holdings, quotes, rate_limited=True, stopped_after_429=True)

    assert row["dataset"] == "top_holdings_quotes"
    assert row["symbol_coverage_ratio"] == 0.5
    assert row["missing_symbols"] == "MSFT"
    assert row["rate_limited"] is True
    assert row["stopped_after_429"] is True


def test_api_usage_row_columns_and_429_metadata():
    settings = SimpleNamespace(api_limits={"tiingo": {"hourly_requests": 50}})

    row = api_usage_row(
        settings,
        "2026-06-05",
        "tiingo",
        "daily_prices",
        40,
        39,
        symbols_requested=["AAPL", "MSFT"],
        symbols_loaded=["AAPL"],
        rate_limited=True,
        stopped_after_429=True,
        retry_after_seconds=3600,
        message="429 received",
    )
    df = pd.DataFrame([row], columns=API_USAGE_COLUMNS)

    assert list(df.columns) == API_USAGE_COLUMNS
    assert bool(df.loc[0, "rate_limited"]) is True
    assert bool(df.loc[0, "stopped_after_429"]) is True
    assert df.loc[0, "retry_after_seconds"] == 3600
    assert "actual_endpoint" in df.columns
    assert bool(df.loc[0, "production_enabled"]) is True


def test_data_quality_row_records_extended_coverage_fields():
    row = quality_row(
        "breadth_metrics",
        "tiingo_cache+twelve_data_quote",
        True,
        8,
        symbol_coverage_ratio=0.75,
        weight_coverage_ratio=0.82,
        missing_symbols=["AVGO"],
        missing_top_weight_symbols=["AVGO"],
        rate_limited=True,
        stopped_after_429=True,
        remaining_symbols_skipped=12,
        cache_rows_used=440,
        live_rows_fetched=50,
        fallback_provider="fmp",
        message="coverage insufficient for top-weight symbols",
    )
    df = pd.DataFrame([row], columns=DATA_QUALITY_COLUMNS)

    assert list(df.columns) == DATA_QUALITY_COLUMNS
    assert df.loc[0, "missing_symbols"] == "AVGO"
    assert df.loc[0, "symbol_coverage_ratio"] == 0.75
    assert df.loc[0, "weight_coverage_ratio"] == 0.82
    assert bool(df.loc[0, "rate_limited"]) is True
    assert {"history_coverage_ratio", "quote_coverage_ratio", "ma200_available"}.issubset(df.columns)


def test_model_input_v2_keeps_objective_columns():
    price_metrics = pd.DataFrame(
        [
            {
                "symbol": "QQQ",
                "source": "test_source",
                "date": "2025-12-31",
                "latest_close": 100.0,
                "return_20d": 0.01,
                "return_60d": 0.02,
                "vol_20d": 0.15,
                "current_drawdown": -0.03,
                "max_drawdown": -0.10,
                "ma_50": 98.0,
                "ma_200": 95.0,
            }
        ]
    )
    breadth = pd.DataFrame(
        [
            {
                "metric_name": "advancing_ratio",
                "metric_value": 0.5,
                "denominator": 2,
                "data_date": "2025-12-31",
                "source": "tiingo_cache+twelve_data_quote",
                "is_missing": False,
            }
        ]
    )
    quality = pd.DataFrame(
        [
            quality_row(
                "breadth_metrics",
                "tiingo_cache+twelve_data_quote",
                True,
                8,
                symbol_coverage_ratio=1.0,
                weight_coverage_ratio=1.0,
                message="ok",
            )
        ]
    )

    model_input = build_model_input_metrics_v2(
        price_metrics,
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        breadth,
        quality,
    )

    assert list(model_input.columns) == MODEL_INPUT_COLUMNS
    breadth_row = model_input[model_input["metric_name"] == "advancing_ratio"].iloc[0]
    assert breadth_row["source"] == "breadth_metrics"
    assert breadth_row["provider"] == "tiingo_cache+twelve_data_quote"
    assert pd.isna(breadth_row["coverage_ratio"])
    coverage_row = model_input[
        model_input["metric_name"] == "breadth_metrics_tiingo_cache+twelve_data_quote_symbol_coverage_ratio"
    ].iloc[0]
    assert coverage_row["source"] == "data_quality"
    assert coverage_row["provider"] == "tiingo_cache+twelve_data_quote"
    assert coverage_row["coverage_ratio"] == 1.0
    assert "symbol coverage" in coverage_row["quality_message"]
    assert "breadth_metrics_tiingo_cache+twelve_data_quote_symbol_coverage_ratio" in set(model_input["metric_name"])


def test_model_input_csv_contains_no_subjective_output(tmp_path):
    model_input = pd.DataFrame(
        [
            {
                "metric_name": "QQQ_return_20d",
                "metric_value": 0.02,
                "metric_date": "2026-06-05",
                "source": "price_metrics",
                "provider": "alpha_vantage",
                "coverage_ratio": None,
                "is_missing": False,
                "quality_message": "20 trading day return",
            }
        ],
        columns=MODEL_INPUT_COLUMNS,
    )
    path = tmp_path / "model_input_metrics.csv"
    model_input.to_csv(path, index=False)
    text = path.read_text(encoding="utf-8").lower()
    forbidden = ["买入", "卖出", "加仓", "观望", "风险颜色", "方向判断", "主观解释", "analysis_summary", "ai_input"]

    assert all(term not in text for term in forbidden)


def test_latest_manifest_has_no_subjective_output_files():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "reports/latest/manifest.json").read_text(encoding="utf-8"))
    paths = json.dumps(manifest.get("latest_files", {}), ensure_ascii=False).lower()

    assert "analysis_summary" not in paths
    assert "ai_input" not in paths
    assert "model_input_metrics_csv" in manifest["latest_files"]


def test_merge_price_history_deduplicates_and_sorts():
    cache_df = pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-03"],
            "adjClose": [100.0, 101.0],
            "symbol": ["AAPL", "AAPL"],
            "source": ["tiingo", "tiingo"],
        }
    )
    live_df = pd.DataFrame(
        {
            "date": ["2025-01-03", "2025-01-06"],
            "adjClose": [101.5, 102.0],
            "symbol": ["AAPL", "AAPL"],
            "source": ["tiingo", "tiingo"],
        }
    )

    merged = merge_price_history(cache_df, live_df, "AAPL")

    assert merged["date"].tolist() == ["2025-01-02", "2025-01-03", "2025-01-06"]
    assert merged["adjusted_close"].tolist() == [100.0, 101.5, 102.0]


def test_price_cache_reads_legacy_path_and_writes_provider_neutral_path(tmp_path):
    price_cache_dir = tmp_path / "prices"
    legacy_dir = price_cache_dir / "tiingo"
    legacy_dir.mkdir(parents=True)
    price_frame = pd.DataFrame({"date": ["2026-06-05"], "adjClose": [100.0], "symbol": ["QQQ"], "source": ["tiingo"]})
    price_frame.to_csv(legacy_dir / "QQQ.csv", index=False)
    settings = SimpleNamespace(paths=SimpleNamespace(price_cache_dir=price_cache_dir, tiingo_price_cache_dir=legacy_dir))

    loaded = load_price_cache(settings, "QQQ")
    write_price_cache(settings, "QQQ", merge_price_history(pd.DataFrame(), loaded, "QQQ"))

    assert len(loaded) == 1
    assert (price_cache_dir / "QQQ.csv").exists()
    assert pd.read_csv(price_cache_dir / "QQQ.csv")["adjusted_close"].tolist() == [100.0]


def test_qqq_price_history_initializes_cache_and_calculates_ma200(tmp_path):
    price_cache_dir = tmp_path / "cache" / "prices"
    legacy_dir = price_cache_dir / "tiingo"
    raw_dir = tmp_path / "raw"
    price_cache_dir.mkdir(parents=True)
    legacy_dir.mkdir(parents=True)
    raw_dir.mkdir()
    settings = SimpleNamespace(
        symbols={"primary_etf": "QQQ"},
        sources={"providers": {"alpha_vantage": {"default_outputsize": "compact"}}},
        api_limits={
            "alpha_vantage": {"daily_requests": 25},
            "tiingo": {"hourly_requests": 50},
            "twelve_data": {"minute_credits": 8},
        },
        paths=SimpleNamespace(price_cache_dir=price_cache_dir, tiingo_price_cache_dir=legacy_dir),
    )
    alpha_df = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=100, freq="B").astype(str),
            "close": range(300, 400),
            "adjusted_close": range(300, 400),
            "symbol": ["QQQ"] * 100,
            "source": ["alpha_vantage"] * 100,
        }
    )
    tiingo_df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=300, freq="B").astype(str),
            "adjClose": range(100, 400),
            "symbol": ["QQQ"] * 300,
            "source": ["tiingo"] * 300,
        }
    )

    class FakeAlpha:
        available = True

        def daily(self, symbol, outputsize="compact"):
            return ProviderResult("alpha_vantage", True, alpha_df, "100 rows", {})

    class FakeTiingoHistory:
        available = True

        def daily_prices(self, symbol, start_date=None, end_date=None):
            return ProviderResult("tiingo", True, tiingo_df, "300 rows", {})

    class FakeTwelveHistory:
        available = True

        def time_series(self, symbol, outputsize=260, interval="1day"):
            raise AssertionError("Twelve Data should not run after Tiingo completes the cache")

    usage_rows = []
    quality_rows = []
    daily, metrics = fetch_qqq_price_history(
        settings,
        {"alpha_vantage": FakeAlpha(), "tiingo": FakeTiingoHistory(), "twelve_data": FakeTwelveHistory()},
        "2026-06-05",
        raw_dir,
        [],
        usage_rows,
        quality_rows,
    )

    assert len(daily) == 260
    assert pd.notna(metrics.loc[0, "ma_200"])
    assert metrics.loc[0, "source"] == "alpha_vantage_compact+local_cache"
    assert (price_cache_dir / "QQQ.csv").exists()


def test_rate_limit_error_exposes_retry_after():
    exc = RateLimitError("429 Client Error", retry_after_seconds=30)

    assert str(exc) == "429 Client Error"
    assert exc.retry_after_seconds == 30


def test_fmp_batch_quote_falls_back_to_single_symbol_quotes(monkeypatch):
    provider = FMPProvider("demo-key", "https://example.com")

    def fake_request_json(url, params=None, headers=None):  # noqa: ANN001, ARG001
        raise APIError("402 Client Error")

    def fake_quote(symbol):  # noqa: ANN001
        return type(
            "Result",
            (),
            {
                "ok": symbol == "AAPL",
                "data": pd.DataFrame([{"symbol": symbol, "price": 100.0, "previousClose": 99.0, "source": "fmp"}]) if symbol == "AAPL" else pd.DataFrame(),
                "raw": {"symbol": symbol},
                "message": "ok" if symbol == "AAPL" else "failed",
            },
        )()

    monkeypatch.setattr(provider, "request_json", fake_request_json)
    monkeypatch.setattr(provider, "quote", fake_quote)

    result = provider.batch_quote(["AAPL", "MSFT"], chunk_size=10)

    assert result.ok is True
    assert result.data["symbol"].tolist() == ["AAPL"]
    assert "missing=MSFT" in result.message
