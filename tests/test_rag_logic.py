from rag_logic import (
    normalize_intent,
    rewrite_with_retry,
    extract_result_data,
    parse_related_result_ids,
    extract_result_content,
    extract_result_title,
)


class _OkResponse:
    def __init__(self, text):
        self.text = text


class _AlwaysFailModel:
    def generate_content(self, *args, **kwargs):
        raise TimeoutError("timeout")


class _FailThenSuccessModel:
    def __init__(self):
        self.calls = 0

    def generate_content(self, *args, **kwargs):
        self.calls += 1
        if self.calls < 2:
            raise RuntimeError("transient")
        return _OkResponse("刑法 道交條例")


def test_normalize_intent_search_and_block():
    assert normalize_intent("SEARCH") == "SEARCH"
    assert normalize_intent(" block ") == "BLOCK"


def test_normalize_intent_defaults_to_search_for_unknown():
    assert normalize_intent("HELLO") == "SEARCH"
    assert normalize_intent("") == "SEARCH"


def test_rewrite_with_retry_returns_fallback_on_timeout_exception():
    model = _AlwaysFailModel()
    out = rewrite_with_retry(
        model=model,
        prompt="p",
        fallback_text="原始問題",
        retries=3,
        sleep_fn=lambda _: None,
    )
    assert out == "原始問題"


def test_rewrite_with_retry_returns_text_after_transient_error():
    model = _FailThenSuccessModel()
    out = rewrite_with_retry(
        model=model,
        prompt="p",
        fallback_text="原始問題",
        retries=3,
        sleep_fn=lambda _: None,
    )
    assert out == "刑法 道交條例"


def test_parse_related_result_ids_handles_none_and_bounds():
    assert parse_related_result_ids("None", 5) == []
    assert parse_related_result_ids("0, 2, 8", 3) == [0, 2]


def test_extract_result_content_prefers_answers_then_segments_then_snippet():
    data_answers = {
        "extractive_answers": [{"content": "A"}],
        "extractive_segments": [{"content": "B"}],
        "snippets": [{"snippet": "C"}],
    }
    data_segments = {
        "extractive_answers": [{"content": ""}],
        "extractive_segments": [{"content": "B"}],
        "snippets": [{"snippet": "C"}],
    }
    data_snippet = {
        "extractive_answers": [],
        "extractive_segments": [],
        "snippets": [{"snippet": "C"}],
    }
    assert extract_result_content(data_answers) == "A"
    assert extract_result_content(data_segments) == "B"
    assert extract_result_content(data_snippet) == "C"


def test_extract_result_content_prefers_structured_embedding_text():
    data = {
        "embedding_text": "道路交通管理處罰條例 第 1 條",
        "content": "條文原文",
        "extractive_answers": [{"content": "舊答案"}],
    }
    assert extract_result_content(data) == "道路交通管理處罰條例 第 1 條"


def test_extract_result_content_supports_nested_document_struct_data():
    data = {
        "document": {
            "struct_data": {
                "embedding_text": "結構化內容",
                "content": "備援全文",
            }
        }
    }
    assert extract_result_content(data) == "結構化內容"


def test_extract_result_data_prefers_struct_data_over_derived_struct_data():
    class _Document:
        def __init__(self):
            self.struct_data = {"content": "struct"}
            self.derived_struct_data = {"content": "derived"}

    class _Result:
        def __init__(self):
            self.document = _Document()

    assert extract_result_data(_Result()) == {"content": "struct"}


def test_extract_result_title_builds_readable_structured_title():
    data = {
        "law_name": "道路交通管理處罰條例",
        "display_name": "第 7-1 條",
    }
    assert extract_result_title(data) == "道路交通管理處罰條例 第 7-1 條"
