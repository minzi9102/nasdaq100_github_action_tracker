from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult, RateLimitError


class FMPProvider(BaseProvider):
    provider_name = "fmp"

    def quote(self, symbol: str) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("quote")
        url = f"{self.base_url}/quote"
        params = {"symbol": symbol, "apikey": self.api_key}
        try:
            data = self.request_json(url, params=params)
            if isinstance(data, dict) and data.get("Error Message"):
                return ProviderResult(self.provider_name, False, pd.DataFrame(), str(data), data)
            rows = data if isinstance(data, list) else [data]
            df = pd.DataFrame(rows)
            if not df.empty:
                df["source"] = self.provider_name
            return ProviderResult(self.provider_name, True, df, f"{symbol}: quote rows={len(df)}", data)
        except RateLimitError as exc:
            return ProviderResult(
                self.provider_name,
                False,
                pd.DataFrame(),
                str(exc),
                {"rate_limited": True, "retry_after_seconds": exc.retry_after_seconds, "symbol": symbol},
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc), {"rate_limited": False, "symbol": symbol})

    def key_metrics(self, symbol: str, limit: int = 5) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("key_metrics")
        url = f"{self.base_url}/key-metrics"
        params = {"symbol": symbol, "limit": limit, "apikey": self.api_key}
        try:
            data = self.request_json(url, params=params)
            rows = data if isinstance(data, list) else [data]
            df = pd.DataFrame(rows)
            if not df.empty:
                df["symbol"] = symbol
                df["source"] = self.provider_name
            return ProviderResult(self.provider_name, True, df, f"{symbol}: key_metrics rows={len(df)}", data)
        except RateLimitError as exc:
            return ProviderResult(
                self.provider_name,
                False,
                pd.DataFrame(),
                str(exc),
                {"rate_limited": True, "retry_after_seconds": exc.retry_after_seconds, "symbol": symbol},
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc), {"rate_limited": False, "symbol": symbol})

    def batch_quote(self, symbols: list[str], chunk_size: int = 200, fallback_to_single: bool = True) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("batch_quote")
        cleaned = [symbol.strip().upper() for symbol in symbols if str(symbol).strip()]
        if not cleaned:
            return ProviderResult(self.provider_name, True, pd.DataFrame(), "batch_quote: no symbols requested", [])

        url = f"{self.base_url}/quote"
        frames: list[pd.DataFrame] = []
        raw_payloads: list[object] = []
        missing_symbols: list[str] = []
        calls_attempted = 0
        calls_success = 0
        for offset in range(0, len(cleaned), chunk_size):
            chunk = cleaned[offset : offset + chunk_size]
            params = {"symbol": ",".join(chunk), "apikey": self.api_key}
            try:
                calls_attempted += 1
                data = self.request_json(url, params=params)
                calls_success += 1
                raw_payloads.append(data)
                rows = data if isinstance(data, list) else [data]
                df = pd.DataFrame(rows)
                if not df.empty:
                    df["symbol"] = df.get("symbol", pd.Series(chunk[: len(df)]))
                    df["source"] = self.provider_name
                    frames.append(df)
            except RateLimitError as exc:
                return ProviderResult(
                    self.provider_name,
                    False,
                    pd.DataFrame(),
                    str(exc),
                    {
                        "rate_limited": True,
                        "retry_after_seconds": exc.retry_after_seconds,
                        "symbols": chunk,
                        "calls_attempted": calls_attempted,
                        "calls_success": calls_success,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                if not fallback_to_single:
                    return ProviderResult(
                        self.provider_name,
                        False,
                        pd.DataFrame(),
                        str(exc),
                        {
                            "rate_limited": False,
                            "symbols": chunk,
                            "missing_symbols": chunk,
                            "calls_attempted": calls_attempted,
                            "calls_success": calls_success,
                        },
                    )
                for symbol in chunk:
                    single = self.quote(symbol)
                    if single.ok and not single.data.empty:
                        frames.append(single.data)
                        raw_payloads.append(single.raw)
                    else:
                        missing_symbols.append(symbol)
        merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        ok = not merged.empty
        message = f"batch_quote: rows={len(merged)}"
        if missing_symbols:
            message = f"{message}; missing={','.join(missing_symbols)}"
        return ProviderResult(
            self.provider_name,
            ok,
            merged,
            message,
            {
                "payloads": raw_payloads,
                "missing_symbols": missing_symbols,
                "rate_limited": False,
                "calls_attempted": calls_attempted,
                "calls_success": calls_success,
            },
        )

    def stable_batch_quote(self, symbols: list[str]) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("stable_batch_quote")
        cleaned = [symbol.strip().upper() for symbol in symbols if str(symbol).strip()]
        try:
            data = self.request_json(
                f"{self.base_url}/batch-quote",
                params={"symbols": ",".join(cleaned), "apikey": self.api_key},
            )
            rows = data if isinstance(data, list) else [data]
            df = pd.DataFrame(rows)
            if not df.empty:
                df["source"] = self.provider_name
            return ProviderResult(self.provider_name, not df.empty, df, f"stable_batch_quote: rows={len(df)}", data)
        except RateLimitError as exc:
            return ProviderResult(
                self.provider_name,
                False,
                pd.DataFrame(),
                str(exc),
                {"rate_limited": True, "retry_after_seconds": exc.retry_after_seconds, "symbols": cleaned},
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc), {"symbols": cleaned})
