from qqq_tracker.settings import Settings
from qqq_tracker.providers import AlphaVantageProvider, FREDProvider, FMPProvider, InvescoProvider, TiingoProvider, TwelveDataProvider
from qqq_tracker.providers.base import sanitize_error_message


def test_settings_loads():
    settings = Settings()
    assert "providers" in settings.sources
    assert "fmp" in settings.api_limits


def test_providers_construct_without_key():
    assert not AlphaVantageProvider(None, "https://example.com").available
    assert not FREDProvider(None, "https://example.com").available
    assert not FMPProvider(None, "https://example.com").available
    assert InvescoProvider().available
    assert not TiingoProvider(None, "https://example.com").available
    assert not TwelveDataProvider(None, "https://example.com").available


def test_sanitize_error_message_redacts_api_keys():
    message = (
        "402 Client Error for url: "
        "https://example.com/quote?symbol=AVGO&apikey=secret123&api_key=fred456"
    )
    sanitized = sanitize_error_message(message, ["secret123"])
    assert "secret123" not in sanitized
    assert "fred456" not in sanitized
    assert "apikey=%2A%2A%2AREDACTED%2A%2A%2A" in sanitized
    assert "api_key=%2A%2A%2AREDACTED%2A%2A%2A" in sanitized
