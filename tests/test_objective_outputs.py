import pandas as pd

from qqq_tracker.pipeline.daily_run import build_macro_metric_rows, summarize_price
from qqq_tracker.pipeline.report_builder import MODEL_INPUT_COLUMNS, build_model_input_metrics


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
