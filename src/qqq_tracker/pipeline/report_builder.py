from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd


MODEL_INPUT_COLUMNS = [
    "metric_name",
    "metric_value",
    "metric_date",
    "source",
    "provider",
    "coverage_ratio",
    "is_missing",
    "quality_message",
]


def _is_missing(value: object) -> bool:
    return bool(pd.isna(value))


def build_model_input_metrics(
    price_metrics: pd.DataFrame,
    macro_daily: pd.DataFrame,
    macro_metrics: pd.DataFrame,
    fmp_summary: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict] = []

    def add_row(
        metric_name: str,
        metric_value: object,
        metric_date: object,
        source: str,
        provider: object,
        quality_message: str,
        coverage_ratio: object = None,
    ) -> None:
        rows.append(
            {
                "metric_name": metric_name,
                "metric_value": metric_value,
                "metric_date": metric_date,
                "source": source,
                "provider": provider,
                "coverage_ratio": coverage_ratio,
                "is_missing": _is_missing(metric_value),
                "quality_message": quality_message,
            }
        )

    if not price_metrics.empty:
        for _, r in price_metrics.iterrows():
            symbol = r.get("symbol")
            provider = r.get("source")
            metric_date = r.get("date")
            add_row(f"{symbol}_latest_close", r.get("latest_close"), metric_date, "price_metrics", provider, "latest adjusted close")
            add_row(f"{symbol}_return_20d", r.get("return_20d"), metric_date, "price_metrics", provider, "20 trading day return")
            add_row(f"{symbol}_return_60d", r.get("return_60d"), metric_date, "price_metrics", provider, "60 trading day return")
            add_row(f"{symbol}_vol_20d", r.get("vol_20d"), metric_date, "price_metrics", provider, "20 trading day annualized volatility")
            add_row(
                f"{symbol}_current_drawdown",
                r.get("current_drawdown"),
                metric_date,
                "price_metrics",
                provider,
                "current drawdown from period high",
            )
            add_row(
                f"{symbol}_max_drawdown",
                r.get("max_drawdown"),
                metric_date,
                "price_metrics",
                provider,
                "maximum drawdown over fetched period",
            )
            add_row(f"{symbol}_ma_50", r.get("ma_50"), metric_date, "price_metrics", provider, "50 trading day moving average")
            add_row(f"{symbol}_ma_200", r.get("ma_200"), metric_date, "price_metrics", provider, "200 trading day moving average")
    if not macro_daily.empty:
        for _, r in macro_daily.iterrows():
            add_row(
                f"{r.get('series_id')}_latest_value",
                r.get("latest_value"),
                r.get("latest_date"),
                "macro_daily",
                r.get("source"),
                "latest FRED observation value",
            )
    if not macro_metrics.empty:
        for _, r in macro_metrics.iterrows():
            add_row(
                r.get("metric_name"),
                r.get("metric_value"),
                r.get("data_date"),
                "macro_metrics",
                r.get("source"),
                str(r.get("unit_or_method") or ""),
            )
    if not fmp_summary.empty:
        available_ratio = fmp_summary["ok"].mean() if "ok" in fmp_summary.columns and len(fmp_summary) else None
        add_row(
            "fmp_quote_available_ratio",
            available_ratio,
            pd.Timestamp.today().date().isoformat(),
            "fmp_summary",
            "fmp",
            "successful quote responses / requested symbols",
            coverage_ratio=available_ratio,
        )
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
    rows = model_input.to_dict(orient="records")
    if not breadth_metrics.empty:
        for _, r in breadth_metrics.iterrows():
            metric_value = r.get("metric_value")
            rows.append(
                {
                    "metric_name": r.get("metric_name"),
                    "metric_value": metric_value,
                    "metric_date": r.get("data_date"),
                    "source": "breadth_metrics",
                    "provider": r.get("source"),
                    "coverage_ratio": None,
                    "is_missing": _is_missing(metric_value),
                    "quality_message": f"breadth metric; denominator={r.get('denominator')}",
                }
            )
    if not data_quality.empty:
        for _, r in data_quality.iterrows():
            dataset = r.get("dataset")
            provider = r.get("provider")
            metric_date = pd.Timestamp.today().date().isoformat()
            symbol_coverage = r.get("symbol_coverage_ratio")
            weight_coverage = r.get("weight_coverage_ratio")
            rows.append(
                {
                    "metric_name": f"{dataset}_{provider}_symbol_coverage_ratio",
                    "metric_value": symbol_coverage,
                    "metric_date": metric_date,
                    "source": "data_quality",
                    "provider": provider,
                    "coverage_ratio": symbol_coverage,
                    "is_missing": _is_missing(symbol_coverage),
                    "quality_message": f"symbol coverage; ok={r.get('ok')}; rows={r.get('rows')}; message={r.get('message')}",
                }
            )
            rows.append(
                {
                    "metric_name": f"{dataset}_{provider}_weight_coverage_ratio",
                    "metric_value": weight_coverage,
                    "metric_date": metric_date,
                    "source": "data_quality",
                    "provider": provider,
                    "coverage_ratio": weight_coverage,
                    "is_missing": _is_missing(weight_coverage),
                    "quality_message": f"weight coverage; rate_limited={r.get('rate_limited')}; fallback_provider={r.get('fallback_provider')}",
                }
            )
            rows.append(
                {
                    "metric_name": f"{dataset}_{provider}_rate_limited",
                    "metric_value": float(bool(r.get("rate_limited"))),
                    "metric_date": metric_date,
                    "source": "data_quality",
                    "provider": provider,
                    "coverage_ratio": None,
                    "is_missing": False,
                    "quality_message": f"1 means provider hit a limit; stopped_after_429={r.get('stopped_after_429')}",
                }
            )
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
