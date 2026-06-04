from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult


class FREDProvider(BaseProvider):
    provider_name = "fred"

    def observations(self, series_id: str, limit: int = 365) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("observations")
        url = f"{self.base_url}/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        try:
            data = self.request_json(url, params=params)
            observations = data.get("observations", [])
            rows = []
            for obs in observations:
                value = obs.get("value")
                try:
                    value_float = float(value)
                except Exception:
                    value_float = None
                rows.append({
                    "date": obs.get("date"),
                    "series_id": series_id,
                    "value": value_float,
                    "source": self.provider_name,
                })
            df = pd.DataFrame(rows).sort_values("date")
            return ProviderResult(self.provider_name, True, df, f"{series_id}: {len(df)} rows", data)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), str(exc))
