from services.decomposer_service import (
    MAX_SUB_QUERIES,
    _parse_sub_queries,
    decompose_query,
    is_obviously_single_facet,
)
from tests.conftest import FakeModelClient


def test_parse_sub_queries_single():
    assert _parse_sub_queries('["機車行駛人行道"]', "機車行駛人行道") == [
        "機車行駛人行道"
    ]


def test_parse_sub_queries_multi():
    out = _parse_sub_queries('["臨時停車", "使用錯誤燈號"]', "臨時停車使用錯誤燈號")
    assert out == ["臨時停車", "使用錯誤燈號"]


def test_parse_sub_queries_with_markdown_noise():
    raw = '前綴文字\n```json\n["酒駕", "肇事逃逸"]\n```\n後綴'
    assert _parse_sub_queries(raw, "酒駕肇事逃逸") == ["酒駕", "肇事逃逸"]


def test_parse_sub_queries_invalid_json_returns_original():
    assert _parse_sub_queries("not a json array", "原句") == ["原句"]


def test_parse_sub_queries_empty_array_returns_original():
    assert _parse_sub_queries("[]", "原句") == ["原句"]


def test_parse_sub_queries_caps_at_max():
    raw = '["a", "b", "c", "d", "e"]'
    out = _parse_sub_queries(raw, "原句")
    assert len(out) == MAX_SUB_QUERIES
    assert out == ["a", "b", "c"]


def test_parse_sub_queries_strips_whitespace_and_empties():
    raw = '["  酒駕  ", "", "肇逃"]'
    assert _parse_sub_queries(raw, "原句") == ["酒駕", "肇逃"]


def test_decompose_query_empty_returns_original():
    fake = FakeModelClient([])
    assert decompose_query("", fake) == [""]
    assert decompose_query("   ", fake) == ["   "]


def test_decompose_query_single_facet():
    fake = FakeModelClient(['["機車行駛人行道"]'])
    assert decompose_query("機車行駛人行道", fake) == ["機車行駛人行道"]


def test_decompose_query_compound():
    fake = FakeModelClient(['["臨時停車", "使用錯誤燈號"]'])
    result = decompose_query("臨時停車使用錯誤燈號", fake)
    assert result == ["臨時停車", "使用錯誤燈號"]


def test_decompose_query_model_failure_falls_back_to_original():
    fake = FakeModelClient([RuntimeError("model down")])
    assert decompose_query("機車行駛人行道", fake) == ["機車行駛人行道"]


def test_decompose_query_uses_temperature_zero_and_no_thinking():
    fake = FakeModelClient(['["機車行駛人行道"]'])
    decompose_query("機車行駛人行道", fake)
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs.get("temperature") == 0.0
    assert kwargs.get("thinking_budget") == 0


# --- Method A: Regex prefilter for obvious single-facet queries ---


def test_prefilter_empty_returns_true():
    assert is_obviously_single_facet("") is True
    assert is_obviously_single_facet("   ") is True


def test_prefilter_short_single_term_returns_true():
    """≤ 6 chars with at most 1 action verb → obvious single facet."""
    assert is_obviously_single_facet("酒駕") is True
    assert is_obviously_single_facet("闖紅燈") is True
    assert is_obviously_single_facet("肇逃") is True
    assert is_obviously_single_facet("無人機") is True


def test_prefilter_definition_question_returns_true():
    assert is_obviously_single_facet("肇事逃逸的定義") is True
    assert is_obviously_single_facet("什麼是重大交通事故") is True
    assert is_obviously_single_facet("交通事故是什麼") is True


def test_prefilter_compound_with_two_action_verbs_returns_false():
    """這是方案 H 必須處理的關鍵案例，prefilter 絕不能誤殺。"""
    assert is_obviously_single_facet("臨時停車使用錯誤燈號") is False
    assert is_obviously_single_facet("酒駕肇事逃逸") is False
    assert is_obviously_single_facet("闖紅燈超速併排停車") is False


def test_prefilter_connectives_force_decomposition():
    assert is_obviously_single_facet("酒駕和肇逃") is False
    assert is_obviously_single_facet("闖紅燈以及超速") is False
    assert is_obviously_single_facet("超速、未戴安全帽") is False


def test_prefilter_extended_short_queries_are_caught():
    """Phase A+B: 12 字以內 + 無並列詞 + ≤1 action verb → 單面向，prefilter 攔下。"""
    assert is_obviously_single_facet("機車行駛人行道") is True  # 7 字, action={行駛}
    assert is_obviously_single_facet("大型重機可以走快速道路") is True  # 11 字, 0 action


def test_prefilter_long_complex_question_falls_through_to_llm():
    """超過 12 字 + 無強烈複合訊號 → 不確定 → 走 LLM (回傳 False)。"""
    assert is_obviously_single_facet("大型重型機車可以走快速道路嗎還有其他規定") is False
