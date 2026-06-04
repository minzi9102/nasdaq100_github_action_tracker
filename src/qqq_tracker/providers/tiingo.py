from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult


class TiingoProvider(BaseProvider):
    provider_name = "tiingo"

    def daily_prices(self, ticker: str, start_date: str | None = None, end_date: str | None = None) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("daily_prices")
        url = f"{self.base_url}/daily/{ticker}/prices"
        params = {"format": "json"}
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        headers = {"Authorization": f"Token {self.api_key}"}
        try:
            data = self.request_json(url, params=params, headers=headers)
            rows = data if isinstance(data, list) else [data]
            df = pd.DataFrame(rows)
            if not df.empty:
                df["symbol"] = ticker
                df["source"] = self.provider_name
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
            return ProviderResult(self.provider_name, True, df, f"{ticker}: {len(df)} rows", data)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc))
