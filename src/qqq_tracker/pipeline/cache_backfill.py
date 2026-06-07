from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict

import pandas as pd

from qqq_tracker.io import write_csv, write_json
from qqq_tracker.providers import TiingoProvider, TwelveDataProvider
from qqq_tracker.settings import Settings

from .daily_run import (
    API_USAGE_COLUMNS,
    FULL_HISTORY_DAYS,
    INCREMENTAL_LOOKBACK_DAYS,
    MIN_HISTORY_ROWS,
    api_usage_row,
    cache_path_for_symbol,
    determine_target_date,
    history_cache_status,
    history_freshness,
    history_price_column,
    history_target_alignment,
    load_previous_csv,
    load_tiingo_cache,
    merge_price_history,
    normalize_symbol,
    provider_config,
    rel_path,
    result_rate_limit_meta,
    write_tiingo_cache,
)

CACHE_QUALITY_COLUMNS = [
    "run_date",
    "symbol",
    "weight",
    "provider",
    "history_sources",
    "cache_path",
    "price_column",
    "latest_date",
    "target_date",
    "staleness_days",
    "ma200_ready",
    "before_rows",
    "after_rows",
    "fetched_rows",
    "is_complete",
    "is_fresh",
    "is_qualified",
    "is_target_date",
    "needs_target_update",
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


def load_missing_top_weight_symbols(settings: Settings) -> list[str]:
    quality = load_previous_csv(settings, "data_quality.csv", [])
    if quality.empty or "missing_top_weight_symbols" not in quality.columns:
        return []
    breadth = quality[quality.get("dataset", pd.Series(dtype="object")).eq("breadth_metrics")]
    if breadth.empty:
        return []
    value = breadth.iloc[-1].get("missing_top_weight_symbols")
    if pd.isna(value):
        return []
    return [normalize_symbol(symbol) for symbol in str(value).split(",") if symbol.strip()]


def prioritize_backfill_holdings(settings: Settings, holdings: pd.DataFrame) -> pd.DataFrame:
    prepared = prepare_backfill_holdings(holdings)
    if prepared.empty:
        return prepared
    missing_top = set(load_missing_top_weight_symbols(settings))
    top20 = set(prepared.head(20)["symbol"])
    top40 = set(prepared.head(40)["symbol"])

    def priority(symbol: str) -> int:
        if symbol in missing_top:
            return 0
        if symbol in top20:
            return 1
        if symbol in top40:
            return 2
        return 3

    prepared["priority_group"] = prepared["symbol"].map(priority)
    return prepared.sort_values(["priority_group", "weight", "symbol"], ascending=[True, False, True]).reset_index(drop=True)


def cache_quality_row(
    run_date: str,
    symbol: str,
    weight: float,
    before_rows: int,
    after_rows: int,
    provider: str = "tiingo",
    fetched_rows: int = 0,
    was_requested: bool = False,
    ok: bool = False,
    rate_limited: bool = False,
    stopped_after_429: bool = False,
    retry_after_seconds: float | None = None,
    message: str = "",
    history_sources: str = "",
    cache_path: str = "",
    price_column: str = "",
    latest_date: str | None = None,
    target_date: str | None = None,
) -> Dict:
    staleness_days, is_fresh = history_freshness(latest_date, run_date)
    is_complete = int(after_rows) >= MIN_HISTORY_ROWS
    is_qualified = is_complete and is_fresh
    is_target_date, is_before_target = history_target_alignment(latest_date, target_date)
    return {
        "run_date": run_date,
        "symbol": normalize_symbol(symbol),
        "weight": weight,
        "provider": provider,
        "history_sources": history_sources,
        "cache_path": cache_path,
        "price_column": price_column,
        "latest_date": latest_date,
        "target_date": target_date,
        "staleness_days": staleness_days,
        "ma200_ready": int(after_rows) >= 200,
        "before_rows": int(before_rows),
        "after_rows": int(after_rows),
        "fetched_rows": int(fetched_rows),
        "is_complete": is_complete,
        "is_fresh": is_fresh,
        "is_qualified": is_qualified,
        "is_target_date": is_target_date,
        "needs_target_update": is_qualified and is_before_target,
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
    twelve_data: TwelveDataProvider | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    holdings = prioritize_backfill_holdings(settings, load_backfill_holdings(settings, run_date))
    target_date, target_date_source = determine_target_date(settings, run_date)
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
    fallback_candidates: list[dict] = []

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
        before_status = history_cache_status(cache_df, run_date, target_date)
        before_rows = int(before_status["valid_rows"])
        before_latest_date = before_status["latest_date"]

        if (before_status["is_qualified"] and not before_status["needs_target_update"]) or before_status["is_after_target"]:
            primary_cache_path = cache_path_for_symbol(settings, symbol)
            if not primary_cache_path.exists():
                write_tiingo_cache(settings, symbol, cache_df)
            sources = ",".join(sorted(cache_df.get("source", pd.Series(dtype="object")).dropna().astype(str).unique()))
            quality_rows.append(
                cache_quality_row(
                    run_date,
                    symbol,
                    weight,
                    before_rows,
                    before_rows,
                    ok=True,
                    history_sources=sources,
                    cache_path=rel_path(primary_cache_path, settings.paths.root),
                    price_column=history_price_column(cache_df) or "",
                    latest_date=before_latest_date,
                    target_date=target_date,
                    message=(
                        "cache complete, fresh and target-aligned"
                        if before_status["is_target_date"]
                        else "cache complete and fresh but latest date is after target date"
                    ),
                )
            )
            continue

        if stopped_after_429:
            row = cache_quality_row(
                run_date,
                symbol,
                weight,
                before_rows,
                before_rows,
                ok=False,
                rate_limited=True,
                stopped_after_429=True,
                retry_after_seconds=retry_after_seconds,
                latest_date=before_latest_date,
                target_date=target_date,
                message="skipped after Tiingo 429",
            )
            fallback_candidates.append({"row": row, "symbol": symbol, "weight": weight, "before_rows": before_rows, "reason": "tiingo_429_skip"})
            quality_rows.append(row)
            continue

        if calls_attempted >= max_calls_per_run:
            quality_rows.append(
                cache_quality_row(
                    run_date,
                    symbol,
                    weight,
                    before_rows,
                    before_rows,
                    ok=False,
                    latest_date=before_latest_date,
                    target_date=target_date,
                    message="skipped after max_calls limit",
                )
            )
            continue

        attempted_symbols.append(symbol)
        calls_attempted += 1
        end = run_date
        full_history_start = (datetime.fromisoformat(end) - timedelta(days=FULL_HISTORY_DAYS)).date()
        start_date = full_history_start
        if before_status["is_complete"] and before_latest_date is not None:
            incremental_start = datetime.fromisoformat(str(before_latest_date)).date() - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)
            start_date = max(incremental_start, full_history_start)
        start = start_date.isoformat()
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
        after_status = history_cache_status(merged, run_date, target_date)
        after_rows = int(after_status["valid_rows"])
        if after_rows > 0:
            write_tiingo_cache(settings, symbol, merged)
        if result.ok and after_status["is_qualified"] and not after_status["needs_target_update"]:
            loaded_symbols.append(symbol)

        if result_rate_limited:
            rate_limited = True
            stopped_after_429 = True
            retry_after_seconds = retry_after

        row = cache_quality_row(
            run_date,
            symbol,
            weight,
            before_rows,
            after_rows,
            fetched_rows=fetched_rows,
            was_requested=True,
            ok=result.ok and bool(after_status["is_qualified"]) and not bool(after_status["needs_target_update"]),
            rate_limited=result_rate_limited,
            stopped_after_429=result_rate_limited,
            retry_after_seconds=retry_after,
            message=result.message,
            history_sources=",".join(sorted(merged.get("source", pd.Series(dtype="object")).dropna().astype(str).unique())),
            cache_path=rel_path(cache_path_for_symbol(settings, symbol), settings.paths.root),
            price_column=history_price_column(merged) or "",
            latest_date=after_status["latest_date"],
            target_date=target_date,
        )
        if (
            not result.ok
            or fetched_rows == 0
            or not after_status["is_qualified"]
            or after_status["needs_target_update"]
        ) and calls_attempted <= max_calls_per_run:
            fallback_candidates.append({"row": row, "symbol": symbol, "weight": weight, "before_rows": before_rows, "reason": "tiingo_failed_or_incomplete"})
        quality_rows.append(row)

    twelve_usage = run_twelve_data_fallback(settings, twelve_data, run_date, target_date, raw_dir, fallback_candidates)

    cache_quality = pd.DataFrame(quality_rows, columns=CACHE_QUALITY_COLUMNS)
    for candidate in fallback_candidates:
        row = candidate["row"]
        if row.get("provider") not in {"twelve_data", "tiingo+twelve_data"}:
            continue
        mask = cache_quality["symbol"].eq(row["symbol"])
        for col in CACHE_QUALITY_COLUMNS:
            cache_quality.loc[mask, col] = row[col]
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
    api_usage = pd.concat([api_usage, pd.DataFrame([twelve_usage], columns=API_USAGE_COLUMNS)], ignore_index=True)
    manifest = {
        "ok": True,
        "run_date": run_date,
        "target_date": target_date,
        "target_date_source": target_date_source,
        "symbols_total": int(len(holdings)),
        "symbols_complete": int(cache_quality["is_qualified"].sum()) if not cache_quality.empty else 0,
        "symbols_qualified": int(cache_quality["is_qualified"].sum()) if not cache_quality.empty else 0,
        "symbols_target_aligned": int((cache_quality["is_qualified"] & cache_quality["is_target_date"]).sum()) if not cache_quality.empty else 0,
        "symbols_needing_target_update": int(cache_quality["needs_target_update"].sum()) if not cache_quality.empty else 0,
        "calls_attempted": calls_attempted,
        "calls_success": calls_success,
        "rate_limited": rate_limited,
        "stopped_after_429": stopped_after_429,
        "retry_after_seconds": retry_after_seconds,
        "fallback_provider": "twelve_data",
        "twelve_data_calls_attempted": int(twelve_usage["calls_attempted"]),
        "twelve_data_symbols_loaded": twelve_usage["symbols_loaded"],
    }
    return cache_quality, api_usage, manifest


def run_twelve_data_fallback(
    settings: Settings,
    twelve_data: TwelveDataProvider | None,
    run_date: str,
    target_date: str,
    raw_dir: Path,
    candidates: list[dict],
) -> Dict:
    cfg = settings.api_limits.get("twelve_data", {})
    max_credits = int(cfg.get("max_credits_per_run", 160))
    batch_size = int(cfg.get("batch_size", cfg.get("max_credits_per_minute", 8)))
    sleep_seconds = float(cfg.get("sleep_seconds_between_batches", 70))
    outputsize = int(cfg.get("outputsize", 260))
    attempted: list[str] = []
    loaded: list[str] = []
    messages: list[str] = []
    calls_success = 0
    rate_limited = False
    retry_after_seconds = None

    if not candidates:
        return api_usage_row(settings, run_date, "twelve_data", "time_series_cache_fallback", 0, 0, message="no fallback candidates")
    if twelve_data is None or not getattr(twelve_data, "available", False):
        return api_usage_row(
            settings,
            run_date,
            "twelve_data",
            "time_series_cache_fallback",
            0,
            0,
            symbols_requested=[candidate["symbol"] for candidate in candidates],
            message="missing Twelve Data API key; fallback not called",
        )

    for candidate in candidates:
        if len(attempted) >= max_credits or rate_limited:
            break
        if attempted and batch_size > 0 and len(attempted) % batch_size == 0:
            time.sleep(sleep_seconds)
        symbol = candidate["symbol"]
        attempted.append(symbol)
        result = twelve_data.time_series(symbol, outputsize=outputsize, interval="1day")
        result_rate_limited, retry_after = result_rate_limit_meta(result)
        if result.ok and not result.data.empty:
            write_csv(result.data, raw_dir / f"twelve_data_{symbol}_cache_fallback.csv")
            cache_df = load_tiingo_cache(settings, symbol)
            merged = merge_price_history(cache_df, result.data, symbol)
            after_status = history_cache_status(merged, run_date, target_date)
            after_rows = int(after_status["valid_rows"])
            if after_rows > 0:
                write_tiingo_cache(settings, symbol, merged)
            row = candidate["row"]
            row["provider"] = "tiingo+twelve_data" if row.get("was_requested") else "twelve_data"
            row["after_rows"] = after_rows
            row["history_sources"] = ",".join(sorted(merged.get("source", pd.Series(dtype="object")).dropna().astype(str).unique()))
            row["cache_path"] = rel_path(cache_path_for_symbol(settings, symbol), settings.paths.root)
            row["price_column"] = history_price_column(merged) or ""
            row["latest_date"] = after_status["latest_date"]
            row["target_date"] = target_date
            row["staleness_days"] = after_status["staleness_days"]
            row["ma200_ready"] = after_rows >= 200
            row["fetched_rows"] = int(row.get("fetched_rows") or 0) + len(result.data)
            row["is_complete"] = bool(after_status["is_complete"])
            row["is_fresh"] = bool(after_status["is_fresh"])
            row["is_qualified"] = bool(after_status["is_qualified"])
            row["is_target_date"] = bool(after_status["is_target_date"])
            row["needs_target_update"] = bool(after_status["needs_target_update"])
            row["was_requested"] = True
            row["ok"] = bool(after_status["is_qualified"]) and not bool(after_status["needs_target_update"])
            row["message"] = f"{row.get('message')}; twelve_data fallback: {len(result.data)} rows"
            if after_status["is_qualified"] and not after_status["needs_target_update"]:
                calls_success += 1
                loaded.append(symbol)
            messages.append(f"{symbol}: {len(result.data)} rows")
        else:
            messages.append(f"{symbol}: {result.message}")
        if result_rate_limited:
            rate_limited = True
            retry_after_seconds = retry_after
            row = candidate["row"]
            row["provider"] = "twelve_data" if not row.get("was_requested") else "tiingo+twelve_data"
            row["rate_limited"] = True
            row["stopped_after_429"] = True
            row["retry_after_seconds"] = retry_after
            row["message"] = f"{row.get('message')}; twelve_data rate limited: {result.message}"

    return api_usage_row(
        settings,
        run_date,
        "twelve_data",
        "time_series_cache_fallback",
        len(attempted),
        calls_success,
        symbols_requested=attempted,
        symbols_loaded=loaded,
        rate_limited=rate_limited,
        stopped_after_429=rate_limited,
        retry_after_seconds=retry_after_seconds,
        credits_used=len(attempted),
        message="; ".join(messages[:5]) + ("; ..." if len(messages) > 5 else ""),
    )


def repair_price_cache_with_twelve_data(
    settings: Settings,
    twelve_data: TwelveDataProvider,
    run_date: str,
    max_calls: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    holdings = prioritize_backfill_holdings(settings, load_backfill_holdings(settings, run_date))
    target_date, target_date_source = determine_target_date(settings, run_date)
    cfg = settings.api_limits.get("twelve_data", {})
    max_credits = int(max_calls if max_calls is not None else cfg.get("max_time_series_symbols_backfill", 80))
    batch_size = int(cfg.get("time_series_batch_size", cfg.get("max_credits_per_minute", 8)))
    sleep_seconds = float(cfg.get("sleep_seconds_between_batches", 70))
    outputsize = int(cfg.get("outputsize", 260))
    raw_dir = settings.paths.raw_dir / run_date
    quality_rows: list[Dict] = []
    attempted: list[str] = []
    loaded: list[str] = []
    messages: list[str] = []
    rate_limited = False
    retry_after_seconds = None

    for _, holding in holdings.iterrows():
        symbol = holding["symbol"]
        weight = float(holding.get("weight", 0.0))
        cache_df = load_tiingo_cache(settings, symbol)
        before_status = history_cache_status(cache_df, run_date, target_date)
        before_rows = int(before_status["valid_rows"])
        if (before_status["is_qualified"] and not before_status["needs_target_update"]) or before_status["is_after_target"]:
            continue
        if len(attempted) >= max_credits or rate_limited:
            break
        if attempted and batch_size > 0 and len(attempted) % batch_size == 0:
            time.sleep(sleep_seconds)
        attempted.append(symbol)
        result = twelve_data.time_series(symbol, outputsize=outputsize, interval="1day")
        limited, retry_after = result_rate_limit_meta(result)
        merged = merge_price_history(cache_df, result.data if result.ok else pd.DataFrame(), symbol)
        after_status = history_cache_status(merged, run_date, target_date)
        after_rows = int(after_status["valid_rows"])
        if after_rows > 0:
            write_tiingo_cache(settings, symbol, merged)
        if result.ok:
            write_csv(result.data, raw_dir / f"twelve_data_{symbol}_history_repair.csv")
        if after_status["is_qualified"] and not after_status["needs_target_update"]:
            loaded.append(symbol)
        quality_rows.append(
            cache_quality_row(
                run_date,
                symbol,
                weight,
                before_rows,
                after_rows,
                provider="twelve_data",
                fetched_rows=len(result.data) if result.ok else 0,
                was_requested=True,
                ok=bool(after_status["is_qualified"]) and not bool(after_status["needs_target_update"]),
                rate_limited=limited,
                stopped_after_429=limited,
                retry_after_seconds=retry_after,
                history_sources=",".join(sorted(merged.get("source", pd.Series(dtype="object")).dropna().astype(str).unique())),
                cache_path=rel_path(cache_path_for_symbol(settings, symbol), settings.paths.root),
                price_column=history_price_column(merged) or "",
                latest_date=after_status["latest_date"],
                target_date=target_date,
                message=result.message,
            )
        )
        messages.append(f"{symbol}: {result.message}")
        if limited:
            rate_limited = True
            retry_after_seconds = retry_after

    usage = pd.DataFrame(
        [
            api_usage_row(
                settings,
                run_date,
                "twelve_data",
                "time_series_history_repair",
                len(attempted),
                len(loaded),
                symbols_requested=attempted,
                symbols_loaded=loaded,
                rate_limited=rate_limited,
                stopped_after_429=rate_limited,
                retry_after_seconds=retry_after_seconds,
                credits_used=len(attempted),
                message="; ".join(messages[:5]) + ("; ..." if len(messages) > 5 else ""),
            )
        ],
        columns=API_USAGE_COLUMNS,
    )
    quality = pd.DataFrame(quality_rows, columns=CACHE_QUALITY_COLUMNS)
    manifest = {
        "ok": True,
        "run_date": run_date,
        "target_date": target_date,
        "target_date_source": target_date_source,
        "provider": "twelve_data",
        "calls_attempted": len(attempted),
        "symbols_loaded": loaded,
        "rate_limited": rate_limited,
    }
    return quality, usage, manifest


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
    twelve_cfg = provider_config(settings, "twelve_data")
    twelve_data = TwelveDataProvider(
        settings.get_secret(twelve_cfg.get("api_key_env", "TWELVE_DATA_API_KEY")),
        twelve_cfg.get("base_url", "https://api.twelvedata.com"),
        timeout=timeout,
        retry_count=retry_count,
    )

    cache_quality, api_usage, manifest = backfill_price_cache(settings, tiingo, run_date, max_calls=max_calls, twelve_data=twelve_data)
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


def run_history_repair(as_of: str = "auto", max_calls: int | None = None) -> dict:
    settings = Settings()
    settings.ensure_dirs()
    run_date = as_of_date(as_of)
    timeout = settings.pipeline.get("api", {}).get("request_timeout_seconds", 30)
    retry_count = settings.pipeline.get("api", {}).get("retry_count", 2)
    cfg = provider_config(settings, "twelve_data")
    twelve_data = TwelveDataProvider(
        settings.get_secret(cfg.get("api_key_env", "TWELVE_DATA_API_KEY")),
        cfg.get("base_url", "https://api.twelvedata.com"),
        timeout=timeout,
        retry_count=retry_count,
    )
    quality, usage, manifest = repair_price_cache_with_twelve_data(settings, twelve_data, run_date, max_calls=max_calls)
    processed_dir = settings.paths.processed_dir / run_date
    write_csv(quality, processed_dir / "twelve_data_cache_quality.csv")
    write_csv(usage, processed_dir / "twelve_data_history_api_usage.csv")
    write_csv(quality, settings.paths.reports_latest_dir / "twelve_data_cache_quality.csv")
    write_csv(usage, settings.paths.reports_latest_dir / "twelve_data_history_api_usage.csv")
    write_json(manifest, settings.paths.state_dir / "latest_twelve_data_history_repair_manifest.json")
    return manifest
