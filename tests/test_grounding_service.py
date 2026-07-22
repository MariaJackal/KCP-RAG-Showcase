"""services/grounding_service.py 單元測試（mock client，離線）。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from services.grounding_service import GROUNDING_WARNING_BLOCK, check_grounding


def _settings(threshold=0.6):
    return SimpleNamespace(project_id="test-project", grounding_threshold=threshold)


def _sources():
    return [
        {"index": 1, "title": "處罰條例 第35條", "content": "汽車駕駛人酒精濃度超過規定標準…"},
        {"index": 2, "title": "刑法 第185-3條", "content": "吐氣所含酒精濃度達每公升0.25毫克…"},
    ]


def _mock_client(score=0.87, claims=None):
    client = MagicMock()
    client.check_grounding.return_value = SimpleNamespace(
        support_score=score, claims=claims or []
    )
    return client


def test_returns_score_and_request_shape():
    client = _mock_client(score=0.87)
    score = check_grounding("答案內容", _sources(), _settings(), grounding_client=client)
    assert score == 0.87

    request = client.check_grounding.call_args.kwargs["request"]
    assert request["grounding_config"] == (
        "projects/test-project/locations/global/groundingConfigs/default_grounding_config"
    )
    assert request["answer_candidate"] == "答案內容"
    assert len(request["facts"]) == 2
    assert request["facts"][0]["fact_text"].startswith("汽車駕駛人")
    assert request["facts"][0]["attributes"] == {"title": "處罰條例 第35條"}
    assert request["grounding_spec"] == {"citation_threshold": 0.6}


def test_threshold_from_settings():
    client = _mock_client()
    check_grounding("答案", _sources(), _settings(threshold=0.75), grounding_client=client)
    request = client.check_grounding.call_args.kwargs["request"]
    assert request["grounding_spec"] == {"citation_threshold": 0.75}


def test_empty_answer_skips_call():
    client = _mock_client()
    assert check_grounding("", _sources(), _settings(), grounding_client=client) is None
    assert check_grounding("   ", _sources(), _settings(), grounding_client=client) is None
    client.check_grounding.assert_not_called()


def test_empty_sources_skips_call():
    client = _mock_client()
    assert check_grounding("答案", [], _settings(), grounding_client=client) is None
    client.check_grounding.assert_not_called()


def test_sources_with_empty_content_filtered():
    client = _mock_client()
    sources = _sources() + [{"index": 3, "title": "空的", "content": "  "}]
    check_grounding("答案", sources, _settings(), grounding_client=client)
    request = client.check_grounding.call_args.kwargs["request"]
    assert len(request["facts"]) == 2  # 空 content 不進 facts


def test_all_sources_empty_returns_none_without_call():
    client = _mock_client()
    sources = [{"index": 1, "title": "T", "content": ""}]
    assert check_grounding("答案", sources, _settings(), grounding_client=client) is None
    client.check_grounding.assert_not_called()


def test_fact_content_truncated_to_2000():
    client = _mock_client()
    sources = [{"index": 1, "title": "T", "content": "字" * 3000}]
    check_grounding("答案", sources, _settings(), grounding_client=client)
    request = client.check_grounding.call_args.kwargs["request"]
    assert len(request["facts"][0]["fact_text"]) == 2000


def test_api_failure_returns_none():
    client = MagicMock()
    client.check_grounding.side_effect = RuntimeError("boom")
    assert check_grounding("答案", _sources(), _settings(), grounding_client=client) is None


def test_warning_block_mentions_verification():
    """警示文案必須指向「核對原始法規」（寧可少答不要答錯的呈現）。"""
    assert "核對原始法規" in GROUNDING_WARNING_BLOCK
