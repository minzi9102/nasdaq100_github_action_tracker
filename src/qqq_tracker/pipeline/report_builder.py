from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd


MODEL_INPUT_COLUMNS = ["metric_name", "metric_value", "unit_or_method", "data_date", "source", "is_missing"]


def _is_missing(value: object) -> bool:
    return bool(pd.isna(value))


def build_model_input_metrics(
    price_metrics: pd.DataFrame,
    macro_daily: pd.DataFrame,
    macro_metrics: pd.DataFrame,
    fmp_summary: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    if not price_metrics.empty:
        for _, r in price_metrics.iterrows():
            symbol = r.get("symbol")
            source = r.get("source")
            date = r.get("date")
            rows.extend([
                [f"{symbol}_latest_close", r.get("latest_close"), "latest adjusted close", date, source, _is_missing(r.get("latest_close"))],
                [f"{symbol}_return_20d", r.get("return_20d"), "20 trading day return", date, source, _is_missing(r.get("return_20d"))],
                [f"{symbol}_return_60d", r.get("return_60d"), "60 trading day return", date, source, _is_missing(r.get("return_60d"))],
                [f"{symbol}_vol_20d", r.get("vol_20d"), "20 trading day annualized volatility", date, source, _is_missing(r.get("vol_20d"))],
                [f"{symbol}_current_drawdown", r.get("current_drawdown"), "current drawdown from period high", date, source, _is_missing(r.get("current_drawdown"))],
                [f"{symbol}_max_drawdown", r.get("max_drawdown"), "maximum drawdown over fetched period", date, source, _is_missing(r.get("max_drawdown"))],
                [f"{symbol}_ma_50", r.get("ma_50"), "50 trading day moving average", date, source, _is_missing(r.get("ma_50"))],
                [f"{symbol}_ma_200", r.get("ma_200"), "200 trading day moving average", date, source, _is_missing(r.get("ma_200"))],
            ])
    if not macro_daily.empty:
        for _, r in macro_daily.iterrows():
            rows.append([
                f"{r.get('series_id')}_latest_value",
                r.get("latest_value"),
                "latest FRED observation value",
                r.get("latest_date"),
                r.get("source"),
                _is_missing(r.get("latest_value")),
            ])
    if not macro_metrics.empty:
        for _, r in macro_metrics.iterrows():
            rows.append([
                r.get("metric_name"),
                r.get("metric_value"),
                r.get("unit_or_method"),
                r.get("data_date"),
                r.get("source"),
                _is_missing(r.get("metric_value")),
            ])
    if not fmp_summary.empty:
        available_ratio = fmp_summary["ok"].mean() if "ok" in fmp_summary.columns and len(fmp_summary) else None
        rows.append([
            "fmp_quote_available_ratio",
            available_ratio,
            "successful quote responses / requested symbols",
            pd.Timestamp.today().date().isoformat(),
            "FMP",
            _is_missing(available_ratio),
        ])
    return pd.DataFrame(rows, columns=MODEL_INPUT_COLUMNS)


def build_model_input_metrics_v2(
    price_metrics: pd.DataFrame,
    macro_daily: pd.DataFrame,
    macro_metrics: pd.DataFrame,
    fmp_summary: pd.DataFrame,
    breadth_metrics: pd.DataFrame,
    data_quality: pd.DataFrame,
) -> pd.DataFrame:
    model_input = build_model_input_metrics(price_metrics, macro_daily, macro_metrics, fmp_summary)
    rows = model_input.values.tolist()
    if not breadth_metrics.empty:
        for _, r in breadth_metrics.iterrows():
            rows.append([
                r.get("metric_name"),
                r.get("metric_value"),
                f"breadth metric; denominator={r.get('denominator')}",
                r.get("data_date"),
                r.get("source"),
                _is_missing(r.get("metric_value")),
            ])
    if not data_quality.empty:
        for _, r in data_quality.iterrows():
            dataset = r.get("dataset")
            provider = r.get("provider")
            rows.append([
                f"{dataset}_{provider}_symbol_coverage_ratio",
                r.get("symbol_coverage_ratio"),
                f"data quality symbol coverage; ok={r.get('ok')}; rows={r.get('rows')}",
                pd.Timestamp.today().date().isoformat(),
                provider,
                _is_missing(r.get("symbol_coverage_ratio")),
            ])
            rows.append([
                f"{dataset}_{provider}_weight_coverage_ratio",
                r.get("weight_coverage_ratio"),
                f"data quality weight coverage; rate_limited={r.get('rate_limited')}; fallback_provider={r.get('fallback_provider')}",
                pd.Timestamp.today().date().isoformat(),
                provider,
                _is_missing(r.get("weight_coverage_ratio")),
            ])
            rows.append([
                f"{dataset}_{provider}_rate_limited",
                float(bool(r.get("rate_limited"))),
                f"1 means provider hit 429 and breadth stopped_after_429={r.get('stopped_after_429')}",
                pd.Timestamp.today().date().isoformat(),
                provider,
                False,
            ])
    return pd.DataFrame(rows, columns=MODEL_INPUT_COLUMNS)


def write_excel(path: Path, sheets: Dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        wb = writer.book
        header_fmt = wb.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1, "align": "center"})
        pct_fmt = wb.add_format({"num_format": "0.00%"})
        for sheet_name, df in sheets.items():
            name = sheet_name[:31]
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(1, len(df)), max(0, len(df.columns) - 1))
            for i, col in enumerate(df.columns):
                ws.write(0, i, col, header_fmt)
                width = min(max(len(str(col)) + 4, 12), 42)
                ws.set_column(i, i, width)
                if any(k in str(col) for k in ["return", "vol", "drawdown", "收益", "波动", "回撤", "率"]):
                    ws.set_column(i, i, width, pct_fmt)
