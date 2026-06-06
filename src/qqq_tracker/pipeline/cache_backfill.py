from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict

import pandas as pd

from qqq_tracker.io import write_csv, write_json
from qqq_tracker.providers import TiingoProvider
from qqq_tracker.settings import Settings

from .daily_run import (
    API_USAGE_COLUMNS,
    FULL_HISTORY_DAYS,
    MIN_HISTORY_ROWS,
    api_usage_row,
    cache_path_for_symbol,
    load_previous_csv,
    load_tiingo_cache,
    merge_price_history,
    normalize_symbol,
    provider_config,
    rel_path,
    result_rate_limit_meta,
    valid_history_row_count,
    write_tiingo_cache,
)

CACHE_QUALITY_COLUMNS = [
    "run_date",
    "symbol",
    "weight",
    "provider",
    "before_rows",
    "after_rows",
    "fetched_rows",
    "is_complete",
    "was_requested",
    "ok",
    "rate_limited",
    "stopped_after_429",
    "retry_after_seconds",
    "message",
]


def as_of_date(value: str) -> str:
    if value == "auto":
        return date.today().isoformat()
    return value


def load_backfill_holdings(settings: Settings, run_date: str) -> pd.DataFrame:
    dated = settings.paths.processed_dir / run_date / "qqq_equity_holdings.csv"
    if dated.exists():
        return pd.read_csv(dated)
    previous = load_previous_csv(settings, "qqq_equity_holdings.csv", [])
    if not previous.empty:
        return previous
    return pd.DataFrame()


def prepare_backfill_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    if holdings.empty or "symbol" not in holdings.columns:
        return pd.DataFrame(columns=["symbol", "weight"])
    prepared = holdings.copy()
    prepared["symbol"] = prepared["symbol"].map(normalize_symbol)
    prepared["weight"] = pd.to_numeric(prepared.get("weight", 0.0), errors="coerce").fillna(0.0)
    prepared = prepared[prepared["symbol"].notna() & prepared["symbol"].ne("")]
    prepared = prepared.drop_duplicates(subset=["symbol"], keep="first")
    return prepared.sort_values(["weight", "symbol"], ascending=[False, True]).reset_index(drop=True)


def cache_quality_row(
    run_date: str,
    symbol: str,
    weight: float,
    before_rows: int,
    after_rows: int,
    fetched_rows: int = 0,
    was_requested: bool = False,
    ok: bool = False,
    rate_limited: bool = False,
    stopped_after_429: bool = False,
    retry_after_seconds: float | None = None,
    message: str = "",
) -> Dict:
    return {
        "run_date": run_date,
        "symbol": normalize_symbol(symbol),
        "weight": weight,
        "provider": "tiingo",
        "before_rows": int(before_rows),
        "after_rows": int(after_rows),
        "fetched_rows": int(fetched_rows),
        "is_complete": int(after_rows) >= MIN_HISTORY_ROWS,
        "was_requested": bool(was_requested),
        "ok": bool(ok),
        "rate_limited": bool(rate_limited),
        "stopped_after_429": bool(stopped_after_429),
        "retry_after_seconds": retry_after_seconds,
        "message": message,
    }


def backfill_price_cache(
    settings: Settings,
    tiingo: TiingoProvider,
    run_date: str,
    max_calls: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    holdings = prepare_backfill_holdings(load_backfill_holdings(settings, run_date))
    max_calls_per_run = int(max_calls if max_calls is not None else settings.api_limits.get("tiingo", {}).get("max_calls_per_run", 40))
    raw_dir = settings.paths.raw_dir / run_date

    quality_rows: list[Dict] = []
    attempted_symbols: list[str] = []
    loaded_symbols: list[str] = []
    calls_attempted = 0
    calls_success = 0
    rate_limited = False
    stopped_after_429 = False
    retry_after_seconds = None
    messages: list[str] = []

    if holdings.empty:
        api_usage = pd.DataFrame(
            [
                api_usage_row(
                    settings,
                    run_date,
                    "tiingo",
                    "daily_prices_cache_backfill",
                    0,
                    0,
                    message="qqq_equity_holdings.csv not found or empty",
                )
            ],
            columns=API_USAGE_COLUMNS,
        )
        manifest = {"ok": False, "run_date": run_date, "message": "qqq_equity_holdings.csv not found or empty"}
        return pd.DataFrame(columns=CACHE_QUALITY_COLUMNS), api_usage, manifest

    for _, holding in holdings.iterrows():
        symbol = holding["symbol"]
        weight = float(holding.get("weight", 0.0))
        cache_df = load_tiingo_cache(settings, symbol)
        before_rows = valid_history_row_count(cache_df)

        if before_rows >= MIN_HISTORY_ROWS:
            quality_rows.append(cache_quality_row(run_date, symbol, weight, before_rows, before_rows, ok=True, message="cache complete"))
            continue

        if stopped_after_429:
            quality_rows.append(
                cache_quality_row(
                    run_date,
                    symbol,
                    weight,
                    before_rows,
                    before_rows,
                    ok=False,
                    rate_limited=True,
                    stopped_after_429=True,
                    retry_after_seconds=retry_after_seconds,
                    message="skipped after Tiingo 429",
                )
            )
            continue

        if calls_attempted >= max_calls_per_run:
            quality_rows.append(cache_quality_row(run_date, symbol, weight, before_rows, before_rows, ok=False, message="skipped after max_calls limit"))
            continue

        attempted_symbols.append(symbol)
        calls_attempted += 1
        end = run_date
        start = (datetime.fromisoformat(end) - timedelta(days=FULL_HISTORY_DAYS)).date().isoformat()
        result = tiingo.daily_prices(symbol, start_date=start, end_date=end)
        result_rate_limited, retry_after = result_rate_limit_meta(result)
        fetched_rows = len(result.data) if result.ok else 0
        live_df = pd.DataFrame()
        if result.ok:
            calls_success += 1
            live_df = result.data
            write_csv(live_df, raw_dir / f"tiingo_{symbol}_cache_backfill.csv")
            messages.append(f"{symbol}: {fetched_rows} rows")
        else:
            messages.append(f"{symbol}: {result.message}")

        merged = merge_price_history(cache_df, live_df, symbol)
        after_rows = valid_history_row_count(merged)
        if after_rows > 0:
            write_tiingo_cache(settings, symbol, merged)
        if result.ok and after_rows >= MIN_HISTORY_ROWS:
            loaded_symbols.append(symbol)

        if result_rate_limited:
            rate_limited = True
            stopped_after_429 = True
            retry_after_seconds = retry_after

        quality_rows.append(
            cache_quality_row(
                run_date,
                symbol,
                weight,
                before_rows,
                after_rows,
                fetched_rows=fetched_rows,
                was_requested=True,
                ok=result.ok and after_rows >= MIN_HISTORY_ROWS,
                rate_limited=result_rate_limited,
                stopped_after_429=result_rate_limited,
                retry_after_seconds=retry_after,
                message=result.message,
            )
        )

    cache_quality = pd.DataFrame(quality_rows, columns=CACHE_QUALITY_COLUMNS)
    api_usage = pd.DataFrame(
        [
            api_usage_row(
                settings,
                run_date,
                "tiingo",
                "daily_prices_cache_backfill",
                calls_attempted,
                calls_success,
                symbols_requested=attempted_symbols,
                symbols_loaded=loaded_symbols,
                rate_limited=rate_limited,
                stopped_after_429=stopped_after_429,
                retry_after_seconds=retry_after_seconds,
                message="; ".join(messages[:5]) + ("; ..." if len(messages) > 5 else ""),
            )
        ],
        columns=API_USAGE_COLUMNS,
    )
    manifest = {
        "ok": True,
        "run_date": run_date,
        "symbols_total": int(len(holdings)),
        "symbols_complete": int(cache_quality["is_complete"].sum()) if not cache_quality.empty else 0,
        "calls_attempted": calls_attempted,
        "calls_success": calls_success,
        "rate_limited": rate_limited,
        "stopped_after_429": stopped_after_429,
        "retry_after_seconds": retry_after_seconds,
    }
    return cache_quality, api_usage, manifest


def run_backfill(as_of: str = "auto", max_calls: int | None = None) -> dict:
    settings = Settings()
    settings.ensure_dirs()
    run_date = as_of_date(as_of)
    processed_dir = settings.paths.processed_dir / run_date
    latest_dir = settings.paths.reports_latest_dir
    processed_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    timeout = settings.pipeline.get("api", {}).get("request_timeout_seconds", 30)
    retry_count = settings.pipeline.get("api", {}).get("retry_count", 2)
    tiingo_cfg = provider_config(settings, "tiingo")
    tiingo = TiingoProvider(
        settings.get_secret(tiingo_cfg.get("api_key_env", "TIINGO_API_TOKEN")),
        tiingo_cfg.get("base_url", "https://api.tiingo.com/tiingo"),
        timeout=timeout,
        retry_count=retry_count,
    )

    cache_quality, api_usage, manifest = backfill_price_cache(settings, tiingo, run_date, max_calls=max_calls)
    write_csv(cache_quality, processed_dir / "cache_quality.csv")
    write_csv(api_usage, processed_dir / "price_cache_api_usage.csv")
    write_csv(cache_quality, latest_dir / "cache_quality.csv")
    write_csv(api_usage, latest_dir / "price_cache_api_usage.csv")

    manifest["latest_files"] = {
        "cache_quality_csv": rel_path(latest_dir / "cache_quality.csv", settings.paths.root),
        "price_cache_api_usage_csv": rel_path(latest_dir / "price_cache_api_usage.csv", settings.paths.root),
        "state_manifest_json": rel_path(settings.paths.state_dir / "latest_cache_backfill_manifest.json", settings.paths.root),
    }
    manifest["cache_quality_summary"] = cache_quality.to_dict(orient="records")
    manifest["api_usage"] = api_usage.to_dict(orient="records")
    write_json(manifest, settings.paths.state_dir / "latest_cache_backfill_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
