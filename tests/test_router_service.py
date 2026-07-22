from conftest import FakeModelClient
from services.router_service import semantic_router


def test_router_blocks_common_greeting_zh_without_model_call():
    model = FakeModelClient(["SEARCH"])
    assert semantic_router("你好", model) == "BLOCK"
    assert model.calls == []  # local pattern matched, no model call


def test_router_blocks_non_domain_prompt_without_model_call():
    model = FakeModelClient(["SEARCH"])
    assert semantic_router("幫我寫程式", model) == "BLOCK"
    assert model.calls == []


def test_router_returns_search_from_model_decision():
    model = FakeModelClient(["SEARCH"])
    assert semantic_router("酒駕罰則？", model) == "SEARCH"


def test_router_unknown_model_output_defaults_to_search():
    model = FakeModelClient(["MAYBE"])
    assert semantic_router("酒駕罰則？", model) == "SEARCH"


def test_router_model_exception_falls_back_to_block():
    model = FakeModelClient([RuntimeError("boom")])
    assert semantic_router("酒駕罰則？", model) == "BLOCK"
