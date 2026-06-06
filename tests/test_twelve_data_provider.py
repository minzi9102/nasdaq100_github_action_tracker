import pandas as pd

from qqq_tracker.providers.twelve_data import TwelveDataProvider


def test_twelve_data_time_series_normalizes_daily_rows(monkeypatch):
    provider = TwelveDataProvider("demo-key", "https://example.com")

    def fake_request_json(url, params=None, headers=None):  # noqa: ANN001, ARG001
        return {
            "meta": {"symbol": "AAPL"},
            "values": [
                {"datetime": "2026-06-05", "open": "10", "high": "12", "low": "9", "close": "11", "volume": "1000"},
            ],
        }

    monkeypatch.setattr(provider, "request_json", fake_request_json)

    result = provider.time_series("AAPL", outputsize=260)

    assert result.ok is True
    assert result.data.columns.tolist() == ["date", "open", "high", "low", "close", "volume", "symbol", "source"]
    assert result.data.loc[0, "date"] == "2026-06-05"
    assert result.data.loc[0, "close"] == 11
    assert result.data.loc[0, "source"] == "twelve_data"


def test_twelve_data_status_error_marks_rate_limit(monkeypatch):
    provider = TwelveDataProvider("demo-key", "https://example.com")

    def fake_request_json(url, params=None, headers=None):  # noqa: ANN001, ARG001
        return {"status": "error", "code": 429, "message": "API credits limit reached"}

    monkeypatch.setattr(provider, "request_json", fake_request_json)

    result = provider.time_series("AAPL")

    assert result.ok is False
    assert result.data.empty
    assert result.raw["rate_limited"] is True


def test_twelve_data_quote_normalizes_for_top_holdings(monkeypatch):
    provider = TwelveDataProvider("demo-key", "https://example.com")

    def fake_request_json(url, params=None, headers=None):  # noqa: ANN001, ARG001
        return {
            "symbol": "MSFT",
            "close": "500.25",
            "change": "1.25",
            "percent_change": "0.25",
            "datetime": "2026-06-05",
            "timestamp": "1780000000",
        }

    monkeypatch.setattr(provider, "request_json", fake_request_json)

    result = provider.quote("MSFT")

    assert result.ok is True
    assert result.data.loc[0, "symbol"] == "MSFT"
    assert result.data.loc[0, "price"] == 500.25
    assert result.data.loc[0, "changesPercentage"] == 0.25
    assert result.data.loc[0, "source"] == "twelve_data"


def test_twelve_data_quote_status_error_marks_rate_limit(monkeypatch):
    provider = TwelveDataProvider("demo-key", "https://example.com")

    def fake_request_json(url, params=None, headers=None):  # noqa: ANN001, ARG001
        return {"status": "error", "code": 429, "message": "API credits limit reached"}

    monkeypatch.setattr(provider, "request_json", fake_request_json)

    result = provider.quote("AAPL")

    assert result.ok is False
    assert result.data.empty
    assert result.raw["rate_limited"] is True
