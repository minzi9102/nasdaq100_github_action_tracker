import pandas as pd
from types import SimpleNamespace

from qqq_tracker.pipeline.daily_run import (
    API_USAGE_COLUMNS,
    BREADTH_METRICS_COLUMNS,
    DATA_QUALITY_COLUMNS,
    PRICE_DAILY_COLUMNS,
    QQQ_EQUITY_HOLDINGS_COLUMNS,
    QQQ_HOLDINGS_COLUMNS,
    TOP_HOLDINGS_QUOTES_COLUMNS,
    api_usage_row,
    build_breadth_metrics,
    build_equity_holdings,
    build_macro_metric_rows,
    build_top_holdings_quotes,
    fetch_breadth,
    merge_price_history,
    normalize_price_daily,
    quality_row,
    run_twelve_data_quote_fallback,
    summarize_price,
    top_holdings_quote_quality,
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
    assert {"状态", "方向/解读", "阈值/比较基准"}.isdisjoint(model_input.columns)


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
            "date": pd.date_range("2025-01-01", periods=220, freq="B").astype(str),
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


def test_twelve_data_quote_fallback_not_called_when_top20_covered(tmp_path):
    settings = SimpleNamespace(api_limits={"twelve_data": {"quote_max_credits_per_run": 20}})
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.7}, {"symbol": "MSFT", "weight": 0.3}])
    quote_map = {
        "AAPL": {"symbol": "AAPL", "price": 200.0, "source": "fmp"},
        "MSFT": {"symbol": "MSFT", "price": 500.0, "source": "fmp"},
    }
    twelve = FakeTwelveDataQuote({"MSFT": {"price": 501.0}})

    updated, usage = run_twelve_data_quote_fallback(settings, twelve, "2026-06-05", holdings, quote_map, raw_dir=tmp_path)

    assert twelve.calls == []
    assert updated == quote_map
    assert usage["calls_attempted"] == 0
    assert usage["message"] == "no missing top holdings quote candidates"


def test_twelve_data_quote_fallback_only_requests_missing_top20(tmp_path):
    settings = SimpleNamespace(api_limits={"twelve_data": {"quote_max_credits_per_run": 20, "quote_batch_size": 100}})
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.7}, {"symbol": "MSFT", "weight": 0.3}, {"symbol": "LOW", "weight": 0.01}])
    quote_map = {"AAPL": {"symbol": "AAPL", "price": 200.0, "source": "fmp"}}
    twelve = FakeTwelveDataQuote({"MSFT": {"price": 500.0, "change": 1.0}, "LOW": {"price": 10.0}})

    updated, usage = run_twelve_data_quote_fallback(settings, twelve, "2026-06-05", holdings, quote_map, raw_dir=tmp_path, limit=2)
    quotes = build_top_holdings_quotes(holdings, updated, limit=2)

    assert twelve.calls == ["MSFT"]
    assert usage["calls_success"] == 1
    assert quotes.loc[quotes["symbol"] == "MSFT", "provider"].iloc[0] == "twelve_data"
    assert bool(quotes.loc[quotes["symbol"] == "MSFT", "is_missing"].iloc[0]) is False


def test_twelve_data_quote_fallback_caps_fmp_full_failure_to_top20(tmp_path, monkeypatch):
    settings = SimpleNamespace(api_limits={"twelve_data": {"quote_max_credits_per_run": 20, "quote_batch_size": 100}})
    holdings = pd.DataFrame([{"symbol": f"S{i:02d}", "weight": 100 - i} for i in range(25)])
    twelve = FakeTwelveDataQuote({f"S{i:02d}": {"price": float(i)} for i in range(25)})
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    _, usage = run_twelve_data_quote_fallback(settings, twelve, "2026-06-05", holdings, {}, raw_dir=tmp_path)

    assert len(twelve.calls) == 20
    assert twelve.calls == [f"S{i:02d}" for i in range(20)]
    assert usage["credits_used"] == 20


def test_twelve_data_quote_429_stops_without_overwriting_fmp(tmp_path):
    settings = SimpleNamespace(api_limits={"twelve_data": {"quote_max_credits_per_run": 20, "quote_batch_size": 100}})
    holdings = pd.DataFrame([{"symbol": "AAPL", "weight": 0.9}, {"symbol": "MSFT", "weight": 0.8}, {"symbol": "NVDA", "weight": 0.7}])
    quote_map = {"AAPL": {"symbol": "AAPL", "price": 200.0, "source": "fmp"}}
    twelve = FakeTwelveDataQuote({"MSFT": "429", "NVDA": {"price": 900.0}})

    updated, usage = run_twelve_data_quote_fallback(settings, twelve, "2026-06-05", holdings, quote_map, raw_dir=tmp_path)

    assert twelve.calls == ["MSFT"]
    assert updated["AAPL"]["source"] == "fmp"
    assert "NVDA" not in updated
    assert bool(usage["rate_limited"]) is True
    assert bool(usage["stopped_after_429"]) is True
    assert usage["retry_after_seconds"] == 120


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


def test_data_quality_row_records_extended_coverage_fields():
    row = quality_row(
        "breadth_metrics",
        "fmp+tiingo_cache",
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
                "source": "fmp+tiingo_cache",
                "is_missing": False,
            }
        ]
    )
    quality = pd.DataFrame(
        [
            quality_row(
                "breadth_metrics",
                "fmp+tiingo_cache",
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
    forbidden = {"状态", "方向/解读", "阈值/比较基准", "signal", "status", "direction", "建议"}
    assert forbidden.isdisjoint(model_input.columns)
    assert "breadth_metrics_fmp+tiingo_cache_symbol_coverage_ratio" in set(model_input["metric_name"])


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
    assert merged["adjClose"].tolist() == [100.0, 101.5, 102.0]


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
