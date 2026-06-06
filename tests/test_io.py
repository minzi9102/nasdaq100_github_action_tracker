import json

import numpy as np

from qqq_tracker.io import write_json


def test_write_json_replaces_nan_with_null(tmp_path):
    path = tmp_path / "manifest.json"

    write_json({"value": np.nan, "nested": [{"missing": float("nan")}]}, path)

    text = path.read_text(encoding="utf-8")
    assert "NaN" not in text
    assert json.loads(text) == {"value": None, "nested": [{"missing": None}]}
