from __future__ import annotations

import json
import re
from datetime import date
from typing import Callable

import pandas as pd

from qqq_tracker.io import write_csv, write_json
from qqq_tracker.providers import AlphaVantageProvider, FMPProvider, TwelveDataProvider
from qqq_tracker.providers.base import ProviderResult
from qqq_tracker.settings import Settings

from .daily_run import provider_config, rel_path

CAPABILITY_COLUMNS = [
    "provider",
    "endpoint",
    "symbol_or_symbols",
    "http_status",
    "rows",
    "usable_for_production",
    "error_type",
    "message",
]


def classify_probe_result(result: ProviderResult) -> tuple[str, int | None]:
    message = str(result.message or "")
    lower = message.lower()
    status_match = re.search(r"\b(402|429|4\d\d|5\d\d)\b", message)
    http_status = int(status_match.group(1)) if status_match else (200 if result.raw is not None else None)
    if result.ok and not result.data.empty:
        return "success", http_status or 200
    if "premium" in lower:
        return "premium_blocked", http_status or 200
    if http_status == 402 or "payment required" in lower or "permission" in lower:
        return "permission_limited", http_status
    raw = result.raw if isinstance(result.raw, dict) else {}
    if http_status == 429 or raw.get("rate_limited") or "rate limit" in lower or "credits limit" in lower:
        return "rate_limited", http_status or 429
    if result.ok and result.data.empty:
        return "parse_error", http_status or 200
    return "api_error", http_status


def probe_row(
    provider: str,
    endpoint: str,
    symbols: str,
    call: Callable[[], ProviderResult],
    production_candidate: bool,
) -> dict:
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001
        result = ProviderResult(provider, False, pd.DataFrame(), str(exc))
    error_type, http_status = classify_probe_result(result)
    rows = len(result.data)
    return {
        "provider": provider,
        "endpoint": endpoint,
        "symbol_or_symbols": symbols,
        "http_status": http_status,
        "rows": rows,
        "usable_for_production": bool(production_candidate and error_type == "success" and rows > 0),
        "error_type": error_type,
        "message": result.message,
    }


def run_probe_calls(
    alpha: AlphaVantageProvider,
    fmp: FMPProvider,
    twelve: TwelveDataProvider,
) -> pd.DataFrame:
    calls = [
        ("alpha_vantage", "TIME_SERIES_DAILY compact", "QQQ", lambda: alpha.daily("QQQ", outputsize="compact"), True),
        ("alpha_vantage", "TIME_SERIES_DAILY full", "QQQ", lambda: alpha.daily("QQQ", outputsize="full"), False),
        ("fmp", "quote", "AAPL", lambda: fmp.quote("AAPL"), False),
        ("fmp", "quote", "AVGO", lambda: fmp.quote("AVGO"), False),
        ("fmp", "quote batch", "AAPL,MSFT", lambda: fmp.batch_quote(["AAPL", "MSFT"], fallback_to_single=False), False),
        ("fmp", "stable/batch-quote", "AAPL,MSFT", lambda: fmp.stable_batch_quote(["AAPL", "MSFT"]), False),
        ("twelve_data", "quote", "GOOG", lambda: twelve.quote("GOOG"), True),
        ("twelve_data", "quote", "AVGO", lambda: twelve.quote("AVGO"), True),
        ("twelve_data", "time_series", "GOOG", lambda: twelve.time_series("GOOG", outputsize=260), True),
        ("twelve_data", "time_series", "AVGO", lambda: twelve.time_series("AVGO", outputsize=260), True),
    ]
    return pd.DataFrame(
        [probe_row(provider, endpoint, symbols, call, candidate) for provider, endpoint, symbols, call, candidate in calls],
        columns=CAPABILITY_COLUMNS,
    )


def run_capability_probe(as_of: str = "auto") -> dict:
    settings = Settings()
    settings.ensure_dirs()
    run_date = date.today().isoformat() if as_of == "auto" else as_of
    timeout = settings.pipeline.get("api", {}).get("request_timeout_seconds", 30)
    retry_count = settings.pipeline.get("api", {}).get("retry_count", 2)
    av_cfg = provider_config(settings, "alpha_vantage")
    fmp_cfg = provider_config(settings, "fmp")
    twelve_cfg = provider_config(settings, "twelve_data")
    results = run_probe_calls(
        AlphaVantageProvider(
            settings.get_secret(av_cfg.get("api_key_env", "ALPHA_VANTAGE_API_KEY")),
            av_cfg.get("base_url", "https://www.alphavantage.co/query"),
            timeout=timeout,
            retry_count=retry_count,
        ),
        FMPProvider(
            settings.get_secret(fmp_cfg.get("api_key_env", "FMP_API_KEY")),
            fmp_cfg.get("base_url", "https://financialmodelingprep.com/stable"),
            timeout=timeout,
            retry_count=retry_count,
        ),
        TwelveDataProvider(
            settings.get_secret(twelve_cfg.get("api_key_env", "TWELVE_DATA_API_KEY")),
            twelve_cfg.get("base_url", "https://api.twelvedata.com"),
            timeout=timeout,
            retry_count=retry_count,
        ),
    )
    processed_path = settings.paths.processed_dir / run_date / "provider_capability_probe.csv"
    latest_path = settings.paths.reports_latest_dir / "provider_capability_probe.csv"
    write_csv(results, processed_path)
    write_csv(results, latest_path)
    manifest = {
        "ok": True,
        "run_date": run_date,
        "generated_rows": len(results),
        "latest_file": rel_path(latest_path, settings.paths.root),
        "results": results.to_dict(orient="records"),
    }
    write_json(manifest, settings.paths.state_dir / "latest_provider_capability_probe_manifest.json")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
