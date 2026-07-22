from conftest import MockResponse
from personas import get_persona
from services.rewrite_service import rewrite_query


class _CaptureModel:
    """Model that captures the prompt and returns a fixed response."""

    def __init__(self, response_text="keyword1 keyword2"):
        self.response_text = response_text
        self.captured_prompt = None

    def generate_content(self, prompt, **kwargs):
        self.captured_prompt = prompt
        return MockResponse(self.response_text)


def test_rewrite_without_persona_has_traffic_accident_anchors():
    model = _CaptureModel()
    rewrite_query("", "酒駕", model)
    prompt = model.captured_prompt
    assert "刑事責任" in prompt
    assert "行政法規" in prompt
    assert "作業程序" in prompt
    assert "內部規範" in prompt
    assert "A1類" in prompt
    assert "A2類" in prompt
    assert "A3類" in prompt
    assert "道路交通事故當事人登記聯單" in prompt
    assert "肇事逃逸" in prompt
    assert "交通事故處理優先" not in prompt


def test_rewrite_with_traffic_persona_has_extra_dimension():
    model = _CaptureModel()
    persona = get_persona("traffic")
    rewrite_query("", "酒駕", model, persona=persona)
    prompt = model.captured_prompt
    assert "交通事故處理優先" in prompt
    assert "A3移車" in prompt
    assert "酒駕" in prompt


def test_rewrite_with_default_persona_has_traffic_extra_dimension():
    model = _CaptureModel()
    persona = get_persona("traffic")
    rewrite_query("", "問題", model, persona=persona)
    prompt = model.captured_prompt
    assert "關鍵流程需含" in prompt


def test_rewrite_prompt_contains_terminology_normalization():
    model = _CaptureModel()
    rewrite_query("", "大型重機行駛慢車道", model)
    prompt = model.captured_prompt
    assert "大型重型機車" in prompt
    assert "比照小型汽車" in prompt
    assert "第92條" in prompt


def test_rewrite_prompt_distinguishes_heavy_bike_from_motorcycle():
    model = _CaptureModel()
    rewrite_query("", "重機可以上高速公路嗎", model)
    prompt = model.captured_prompt
    # 重機 → 機車，術語表中明確區分，不應混入大型重機說明
    assert "大型重機」≠「重機" in prompt


def test_rewrite_prompt_contains_enforcement_terms():
    model = _CaptureModel()
    rewrite_query("", "開紅單流程", model)
    prompt = model.captured_prompt
    assert "舉發違反道路交通管理事件通知單" in prompt
    assert "酒精濃度檢測" in prompt
    assert "肇事逃逸" in prompt


def test_rewrite_history_truncation():
    model = _CaptureModel()
    long_history = "x" * 3000
    rewrite_query(long_history, "q", model)
    prompt = model.captured_prompt
    assert "..." in prompt
