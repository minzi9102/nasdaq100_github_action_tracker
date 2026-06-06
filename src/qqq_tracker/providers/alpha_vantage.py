from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult


class AlphaVantageProvider(BaseProvider):
    provider_name = "alpha_vantage"

    def daily(self, symbol: str, outputsize: str = "compact") -> ProviderResult:
        if not self.available:
            return self.unavailable_result("daily")
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": outputsize,
            "apikey": self.api_key,
        }
        try:
            data = self.request_json(self.base_url, params=params)
            if "Error Message" in data:
                return ProviderResult(self.provider_name, False, pd.DataFrame(), data["Error Message"], data)
            if "Note" in data or "Information" in data:
                return ProviderResult(self.provider_name, False, pd.DataFrame(), data.get("Note") or data.get("Information"), data)
            ts = data.get("Time Series (Daily)")
            if not ts:
                return ProviderResult(self.provider_name, False, pd.DataFrame(), "missing Time Series (Daily)", data)
            rows = []
            for date, v in ts.items():
                rows.append({
                    "date": date,
                    "symbol": symbol,
                    "open": float(v.get("1. open", 0)),
                    "high": float(v.get("2. high", 0)),
                    "low": float(v.get("3. low", 0)),
                    "close": float(v.get("4. close", 0)),
                    "adjusted_close": float(v.get("5. adjusted close", v.get("4. close", 0))),
                    "volume": float(v.get("6. volume", v.get("5. volume", 0))),
                    "source": self.provider_name,
                })
            df = pd.DataFrame(rows).sort_values("date")
            return ProviderResult(self.provider_name, True, df, f"{symbol}: {len(df)} rows", data)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc))

    def daily_adjusted(self, symbol: str, outputsize: str = "compact") -> ProviderResult:
        return self.daily(symbol, outputsize=outputsize)
