from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult, RateLimitError


class TwelveDataProvider(BaseProvider):
    provider_name = "twelve_data"

    def quote(self, symbol: str) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("quote")
        url = f"{self.base_url}/quote"
        params = {
            "symbol": symbol,
            "format": "JSON",
            "apikey": self.api_key,
        }
        try:
            data = self.request_json(url, params=params)
            if isinstance(data, dict) and data.get("status") == "error":
                return self._error_result("quote", symbol, data)
            df = self._normalize_quote(data, symbol)
            return ProviderResult(self.provider_name, not df.empty, df, f"{symbol}: quote rows={len(df)}", data)
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

    def time_series(self, symbol: str, outputsize: int = 260, interval: str = "1day") -> ProviderResult:
        if not self.available:
            return self.unavailable_result("time_series")
        url = f"{self.base_url}/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "format": "JSON",
            "apikey": self.api_key,
        }
        try:
            data = self.request_json(url, params=params)
            if isinstance(data, dict) and data.get("status") == "error":
                return self._error_result("time_series", symbol, data)
            df = self._normalize_time_series(data, symbol)
            return ProviderResult(self.provider_name, True, df, f"{symbol}: {len(df)} rows", data)
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

    def _error_result(self, method: str, symbol: str, data: dict) -> ProviderResult:
        message = str(data.get("message") or data)
        code = str(data.get("code") or "")
        lower_message = message.lower()
        is_rate_limited = code == "429" or "limit" in lower_message or "credit" in lower_message
        raw = {"rate_limited": is_rate_limited, "symbol": symbol, "method": method, "payload": data}
        return ProviderResult(self.provider_name, False, pd.DataFrame(), message, raw)

    def _normalize_quote(self, data: object, fallback_symbol: str) -> pd.DataFrame:
        if not isinstance(data, dict) or not data:
            return pd.DataFrame()
        row = dict(data)
        symbol = str(row.get("symbol") or fallback_symbol).strip().upper().replace(".", "-")
        normalized = {
            "symbol": symbol,
            "price": row.get("close") or row.get("price"),
            "change": row.get("change"),
            "changesPercentage": row.get("percent_change") or row.get("changesPercentage"),
            "marketCap": row.get("market_cap") or row.get("marketCap"),
            "pe": row.get("pe") or row.get("peRatio"),
            "eps": row.get("eps"),
            "timestamp": row.get("timestamp"),
            "date": row.get("datetime") or row.get("date"),
            "source": self.provider_name,
        }
        df = pd.DataFrame([normalized])
        for col in ["price", "change", "changesPercentage", "marketCap", "pe", "eps", "timestamp"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _normalize_time_series(self, data: object, fallback_symbol: str) -> pd.DataFrame:
        rows: list[dict] = []

        def add_values(symbol: str, payload: dict) -> None:
            for item in payload.get("values") or []:
                if isinstance(item, dict):
                    row = dict(item)
                    row["symbol"] = symbol
                    rows.append(row)

        if isinstance(data, dict) and "values" in data:
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            add_values(str(meta.get("symbol") or fallback_symbol), data)
        elif isinstance(data, dict):
            for key, payload in data.items():
                if isinstance(payload, dict) and "values" in payload:
                    add_values(str(key), payload)

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper().str.replace(".", "-", regex=False)
        if "datetime" in df.columns:
            df["date"] = pd.to_datetime(df["datetime"], errors="coerce").dt.date.astype(str)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["source"] = self.provider_name
        return df.reindex(columns=["date", "open", "high", "low", "close", "volume", "symbol", "source"]).dropna(subset=["date"])
