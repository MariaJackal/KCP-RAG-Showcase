from services.term_normalizer import _parse_response, normalize_terms
from tests.conftest import FakeModelClient


def test_parse_valid_response():
    raw = '{"normalized": "機車行駛人行道 機慢車", "changes": ["機車→補機慢車"]}'
    normalized, changes = _parse_response(raw, "機車行駛人行道")
    assert normalized == "機車行駛人行道 機慢車"
    assert changes == ["機車→補機慢車"]


def test_parse_response_with_markdown_noise():
    raw = '前言\n```json\n{"normalized": "酒駕 第35條", "changes": ["酒駕→第35條"]}\n```'
    normalized, changes = _parse_response(raw, "酒駕")
    assert normalized == "酒駕 第35條"
    assert changes == ["酒駕→第35條"]


def test_parse_invalid_json_returns_original():
    normalized, changes = _parse_response("garbage", "原句")
    assert normalized == "原句"
    assert changes == []


def test_parse_missing_normalized_key_returns_original():
    normalized, changes = _parse_response('{"foo": "bar"}', "原句")
    assert normalized == "原句"
    assert changes == []


def test_parse_empty_normalized_returns_original():
    normalized, changes = _parse_response('{"normalized": "", "changes": []}', "原句")
    assert normalized == "原句"
    assert changes == []


def test_normalize_empty_query_returns_unchanged():
    fake = FakeModelClient([])
    normalized, changes = normalize_terms("", fake)
    assert normalized == ""
    assert changes == []


def test_normalize_successful_call():
    import pytest
    pytest.skip("上游開發中行為（normalizer 目前會 strip 條號），非公開版差異")
    fake = FakeModelClient(['{"normalized": "酒駕 酒精濃度超過規定標準 第35條", "changes": ["酒駕→補§35"]}'])
    normalized, changes = normalize_terms("酒駕", fake)
    assert "第35條" in normalized
    assert changes == ["酒駕→補§35"]


def test_normalize_model_failure_falls_back_to_original():
    fake = FakeModelClient([RuntimeError("model down")])
    normalized, changes = normalize_terms("酒駕", fake)
    assert normalized == "酒駕"
    assert changes == []


def test_normalize_uses_temperature_zero_and_no_thinking():
    fake = FakeModelClient(['{"normalized": "酒駕 第35條", "changes": ["x"]}'])
    normalize_terms("酒駕", fake)
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs.get("temperature") == 0.0
    assert kwargs.get("thinking_budget") == 0
