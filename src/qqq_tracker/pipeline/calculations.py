from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def pct_change_from_close(df: pd.DataFrame, n: int, price_col: str = "adjusted_close") -> float | None:
    if df.empty or price_col not in df.columns or len(df.dropna(subset=[price_col])) <= n:
        return None
    s = df.dropna(subset=[price_col]).sort_values("date")[price_col].astype(float)
    return float(s.iloc[-1] / s.iloc[-n - 1] - 1)


def moving_average(df: pd.DataFrame, n: int, price_col: str = "adjusted_close") -> float | None:
    if df.empty or price_col not in df.columns or len(df.dropna(subset=[price_col])) < n:
        return None
    s = df.dropna(subset=[price_col]).sort_values("date")[price_col].astype(float)
    return float(s.tail(n).mean())


def annualized_vol(df: pd.DataFrame, n: int = 20, price_col: str = "adjusted_close") -> float | None:
    if df.empty or price_col not in df.columns:
        return None
    s = df.dropna(subset=[price_col]).sort_values("date")[price_col].astype(float)
    rets = s.pct_change().dropna()
    if len(rets) < n:
        return None
    return float(rets.tail(n).std() * np.sqrt(252))


def drawdown_metrics(df: pd.DataFrame, price_col: str = "adjusted_close") -> Dict[str, Any]:
    if df.empty or price_col not in df.columns:
        return {"current_drawdown": None, "max_drawdown": None, "period_high": None, "period_low": None}
    s = df.dropna(subset=[price_col]).sort_values("date")
    close = s[price_col].astype(float)
    rolling_high = close.cummax()
    dd = close / rolling_high - 1
    return {
        "current_drawdown": float(dd.iloc[-1]),
        "max_drawdown": float(dd.min()),
        "period_high": float(close.max()),
        "period_low": float(close.min()),
    }


def latest_value(df: pd.DataFrame, value_col: str = "value") -> float | None:
    if df.empty or value_col not in df.columns:
        return None
    d = df.dropna(subset=[value_col]).sort_values("date")
    if d.empty:
        return None
    return float(d[value_col].iloc[-1])


def signal_price_return(value: float | None, green_gte: float, yellow_gte: float) -> str:
    if value is None:
        return "灰色"
    if value >= green_gte:
        return "绿色"
    if value >= yellow_gte:
        return "黄色"
    return "红色"


def signal_lte(value: float | None, green_lte: float, yellow_lte: float) -> str:
    if value is None:
        return "灰色"
    if value <= green_lte:
        return "绿色"
    if value <= yellow_lte:
        return "黄色"
    return "红色"


def signal_gte(value: float | None, green_gte: float, yellow_gte: float) -> str:
    if value is None:
        return "灰色"
    if value >= green_gte:
        return "绿色"
    if value >= yellow_gte:
        return "黄色"
    return "红色"
