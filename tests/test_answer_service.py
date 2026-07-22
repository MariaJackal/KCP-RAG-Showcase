from google.api_core.exceptions import ResourceExhausted

from conftest import FakeModelClient, make_settings
from personas import get_persona
from services.answer_service import (
    build_citation_sources,
    filter_irrelevant_results,
    generate_refined_answer,
)
from services.model_client import ModelResponse, ModelUsage


class _SeqModel(FakeModelClient):
    """Alias kept for clarity; FakeModelClient already handles sequences."""


class _CaptureModel:
    """Model that captures the prompt and returns a fixed answer."""

    def __init__(self, answer_text):
        self.captured_prompt = None
        self._answer = answer_text
        self.provider = "test"
        self.model_name = "test-capture"

    def generate_text(self, prompt, **kwargs):
        self.captured_prompt = prompt
        return ModelResponse(text=self._answer, finish_reason="STOP", usage=ModelUsage())

    def stream_text(self, prompt, **kwargs):
        self.captured_prompt = prompt
        yield ModelResponse(text=self._answer, finish_reason="STOP", usage=ModelUsage())


class _RaisingModel:
    """Model that always raises the given exception."""

    def __init__(self, exc):
        self._exc = exc
        self.provider = "test"
        self.model_name = "test-error"

    def generate_text(self, prompt, **kwargs):
        raise self._exc

    def stream_text(self, prompt, **kwargs):
        raise self._exc
        yield  # make it a generator


class _Result:
    def __init__(self, title="文件", snippet="內容", answer="", segment="", struct_data=None):
        data = {
            "title": title,
            "snippets": [{"snippet": snippet}],
            "extractive_answers": [{"content": answer}] if answer else [],
            "extractive_segments": [{"content": segment}] if segment else [],
        }

        class _Doc:
            def __init__(self, d, structured):
                self.derived_struct_data = d
                self.struct_data = structured or {}

        self.document = _Doc(data, struct_data)


class _LabeledResult:
    def __init__(self, result, sub_query, sub_query_index):
        self.result = result
        self.sub_query = sub_query
        self.sub_query_index = sub_query_index


_SETTINGS = make_settings()


def test_build_citation_sources_indexes_from_one():
    sources = build_citation_sources([_Result(title="法規A"), _Result(title="法規B")])
    assert [s["index"] for s in sources] == [1, 2]
    assert [s["title"] for s in sources] == ["法規A", "法規B"]
    assert all(s["content"] for s in sources)


def test_build_citation_sources_respects_max_sources():
    results = [_Result(title=f"法規{i}") for i in range(5)]
    assert len(build_citation_sources(results, max_sources=3)) == 3


def test_build_citation_sources_unwraps_labeled_results():
    labeled = _LabeledResult(_Result(title="子面向法規"), "子查詢", 1)
    sources = build_citation_sources([labeled])
    assert sources[0]["title"] == "子面向法規"


def test_build_citation_sources_empty_results():
    assert build_citation_sources([]) == []


def test_filter_irrelevant_results_empty_input_returns_empty():
    assert filter_irrelevant_results([], "q", FakeModelClient([])) == []


def test_filter_irrelevant_results_none_decision_returns_empty():
    model = FakeModelClient(["None"])
    results = [_Result(title="A")]
    assert filter_irrelevant_results(results, "q", model) == []


def test_filter_irrelevant_results_picks_selected_ids():
    model = FakeModelClient(["0,2"])
    results = [_Result(title="A"), _Result(title="B"), _Result(title="C")]
    out = filter_irrelevant_results(results, "q", model)
    assert [r.document.derived_struct_data["title"] for r in out] == ["A", "C"]


def test_filter_irrelevant_results_uses_structured_result_titles():
    model = _CaptureModel("0")
    results = [
        _Result(
            struct_data={
                "law_name": "道路交通管理處罰條例",
                "display_name": "第 1 條",
                "embedding_text": "道路交通管理處罰條例 第 1 條 測試內容",
            }
        )
    ]
    filter_irrelevant_results(results, "q", model)
    assert "道路交通管理處罰條例 第 1 條" in model.captured_prompt


def test_filter_irrelevant_results_on_model_error_returns_original():
    model = _RaisingModel(RuntimeError("fail"))
    results = [_Result(title="A"), _Result(title="B")]
    out = filter_irrelevant_results(results, "q", model)
    assert len(out) == 2


def test_generate_refined_answer_returns_insufficient_when_no_results():
    rewriter = FakeModelClient([])
    answer_model = FakeModelClient([])
    out = generate_refined_answer("q", "q", [], rewriter, answer_model, _SETTINGS)
    assert "資料不足" in out


def test_generate_refined_answer_uses_answer_model_when_available():
    rewriter = FakeModelClient(["0"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    out = generate_refined_answer("q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS)
    assert "結論" in out


def test_generate_refined_answer_resource_exhausted_then_success():
    rewriter = FakeModelClient(["0"])
    answer_model = FakeModelClient([
        ResourceExhausted("busy"),
        "**結論:** A\n**注意事項:** C\n**法規依據:** B",
    ])
    out = generate_refined_answer("q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS)
    assert "結論" in out


def test_generate_refined_answer_fallback_to_rewriter_model_when_answer_model_fails():
    rewriter = FakeModelClient([
        "**結論:** 備援\n**注意事項:** C\n**法規依據:** B",
    ])
    answer_model = _RaisingModel(RuntimeError("boom"))
    out = generate_refined_answer("q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS)
    assert "備援" in out


def test_generate_refined_answer_temporary_when_all_models_fail():
    rewriter = _RaisingModel(RuntimeError("boom2"))
    answer_model = _RaisingModel(RuntimeError("boom"))
    out = generate_refined_answer("q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS)
    assert "參考依據" in out


def test_generate_refined_answer_with_persona_uses_role_description():
    rewriter = FakeModelClient(["0"])
    answer_model = _CaptureModel("**結論:** A\n**注意事項:** C\n**法規依據:** B")
    persona = get_persona("traffic")
    out = generate_refined_answer(
        "q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS, persona=persona
    )
    assert "結論" in out
    assert "交通執法法規助理" in answer_model.captured_prompt


def test_generate_refined_answer_with_persona_adds_focus_instructions():
    rewriter = FakeModelClient(["0"])
    answer_model = _CaptureModel("**結論:** A\n**注意事項:** C\n**法規依據:** B")
    persona = get_persona("traffic")
    generate_refined_answer(
        "q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS, persona=persona
    )
    assert "交通執法導向" in answer_model.captured_prompt


def test_generate_refined_answer_without_persona_uses_default_role():
    rewriter = FakeModelClient(["0"])
    answer_model = _CaptureModel("**結論:** A\n**注意事項:** C\n**法規依據:** B")
    generate_refined_answer(
        "q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS
    )
    assert "法規專家行動助理" in answer_model.captured_prompt


def test_generate_refined_answer_truncates_oversized_context():
    long_text = "甲" * 8000
    rewriter = FakeModelClient(["0"])
    answer_model = _CaptureModel("**結論:** A\n**注意事項:** C\n**法規依據:** B")
    generate_refined_answer(
        "q", "q",
        [_Result(title="法規A", snippet=long_text, answer=long_text, segment=long_text)],
        rewriter, answer_model, _SETTINGS,
    )
    assert "甲" in answer_model.captured_prompt
    assert "以下內容因長度限制已截斷" not in answer_model.captured_prompt


def test_generate_refined_answer_uses_structured_embedding_text_in_context():
    rewriter = FakeModelClient(["0"])
    answer_model = _CaptureModel("**結論:** A\n**注意事項:** C\n**法規依據:** B")
    generate_refined_answer(
        "q", "q",
        [
            _Result(
                struct_data={
                    "law_name": "道路交通管理處罰條例",
                    "display_name": "第 2 條",
                    "embedding_text": "道路交通管理處罰條例 第 2 條 測試條文全文",
                }
            )
        ],
        rewriter, answer_model, _SETTINGS,
    )
    assert "道路交通管理處罰條例 第 2 條" in answer_model.captured_prompt
    assert "測試條文全文" in answer_model.captured_prompt


def test_generate_refined_answer_keeps_subfacet_labels_in_context():
    rewriter = FakeModelClient(["0"])
    answer_model = _CaptureModel("**結論:** A\n**注意事項:** C\n**法規依據:** B")
    results = [
        _LabeledResult(
            _Result(title="道路交通管理處罰條例 第55條", answer="汽車駕駛人臨時停車規定。"),
            "臨時停車",
            1,
        ),
        _LabeledResult(
            _Result(title="道路交通管理處罰條例 第42條", answer="汽車駕駛人不依規定使用燈光。"),
            "不依規定使用燈光",
            2,
        ),
    ]
    generate_refined_answer(
        "臨時停車使用錯誤燈號",
        "臨時停車 第55條 不依規定使用燈光 第42條",
        results,
        rewriter,
        answer_model,
        _SETTINGS,
    )
    assert "[子面向 1：臨時停車]" in answer_model.captured_prompt
    assert "[子面向 2：不依規定使用燈光]" in answer_model.captured_prompt
    assert "道路交通管理處罰條例 第55條" in answer_model.captured_prompt
    assert "道路交通管理處罰條例 第42條" in answer_model.captured_prompt


# ---------------------------------------------------------------------------
# Followup clarification prompt tests
# ---------------------------------------------------------------------------

from services.answer_prompts import build_answer_prompt as _build_answer_prompt  # noqa: E402


def test_answer_prompt_forbids_self_generated_menu():
    """追問選單改由規則層統一附加；prompt 必須禁止 LLM 自生選單。"""
    prompt = _build_answer_prompt("闖紅燈罰多少", "無資料")
    assert "追問選單規則" in prompt
    assert "由系統的確定性規則層統一附加" in prompt
    assert "(1) <選項一>" not in prompt


def test_answer_prompt_persona_instructions_preserved():
    traffic_persona = get_persona("traffic")
    prompt = _build_answer_prompt("肇事逃逸怎麼處理", "無資料", persona=traffic_persona)
    assert "追問選單規則" in prompt
    assert traffic_persona.answer_role_description in prompt
    assert "A1/A2/A3" in prompt or "A1" in prompt


def test_answer_does_not_append_reference_line():
    """ensure_reference_line 已移除，最終答案不應自動附加「參考依據：」行。"""
    rewriter = FakeModelClient(["0"])
    answer_model = _CaptureModel("**結論:** A\n**注意事項:** C\n**法規依據:** B")
    out = generate_refined_answer("q", "q", [_Result(title="法規A")], rewriter, answer_model, _SETTINGS)
    assert "參考依據：" not in out
