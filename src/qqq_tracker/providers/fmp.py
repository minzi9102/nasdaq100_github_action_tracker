from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult


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
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc))

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
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc))
