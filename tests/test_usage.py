# SPDX-License-Identifier: Apache-2.0
from src.usage import _cost, _cost_note


def test_cost_extracts_from_usage_dict():
    assert _cost({"cost": 0.0021}) == 0.0021


def test_cost_returns_none_for_missing_or_invalid_usage():
    assert _cost(None) is None
    assert _cost({}) is None
    assert _cost("not-a-dict") is None


def test_cost_note_formats_known_cost():
    assert _cost_note({"cost": 0.001234}) == "\U0001f4b2 Request cost: $0.001234 USD"


def test_cost_note_reports_unknown():
    assert _cost_note(None) == "\U0001f4b2 Request cost: unknown"
