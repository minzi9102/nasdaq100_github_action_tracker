from __future__ import annotations

from typing import Any

import pandas as pd

from .base import APIError, BaseProvider, ProviderResult, sanitize_error_message


DEFAULT_HOLDINGS_URL = (
    "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/"
    "holdings/fund?idType=ticker&interval=monthly&productType=ETF"
)


class InvescoProvider(BaseProvider):
    provider_name = "invesco"

    def __init__(self, holdings_url: str | None = None, timeout: int = 30, retry_count: int = 2) -> None:
        super().__init__(api_key="public", base_url="https://dng-api.invesco.com", timeout=timeout, retry_count=retry_count)
        self.holdings_url = (holdings_url or DEFAULT_HOLDINGS_URL).strip()

    @property
    def available(self) -> bool:
        return bool(self.holdings_url)

    def qqq_holdings(self, ticker: str = "QQQ") -> ProviderResult:
        if not self.available:
            return ProviderResult(self.provider_name, False, pd.DataFrame(), "qqq_holdings: missing holdings URL")
        try:
            payload = self.request_json(self.holdings_url, headers=self._headers())
            df = self._normalize_payload(payload)
            if df.empty:
                total = payload.get("totalNumberOfHoldings") if isinstance(payload, dict) else None
                return ProviderResult(
                    self.provider_name,
                    False,
                    pd.DataFrame(),
                    f"{ticker}: parsed 0 rows from dng-api payload; declared_total={total}",
                    payload,
                )
            return ProviderResult(
                self.provider_name,
                True,
                df,
                f"{ticker}: holdings rows={len(df)} effective_date={payload.get('effectiveDate')}",
                payload,
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(self.provider_name, False, pd.DataFrame(), sanitize_error_message(str(exc)))

    def request_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                r = self._request(url, params=params, headers=headers)
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise APIError(sanitize_error_message(str(last_error)))

    def _request(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None):
        import requests

        response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "HeadlessChrome/148.0.7778.96 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.invesco.com",
            "Referer": "https://www.invesco.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "Connection": "keep-alive",
        }

    def _normalize_payload(self, payload: dict[str, Any]) -> pd.DataFrame:
        holdings = payload.get("holdings") or []
        if not holdings:
            return pd.DataFrame(
                columns=[
                    "date",
                    "symbol",
                    "company_name",
                    "weight",
                    "sector",
                    "security_type_code",
                    "security_type_name",
                    "source",
                ]
            )
        df = pd.DataFrame(holdings)
        for col in ["ticker", "issuerName", "percentageOfTotalNetAssets", "securityTypeCode", "securityTypeName"]:
            if col not in df.columns:
                df[col] = None
        out = pd.DataFrame()
        out["date"] = pd.Series(payload.get("effectiveDate"), index=df.index)
        out["symbol"] = df["ticker"].astype(str).str.strip().str.upper()
        out["company_name"] = df["issuerName"].astype(str).str.strip()
        out["weight"] = pd.to_numeric(df["percentageOfTotalNetAssets"], errors="coerce") / 100.0
        out["sector"] = ""
        out["security_type_code"] = df["securityTypeCode"].astype("string").str.strip().str.upper()
        out["security_type_name"] = df["securityTypeName"].astype("string").str.strip()
        out["source"] = self.provider_name
        out = out.replace(
            {
                "symbol": {"NAN": None, "NONE": None, "": None},
                "company_name": {"nan": None, "None": None, "": None},
                "security_type_code": {"<NA>": None, "NAN": None, "NONE": None, "": None},
                "security_type_name": {"<NA>": None, "nan": None, "None": None, "": None},
            }
        )
        out = out[out["symbol"].notna() & out["company_name"].notna() & out["weight"].notna()].copy()
        out = out.drop_duplicates(subset=["symbol"]).sort_values(["weight", "symbol"], ascending=[False, True]).reset_index(drop=True)
        return out[
            [
                "date",
                "symbol",
                "company_name",
                "weight",
                "sector",
                "security_type_code",
                "security_type_name",
                "source",
            ]
        ]
