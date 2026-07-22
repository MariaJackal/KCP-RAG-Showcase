from conftest import FakeModelClient, make_settings
from personas import get_persona
from services.cache_store import TTLCache
from services.pipeline import run_rag_pipeline


class _Result:
    def __init__(self, title="doc", snippet="relevant context"):
        data = {
            "title": title,
            "snippets": [{"snippet": snippet}],
            "extractive_answers": [],
            "extractive_segments": [],
        }

        class _Doc:
            def __init__(self, d):
                self.derived_struct_data = d
                self.struct_data = {}

        self.document = _Doc(data)


class _SearchResponse:
    def __init__(self, results=None):
        self.results = results or []


class _SearchClient:
    def __init__(self, response=None):
        self._response = response or _SearchResponse()

    def serving_config_path(self, **kwargs):
        return "projects/test/locations/global/dataStores/test-ds/servingConfigs/default_config"

    def search(self, request):
        return self._response


def test_stream_callback_exception_does_not_fail_pipeline():
    streamed = []

    def flaky_stream_callback(chunk):
        streamed.append(chunk)
        raise RuntimeError("stream callback boom")

    # FakeModelClient stream_text splits "Part A Part B" at midpoint → "Part A" + " Part B"
    result = run_rag_pipeline(
        question="What is the process?",
        persona=get_persona("traffic"),
        recent_messages=[],
        rewriter_model=FakeModelClient(["SEARCH", "rewritten query"]),
        answer_model=FakeModelClient(["Part A Part B"]),
        search_client=_SearchClient(_SearchResponse([_Result()])),
        settings=make_settings(),
        search_cache=TTLCache(ttl_seconds=300, max_entries=32),
        answer_cache=TTLCache(ttl_seconds=300, max_entries=32),
        persona_id="traffic",
        stream_callback=flaky_stream_callback,
    )

    assert len(streamed) >= 1  # at least one chunk was streamed before exception
    assert result.error is None
    assert "Part A" in result.answer
