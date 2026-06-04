from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import requests


@dataclass
class ProviderResult:
    name: str
    ok: bool
    data: pd.DataFrame
    message: str = ""
    raw: Any = None


class APIError(RuntimeError):
    pass


SECRET_QUERY_KEYS = {"apikey", "api_key", "token", "access_token"}


def sanitize_error_message(message: str, secrets: list[str | None] | None = None) -> str:
    sanitized = message
    for part in message.split():
        if "://" not in part or "?" not in part:
            continue
        trailing = ""
        candidate = part
        while candidate and candidate[-1] in ".,);]":
            trailing = candidate[-1] + trailing
            candidate = candidate[:-1]
        try:
            split = urlsplit(candidate)
        except ValueError:
            continue
        if not split.query:
            continue
        query = [
            (key, "***REDACTED***" if key.lower() in SECRET_QUERY_KEYS else value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
        ]
        redacted = urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))
        sanitized = sanitized.replace(part, redacted + trailing)

    for secret in secrets or []:
        if secret:
            sanitized = sanitized.replace(secret, "***REDACTED***")
    return sanitized


class BaseProvider:
    provider_name = "base"

    def __init__(self, api_key: Optional[str], base_url: str, timeout: int = 30, retry_count: int = 2) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_count = retry_count

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def request_json(self, url: str, params: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                r = requests.get(url, params=params, headers=headers, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.retry_count:
                    time.sleep(1.5 * (attempt + 1))
        raise APIError(sanitize_error_message(str(last_error), [self.api_key]))

    def unavailable_result(self, method: str) -> ProviderResult:
        return ProviderResult(self.provider_name, False, pd.DataFrame(), f"{method}: missing API key")
