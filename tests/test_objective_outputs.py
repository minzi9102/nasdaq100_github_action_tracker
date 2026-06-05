import pandas as pd

from qqq_tracker.pipeline.daily_run import (
    BREADTH_METRICS_COLUMNS,
    DATA_QUALITY_COLUMNS,
    PRICE_DAILY_COLUMNS,
    QQQ_HOLDINGS_COLUMNS,
    build_breadth_metrics,
    build_macro_metric_rows,
    normalize_price_daily,
    quality_row,
    summarize_price,
)
from qqq_tracker.pipeline.report_builder import MODEL_INPUT_COLUMNS, build_model_input_metrics, build_model_input_metrics_v2
from qqq_tracker.providers.invesco import InvescoProvider


def test_price_metrics_do_not_include_signal_fields():
    df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=220, freq="B").astype(str),
        "adjusted_close": range(100, 320),
    })

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
    price_metrics = pd.DataFrame([{
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
    }])
    macro_daily = pd.DataFrame([{
        "series_id": "DGS10",
        "name": "US 10Y",
        "latest_date": "2025-12-31",
        "latest_value": 4.5,
        "source": "FRED",
    }])
    macro_metrics = pd.DataFrame([{
        "metric_name": "DGS10_1M_CHANGE",
        "metric_value": 0.1,
        "unit_or_method": "current minus previous",
        "data_date": "2025-12-31",
        "source": "FRED",
    }])
    fmp_summary = pd.DataFrame([{"symbol": "AAPL", "ok": True, "rows": 1, "message": "ok"}])

    model_input = build_model_input_metrics(price_metrics, macro_daily, macro_metrics, fmp_summary)

    assert list(model_input.columns) == MODEL_INPUT_COLUMNS
    assert {"状态", "方向/解读", "阈值/比较基准"}.isdisjoint(model_input.columns)


def test_price_daily_standard_columns_and_sorting():
    df = pd.DataFrame({
        "date": ["2025-01-03", "2025-01-02"],
        "symbol": ["QQQ", "QQQ"],
        "open": [101, 100],
        "high": [102, 101],
        "low": [100, 99],
        "close": [101, 100],
        "adjClose": [100.5, 99.5],
        "volume": [20, 10],
    })

    normalized = normalize_price_daily(df, "QQQ", "tiingo")

    assert list(normalized.columns) == PRICE_DAILY_COLUMNS
    assert normalized["date"].tolist() == ["2025-01-02", "2025-01-03"]
    assert normalized["adjusted_close"].tolist() == [99.5, 100.5]


def test_invesco_holdings_normalize_standard_columns():
    provider = InvescoProvider()
    payload = {
        "effectiveDate": "2026-06-03",
        "totalNumberOfHoldings": 3,
        "holdings": [
            {"ticker": "AAPL", "issuerName": "Apple Inc.", "percentageOfTotalNetAssets": 7.184254},
            {"ticker": "MSFT", "issuerName": "Microsoft Corp.", "percentageOfTotalNetAssets": 5.015267},
            {"ticker": "", "issuerName": "Cash", "percentageOfTotalNetAssets": 0.5},
        ],
    }

    holdings = provider._normalize_payload(payload)  # noqa: SLF001
    holdings = holdings.reindex(columns=QQQ_HOLDINGS_COLUMNS)

    assert list(holdings.columns) == QQQ_HOLDINGS_COLUMNS
    assert holdings["symbol"].tolist() == ["AAPL", "MSFT"]
    assert holdings["date"].tolist() == ["2026-06-03", "2026-06-03"]
    assert holdings["weight"].round(8).tolist() == [0.07184254, 0.05015267]


def test_breadth_metrics_are_objective_numeric_fields():
    frames = {
        "AAPL": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=220, freq="B").astype(str), "adjClose": range(100, 320)}),
        "MSFT": pd.DataFrame({"date": pd.date_range("2025-01-01", periods=220, freq="B").astype(str), "adjClose": range(320, 100, -1)}),
    }

    metrics = build_breadth_metrics(frames)

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


def test_data_quality_row_records_missing_symbols():
    row = quality_row("breadth_metrics", "tiingo", False, 3, 0.75, ["AVGO"], "3/4 symbols fetched")
    df = pd.DataFrame([row], columns=DATA_QUALITY_COLUMNS)

    assert list(df.columns) == DATA_QUALITY_COLUMNS
    assert df.loc[0, "missing_symbols"] == "AVGO"
    assert df.loc[0, "coverage_ratio"] == 0.75


def test_model_input_v2_keeps_objective_columns():
    price_metrics = pd.DataFrame([{
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
    }])
    breadth = pd.DataFrame([{
        "metric_name": "advancing_ratio",
        "metric_value": 0.5,
        "denominator": 2,
        "data_date": "2025-12-31",
        "source": "tiingo",
        "is_missing": False,
    }])
    quality = pd.DataFrame([quality_row("breadth_metrics", "tiingo", True, 8, 1.0, [], "ok")])

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
