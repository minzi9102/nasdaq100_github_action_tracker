import pandas as pd

from qqq_tracker.pipeline.capability_probe import CAPABILITY_COLUMNS, classify_probe_result, run_probe_calls
from qqq_tracker.providers.base import ProviderResult


def result(provider, ok=False, rows=0, message="", raw=None):
    return ProviderResult(provider, ok, pd.DataFrame([{"value": 1}] * rows), message, raw)


def test_probe_classifies_premium_and_permission_limits():
    assert classify_probe_result(result("alpha_vantage", message="This API function is Premium", raw={}))[0] == "premium_blocked"
    error_type, status = classify_probe_result(result("fmp", message="402 Client Error: Payment Required"))
    assert error_type == "permission_limited"
    assert status == 402


def test_probe_classifies_empty_success_as_parse_error():
    assert classify_probe_result(result("twelve_data", ok=True, rows=0, raw={}))[0] == "parse_error"


def test_probe_matrix_keeps_diagnostic_sources_out_of_production():
    class Alpha:
        def daily(self, symbol, outputsize="compact"):
            if outputsize == "full":
                return result("alpha_vantage", message="Premium endpoint", raw={})
            return result("alpha_vantage", ok=True, rows=100, raw={})

    class FMP:
        def quote(self, symbol):
            return result("fmp", ok=True, rows=1, raw={})

        def batch_quote(self, symbols, fallback_to_single=False):
            return result("fmp", message="402 Payment Required")

        def stable_batch_quote(self, symbols):
            return result("fmp", message="402 Payment Required")

    class Twelve:
        def quote(self, symbol):
            return result("twelve_data", ok=True, rows=1, raw={})

        def time_series(self, symbol, outputsize=260):
            return result("twelve_data", ok=True, rows=260, raw={})

    probes = run_probe_calls(Alpha(), FMP(), Twelve())

    assert list(probes.columns) == CAPABILITY_COLUMNS
    assert probes.loc[probes["provider"] == "fmp", "usable_for_production"].eq(False).all()
    assert probes.loc[probes["provider"] == "twelve_data", "usable_for_production"].eq(True).all()
