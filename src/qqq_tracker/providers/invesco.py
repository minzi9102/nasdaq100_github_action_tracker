from __future__ import annotations

from io import BytesIO, StringIO
from typing import Iterable

import pandas as pd
import requests

from .base import ProviderResult, sanitize_error_message


class InvescoProvider:
    provider_name = "invesco"

    def __init__(self, holdings_urls: Iterable[str], timeout: int = 30, retry_count: int = 2) -> None:
        self.holdings_urls = [url for url in holdings_urls if url]
        self.timeout = timeout
        self.retry_count = retry_count

    @property
    def available(self) -> bool:
        return bool(self.holdings_urls)

    def qqq_holdings(self, ticker: str = "QQQ") -> ProviderResult:
        if not self.available:
            return ProviderResult(self.provider_name, False, pd.DataFrame(), "qqq_holdings: no holdings URL configured")

        errors: list[str] = []
        for url in self.holdings_urls:
            try:
                content, content_type = self._download(url)
                df = self._parse(content, content_type)
                normalized = self._normalize(df)
                if normalized.empty:
                    errors.append(f"{url}: no holding rows parsed")
                    continue
                normalized["source"] = self.provider_name
                return ProviderResult(
                    self.provider_name,
                    True,
                    normalized,
                    f"{ticker}: holdings rows={len(normalized)}",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(sanitize_error_message(f"{url} - {exc}"))

        return ProviderResult(self.provider_name, False, pd.DataFrame(), "; ".join(errors))

    def _download(self, url: str) -> tuple[bytes, str]:
        last_error: Exception | None = None
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; qqq-tracker/1.0)",
            "Accept": "text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
        }
        for attempt in range(self.retry_count + 1):
            try:
                r = requests.get(url, headers=headers, timeout=self.timeout)
                r.raise_for_status()
                return r.content, r.headers.get("content-type", "")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RuntimeError(str(last_error))

    def _parse(self, content: bytes, content_type: str) -> pd.DataFrame:
        lower_type = content_type.lower()
        if b"<html" in content[:500].lower():
            raise ValueError("download returned HTML, not a holdings file")
        if "csv" in lower_type or content[:200].count(b",") >= 2:
            text = content.decode("utf-8-sig", errors="replace")
            return pd.read_csv(StringIO(text))
        return pd.read_excel(BytesIO(content), header=None)

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=["date", "symbol", "company_name", "weight", "sector", "source"])

        table = self._with_detected_header(df)
        lowered = {str(c).strip().lower(): c for c in table.columns}

        symbol_col = self._first_present(lowered, ["ticker", "symbol"])
        company_col = self._first_present(lowered, ["company", "company name", "name", "security"])
        weight_col = self._first_present(lowered, ["% of fund", "weight", "market value weight"])
        sector_col = self._first_present(lowered, ["sector", "sector total"])
        date_col = self._first_present(lowered, ["date", "as of date", "as-of date"])

        if symbol_col is None or weight_col is None:
            return pd.DataFrame(columns=["date", "symbol", "company_name", "weight", "sector", "source"])

        out = pd.DataFrame()
        out["symbol"] = table[symbol_col].astype(str).str.strip()
        out["company_name"] = table[company_col].astype(str).str.strip() if company_col is not None else ""
        out["weight"] = table[weight_col].map(self._to_weight)
        out["sector"] = table[sector_col].astype(str).str.strip() if sector_col is not None else ""
        out["date"] = table[date_col].astype(str).str.strip() if date_col is not None else pd.Timestamp.today().date().isoformat()
        out = out[["date", "symbol", "company_name", "weight", "sector"]]
        out = out[out["symbol"].str.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", na=False)]
        out = out.dropna(subset=["weight"]).copy()
        return out.sort_values(["weight", "symbol"], ascending=[False, True]).reset_index(drop=True)

    def _with_detected_header(self, df: pd.DataFrame) -> pd.DataFrame:
        if any(str(c).strip().lower() in {"ticker", "symbol"} for c in df.columns):
            return df.copy()
        for idx, row in df.iterrows():
            values = [str(x).strip().lower() for x in row.tolist()]
            if "ticker" in values or "symbol" in values:
                table = df.iloc[idx + 1 :].copy()
                table.columns = [str(x).strip() for x in row.tolist()]
                return table.dropna(how="all")
        return df.copy()

    def _first_present(self, lowered: dict[str, object], names: list[str]) -> object | None:
        for name in names:
            if name in lowered:
                return lowered[name]
        return None

    def _to_weight(self, value: object) -> float | None:
        if pd.isna(value):
            return None
        text = str(value).strip().replace("%", "").replace(",", "")
        try:
            number = float(text)
        except ValueError:
            return None
        return number / 100 if number > 1 else number
