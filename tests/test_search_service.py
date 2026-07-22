import pytest

from conftest import make_settings
from services.search_service import search_vertex


class _SearchClient:
    """Mock that captures the SearchRequest for assertion."""

    def __init__(self, response=None):
        self.calls = []
        self._response = response or []

    def search(self, request):
        self.calls.append(request)
        return self._response


def test_search_vertex_uses_injected_client():
    client = _SearchClient()
    search_vertex("酒駕", make_settings(), search_client=client)
    assert len(client.calls) == 1


def test_search_vertex_passes_query_text():
    client = _SearchClient()
    search_vertex("盤查程序", make_settings(), search_client=client)
    assert client.calls[0].query == "盤查程序"


def test_search_vertex_page_size_is_15():
    client = _SearchClient()
    search_vertex("q", make_settings(), search_client=client)
    assert client.calls[0].page_size == 15


def test_search_vertex_page_size_is_50_when_rerank_enabled():
    client = _SearchClient()
    search_vertex("q", make_settings(rerank_enabled=True), search_client=client)
    assert client.calls[0].page_size == 50


def test_search_vertex_serving_config_contains_project_and_engine():
    client = _SearchClient()
    search_vertex("q", make_settings(engine_id="test-engine"), search_client=client)
    serving_config = client.calls[0].serving_config
    assert "test-project" in serving_config
    assert "engines/test-engine" in serving_config
    assert "default_search" in serving_config


def test_search_vertex_does_not_send_content_search_spec_for_structured_store():
    client = _SearchClient()
    search_vertex("q", make_settings(engine_id="test-engine"), search_client=client)
    assert not client.calls[0]._pb.HasField("content_search_spec")


def test_search_vertex_returns_client_response_directly():
    """search_vertex returns the response object from client.search unchanged."""
    sentinel = object()
    client = _SearchClient(response=sentinel)
    result = search_vertex("q", make_settings(), search_client=client)
    assert result is sentinel


def test_search_vertex_empty_query_returns_empty_results():
    """Empty query returns empty results and skips client.search."""
    client = _SearchClient()
    result = search_vertex("", make_settings(), search_client=client)
    assert len(client.calls) == 0
    assert result.results == []


def test_search_vertex_whitespace_query_returns_empty_results():
    """Whitespace-only query behaves as empty query."""
    client = _SearchClient()
    result = search_vertex("   ", make_settings(), search_client=client)
    assert len(client.calls) == 0
    assert result.results == []


def test_search_vertex_sets_query_expansion_spec_auto():
    """Hybrid 探針：query_expansion_spec 應該以 AUTO 條件送出。"""
    from google.cloud import discoveryengine_v1 as discoveryengine

    client = _SearchClient()
    search_vertex("機車行駛人行道", make_settings(), search_client=client)
    spec = client.calls[0].query_expansion_spec
    assert spec.condition == discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO


def test_search_vertex_sets_spell_correction_spec_auto():
    """Hybrid 探針：spell_correction_spec 應該以 AUTO 模式送出。"""
    from google.cloud import discoveryengine_v1 as discoveryengine

    client = _SearchClient()
    search_vertex("機車行駛人行道", make_settings(), search_client=client)
    spec = client.calls[0].spell_correction_spec
    assert spec.mode == discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO


def test_search_vertex_client_exception_propagates():
    """Exceptions from client.search should propagate to caller."""

    class _FailClient:
        def search(self, request):
            raise ConnectionError("search unavailable")

    with pytest.raises(ConnectionError, match="search unavailable"):
        search_vertex("酒駕罰則", make_settings(), search_client=_FailClient())
