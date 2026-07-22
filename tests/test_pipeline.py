import time

from conftest import FakeModelClient, make_settings
from models import Message
from personas import get_persona
from services.cache_store import TTLCache
from services.model_client import ModelResponse, ModelUsage
from services.pipeline import PipelineResult, run_rag_pipeline


class _Result:
    def __init__(self, title="文件", snippet="內容", answer="", segment=""):
        data = {
            "title": title,
            "snippets": [{"snippet": snippet}],
            "extractive_answers": [{"content": answer}] if answer else [],
            "extractive_segments": [{"content": segment}] if segment else [],
        }

        class _Doc:
            def __init__(self, d):
                self.derived_struct_data = d
                self.struct_data = {}

        self.document = _Doc(data)


class _SearchResponse:
    """Mock search response with results attribute."""

    def __init__(self, results=None):
        self.results = results or []


class _SearchClient:
    def __init__(self, response=None):
        self.calls = []
        self._response = response or _SearchResponse()

    def serving_config_path(self, **kwargs):
        return f"projects/{kwargs['project']}/locations/{kwargs['location']}/dataStores/{kwargs['data_store']}/servingConfigs/{kwargs['serving_config']}"

    def search(self, request):
        self.calls.append(request)
        return self._response


class _StableRouterRewriter:
    """Prompt-aware 假件：router 與 rewrite 並行共用同一 client。

    FakeModelClient 依呼叫順序出貨（_pop），執行緒排程偶爾讓 rewrite 先搶走
    "SEARCH"，導致 rewritten_query 錯位、快取 miss、answer model 被誤呼叫
    （flaky test_pipeline_answer_cache_hit 的根因）。依 prompt 內容回應即可
    消除順序依賴；加鎖只能防資料損毀、防不了搶答順序。
    """

    provider = "test"
    model_name = "test-model"

    def __init__(self, rewrite_text, intent_text="SEARCH", filter_text="0"):
        self.rewrite_text = rewrite_text
        self.intent_text = intent_text
        self.filter_text = filter_text
        self.calls = []

    def generate_text(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        if "意圖分類器" in prompt:
            text = self.intent_text
        elif "搜尋關鍵字" in prompt:
            text = self.rewrite_text
        else:
            text = self.filter_text
        return ModelResponse(
            text=text,
            finish_reason="STOP",
            usage=ModelUsage(prompt=1, output=1, total=2),
        )


def _base_kwargs(**overrides):
    """Build common kwargs for run_rag_pipeline calls."""
    defaults = dict(
        rewriter_model=FakeModelClient(["SEARCH", "rewritten keywords"]),
        answer_model=FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"]),
        search_client=_SearchClient(_SearchResponse([_Result()])),
        settings=make_settings(),
        search_cache=TTLCache(ttl_seconds=300, max_entries=64),
        answer_cache=TTLCache(ttl_seconds=300, max_entries=64),
        persona_id="traffic",
    )
    defaults.update(overrides)
    return defaults


# --- Test 1: BLOCK intent ---


def test_pipeline_block_intent():
    """Router returns BLOCK -> canned rejection message."""
    router_model = FakeModelClient(["BLOCK"])
    result = run_rag_pipeline(
        question="你好",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(rewriter_model=router_model),
    )
    assert result.intent == "BLOCK"
    assert "僅提供法規查詢" in result.answer
    assert "router" in result.stage_latency_ms
    assert "rewrite" not in result.stage_latency_ms
    assert result.error is None
    assert result.sources == ()  # BLOCK 路徑無檢索來源


# --- Test 2: Full SEARCH happy path ---


def test_pipeline_search_happy_path():
    """Full SEARCH flow produces an answer with all stage latencies."""
    # FakeModelClient: 1st call=router(SEARCH), 2nd=rewrite, 3rd=filter(answer_service), 4th=answer
    rewriter = FakeModelClient(["SEARCH", "rewritten query", "0"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result(title="法規A")]))

    result = run_rag_pipeline(
        question="酒駕罰則？",
        persona=get_persona("traffic"),
        recent_messages=[Message(role="user", content="之前的問題")],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
    )
    assert result.intent == "SEARCH"
    assert "結論" in result.answer
    for key in ("router", "rewrite", "search", "answer"):
        assert key in result.stage_latency_ms
    assert result.error is None
    # 引用來源：與 answer prompt 的 [n] 編號一致，從 1 起算
    assert len(result.sources) == 1
    assert result.sources[0]["index"] == 1
    assert result.sources[0]["title"] == "法規A"
    assert result.sources[0]["content"]


# --- Test 3: Search cache hit ---


def test_pipeline_search_cache_hit():
    """When search cache has data, search_client is NOT called.

    Use a question that does NOT hit the synonym dict so rewritten_query stays
    equal to "cached_query" (otherwise Method C appends law terms and the
    cache key shifts).
    """
    rewriter = _StableRouterRewriter("cached_query")
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result()]))
    search_cache = TTLCache(ttl_seconds=300, max_entries=64)
    search_cache.set("cached_query", _SearchResponse([_Result(title="快取結果")]))

    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
            search_cache=search_cache,
        ),
    )
    assert result.stage_latency_ms["search"] == 0.0
    assert len(search_client.calls) == 0


# --- Test 4: Answer cache hit ---


def test_pipeline_answer_cache_hit():
    """When answer cache has data, answer_model is NOT called.

    Use a question that does NOT hit the synonym dict so rewritten_query stays
    equal to "rq" (otherwise Method C appends law terms and the cache key shifts).
    """
    rewriter = _StableRouterRewriter("rq")
    answer_model = FakeModelClient([RuntimeError("should not be called")])
    search_cache = TTLCache(ttl_seconds=300, max_entries=64)
    search_cache.set("rq", _SearchResponse([_Result()]))
    answer_cache = TTLCache(ttl_seconds=300, max_entries=64)
    answer_cache.set(("無關問題", "rq", "traffic", "", False), "快取答案")

    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_cache=search_cache,
            answer_cache=answer_cache,
        ),
    )
    assert result.answer == "快取答案"
    assert result.stage_latency_ms["answer"] == 0.0
    assert answer_model.calls == []


# --- Test 5: Answer cache key includes persona ---


def test_pipeline_answer_cache_key_includes_persona():
    """Different persona_ids produce different cache keys (no cross-contamination)."""
    answer_cache = TTLCache(ttl_seconds=300, max_entries=64)
    answer_cache.set(("q", "rq", "traffic", "", False), "交通回答")
    answer_cache.set(("q", "rq", "future_role", "", False), "其他回答")

    rewriter1 = _StableRouterRewriter("rq")
    search_cache1 = TTLCache(ttl_seconds=300, max_entries=64)
    search_cache1.set("rq", _SearchResponse([_Result()]))
    r1 = run_rag_pipeline(
        question="q",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter1,
            search_cache=search_cache1,
            answer_cache=answer_cache,
            persona_id="traffic",
        ),
    )

    rewriter2 = _StableRouterRewriter("rq")
    search_cache2 = TTLCache(ttl_seconds=300, max_entries=64)
    search_cache2.set("rq", _SearchResponse([_Result()]))
    r2 = run_rag_pipeline(
        question="q",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter2,
            search_cache=search_cache2,
            answer_cache=answer_cache,
            persona_id="future_role",
        ),
    )

    assert r1.answer == "交通回答"
    assert r2.answer == "其他回答"


def test_digit_followup_does_not_use_fast_cache():
    """Digit replies must never read or write the fast cache.

    Without the guard, ("1", persona) is written by conversation A and read
    by conversation B, returning the wrong answer.
    """
    shared_cache = TTLCache(ttl_seconds=300, max_entries=64)

    # Pre-seed fast cache as if conversation A already answered digit "1"
    from personas import get_persona as _gp
    persona = _gp("traffic")
    shared_cache.set(("1", persona.id), "conversation A的答案")

    # Conversation B sends digit "1" — must NOT get conversation A's answer
    result = run_rag_pipeline(
        question="1",
        persona=persona,
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=FakeModelClient(["SEARCH", "rq"]),
            answer_cache=shared_cache,
        ),
    )
    assert result.answer != "conversation A的答案", (
        "digit reply '1' hit fast cache and returned wrong conversation's answer"
    )

    # After the call, fast cache must still NOT contain ("1", persona)
    assert shared_cache.get(("1", persona.id)) == "conversation A的答案", (
        "digit reply should not overwrite the pre-existing fast cache entry"
    )
    # Verify the pre-seeded value is still the original (not overwritten by this call)
    # and no new entry for ("1", persona) was written with the new answer
    assert shared_cache.get(("1", persona.id)) != result.answer or result.answer == "conversation A的答案"


# --- Test 6: Exception returns error ---


def test_pipeline_exception_returns_error():
    """Pipeline exception sets result.error and returns fallback answer."""

    class _FailingSearchClient:
        def serving_config_path(self, **kwargs):
            return "projects/p/locations/l/dataStores/d/servingConfigs/default_config"

        def search(self, request):
            raise ConnectionError("search service unavailable")

    rewriter = FakeModelClient(["SEARCH", "rq"])
    answer_model = FakeModelClient([])

    result = run_rag_pipeline(
        question="酒駕罰則？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=_FailingSearchClient(),
        ),
    )
    assert result.error is not None
    assert "發生錯誤" in result.answer


# --- Test 7: Progress callback receives stage messages ---


def test_pipeline_progress_callback_called():
    """Progress callback receives Traditional Chinese stage messages in order."""
    messages = []
    rewriter = FakeModelClient(["SEARCH", "rq"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result()]))

    run_rag_pipeline(
        question="酒駕罰則？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
        progress_callback=messages.append,
    )
    stages = {m["stage"] for m in messages if isinstance(m, dict)}
    assert "router" in stages
    assert "rewrite" in stages
    assert "search" in stages
    assert "answer" in stages


# --- Test 8: No callback does not crash ---


def test_pipeline_progress_callback_none():
    """Pipeline runs normally with progress_callback=None."""
    rewriter = FakeModelClient(["SEARCH", "rq"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result()]))

    result = run_rag_pipeline(
        question="酒駕罰則？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
        progress_callback=None,
    )
    assert result.answer is not None


# --- Test 9: Callback exception is swallowed ---


def test_pipeline_progress_callback_exception_swallowed():
    """If progress_callback raises, the pipeline still completes."""

    def bad_callback(msg):
        raise ValueError("callback error")

    rewriter = FakeModelClient(["SEARCH", "rq"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result()]))

    result = run_rag_pipeline(
        question="酒駕罰則？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
        progress_callback=bad_callback,
    )
    assert result.error is None
    assert result.answer is not None


# --- Test 10: Auto-generates request_id ---


def test_pipeline_generates_request_id():
    """When request_id is not provided, pipeline generates a 12-char hex ID."""
    result = run_rag_pipeline(
        question="你好",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(rewriter_model=FakeModelClient(["BLOCK"])),
    )
    assert len(result.request_id) == 12
    int(result.request_id, 16)  # must be valid hex


# --- Test 11: Uses provided request_id ---


def test_pipeline_uses_provided_request_id():
    """Externally-supplied request_id is used as-is."""
    result = run_rag_pipeline(
        question="你好",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(rewriter_model=FakeModelClient(["BLOCK"])),
        request_id="custom123abc",
    )
    assert result.request_id == "custom123abc"


# --- Test 12: Unstable answer not cached ---


def test_pipeline_unstable_answer_not_cached():
    """Answer containing unstable marker is NOT written to cache."""
    unstable = "系統暫時無法穩定生成完整回覆，請稍後再試"
    rewriter = FakeModelClient(["SEARCH", "rq"])
    answer_model = FakeModelClient([unstable])
    search_client = _SearchClient(_SearchResponse([_Result()]))
    answer_cache = TTLCache(ttl_seconds=300, max_entries=64)

    run_rag_pipeline(
        question="酒駕罰則？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
            answer_cache=answer_cache,
        ),
    )
    assert answer_cache.get(("酒駕罰則？", "rq", "traffic")) is None


# ---------------------------------------------------------------------------
# Followup context propagation tests
# ---------------------------------------------------------------------------

def _make_clarification_messages(original_q, option_text, digit):
    """Build a minimal history simulating first-round clarification exchange."""
    clarification_body = (
        f"---\n想提供更精確的答案，請問你的情況是：\n"
        f"(1) {option_text}\n(2) 機車\n(3) 慢車\n(4) 以上皆想了解\n\n直接輸入數字即可"
    )
    return [
        Message(role="user", content=original_q),
        Message(role="assistant", content=f"通用答案內文\n{clarification_body}"),
    ]


def test_pipeline_passes_followup_context_to_answer():
    """followup reply → generate_refined_answer receives non-None followup_context."""
    captured = {}

    def _fake_answer(user_question, search_term, search_results,
                     rewriter_model, answer_model, settings=None, persona=None,
                     followup_context=None, conversation_context=""):
        captured["followup_context"] = followup_context
        captured["user_question"] = user_question
        return "第二輪答案"

    import services.pipeline as _pipeline_mod
    original_fn = _pipeline_mod.generate_refined_answer
    _pipeline_mod.generate_refined_answer = _fake_answer

    try:
        messages = _make_clarification_messages("無照駕駛", "汽車", "1")
        run_rag_pipeline(
            question="1",
            persona=get_persona("traffic"),
            recent_messages=messages,
            **_base_kwargs(
                rewriter_model=FakeModelClient(["rewritten"]),
            ),
        )
    finally:
        _pipeline_mod.generate_refined_answer = original_fn

    fc = captured.get("followup_context")
    assert fc is not None, "followup_context should be passed when user replies with a digit"
    assert fc["original_question"] == "無照駕駛"
    assert fc["chosen_option"] == "汽車"
    assert fc["is_all_options"] is False


def test_pipeline_followup_accepts_option_text_reply():
    """使用者回選項文字（如 'A1'）而非數字，也應被當成追問回覆，不可走 router 被拒答。"""
    captured = {}

    def _fake_answer(user_question, search_term, search_results,
                     rewriter_model, answer_model, settings=None, persona=None,
                     followup_context=None, conversation_context=""):
        captured["followup_context"] = followup_context
        return "第二輪答案"

    import services.pipeline as _pipeline_mod
    original_fn = _pipeline_mod.generate_refined_answer
    _pipeline_mod.generate_refined_answer = _fake_answer

    try:
        messages = _make_clarification_messages("追撞事故處理", "A1（造成人員死亡）", "1")
        run_rag_pipeline(
            question="A1",
            persona=get_persona("traffic"),
            recent_messages=messages,
            **_base_kwargs(rewriter_model=FakeModelClient(["rewritten"])),
        )
    finally:
        _pipeline_mod.generate_refined_answer = original_fn

    fc = captured.get("followup_context")
    assert fc is not None, "回 'A1' 應被當成追問選項 (1)，而非走 router"
    assert fc["original_question"] == "追撞事故處理"
    assert fc["chosen_option"] == "A1（造成人員死亡）"


def test_pipeline_passes_resolved_question_as_user_question():
    """followup reply → user_question passed to answer is resolved text, not raw digit."""
    captured = {}

    def _fake_answer(user_question, search_term, search_results,
                     rewriter_model, answer_model, settings=None, persona=None,
                     followup_context=None, conversation_context=""):
        captured["user_question"] = user_question
        return "第二輪答案"

    import services.pipeline as _pipeline_mod
    original_fn = _pipeline_mod.generate_refined_answer
    _pipeline_mod.generate_refined_answer = _fake_answer

    try:
        messages = _make_clarification_messages("無照駕駛", "汽車", "1")
        run_rag_pipeline(
            question="1",
            persona=get_persona("traffic"),
            recent_messages=messages,
            **_base_kwargs(
                rewriter_model=FakeModelClient(["rewritten"]),
            ),
        )
    finally:
        _pipeline_mod.generate_refined_answer = original_fn

    uq = captured.get("user_question", "")
    assert uq != "1", "user_question must not be the raw digit '1'"
    assert "無照駕駛" in uq
    assert "汽車" in uq


def test_pipeline_passes_conversation_context_excluding_current_question():
    """一般 path：答案層收到前幾輪脈絡，且排除最後一則（當前問題）。"""
    captured = {}

    def _fake_answer(user_question, search_term, search_results,
                     rewriter_model, answer_model, settings=None, persona=None,
                     followup_context=None, conversation_context=""):
        captured["conversation_context"] = conversation_context
        return "答案"

    import services.pipeline as _pipeline_mod
    original_fn = _pipeline_mod.generate_refined_answer
    _pipeline_mod.generate_refined_answer = _fake_answer

    try:
        messages = [
            Message(role="user", content="酒駕怎麼處理"),
            Message(role="assistant", content="酒駕依第35條裁罰。"),
            Message(role="user", content="那拒測呢"),  # 當前問題，須被排除
        ]
        run_rag_pipeline(
            question="那拒測呢",
            persona=get_persona("traffic"),
            recent_messages=messages,
            **_base_kwargs(rewriter_model=FakeModelClient(["SEARCH", "rq"])),
        )
    finally:
        _pipeline_mod.generate_refined_answer = original_fn

    ctx = captured.get("conversation_context", "")
    assert "酒駕怎麼處理" in ctx
    assert "第35條" in ctx
    assert "那拒測呢" not in ctx, "當前問題不應出現在 conversation_context 中"


def test_pipeline_empty_conversation_context_on_first_turn():
    """無前文（第一輪）：conversation_context 為空字串。"""
    captured = {}

    def _fake_answer(user_question, search_term, search_results,
                     rewriter_model, answer_model, settings=None, persona=None,
                     followup_context=None, conversation_context=""):
        captured["conversation_context"] = conversation_context
        return "答案"

    import services.pipeline as _pipeline_mod
    original_fn = _pipeline_mod.generate_refined_answer
    _pipeline_mod.generate_refined_answer = _fake_answer

    try:
        run_rag_pipeline(
            question="酒駕怎麼處理",
            persona=get_persona("traffic"),
            recent_messages=[Message(role="user", content="酒駕怎麼處理")],
            **_base_kwargs(rewriter_model=FakeModelClient(["SEARCH", "rq"])),
        )
    finally:
        _pipeline_mod.generate_refined_answer = original_fn

    assert captured.get("conversation_context") == ""


# --- Cross-reference expansion tests ---


class _SeqSearchClient:
    """Search client that returns different responses per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    def serving_config_path(self, **kwargs):
        return "mock/path"

    def search(self, request):
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return _SearchResponse()


def test_cross_reference_expansion_triggered_when_biaoozhao_in_results():
    """When first search results contain 比照小型汽車, a second search must be issued."""
    result_with_cross_ref = _Result(
        title="第92條",
        snippet="大型重型機車比照小型汽車適用",
        answer="大型重型機車，除本條例另有規定外，比照小型汽車適用其行駛及處罰規定。",
    )
    result_small_car = _Result(title="第45條", snippet="小型汽車行駛路線", answer="小型汽車應依標線行駛。")

    seq_client = _SeqSearchClient([
        _SearchResponse([result_with_cross_ref]),
        _SearchResponse([result_small_car]),
    ])

    result = run_rag_pipeline(
        question="大型重機行駛慢車道",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=FakeModelClient(["SEARCH", "大型重型機車 比照小型汽車 慢車道", "0,1"]),
            answer_model=FakeModelClient(["**結論:** 依§92比照小型汽車。\n**注意事項:**\n- 比照小型汽車\n**法規依據:**\n- **《道路交通管理處罰條例》第 92 條**"]),
            search_client=seq_client,
        ),
    )
    assert seq_client.call_count == 2
    assert "search_expansion" in result.stage_latency_ms


def test_cross_reference_expansion_not_triggered_when_no_biaoozhao():
    """When first search results have no 比照 pattern, only one search is issued."""
    result_normal = _Result(title="第55條", snippet="臨時停車規定", answer="汽車不得任意臨時停車。")

    seq_client = _SeqSearchClient([
        _SearchResponse([result_normal]),
    ])

    result = run_rag_pipeline(
        question="汽車臨時停車規定",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=FakeModelClient(["SEARCH", "汽車 臨時停車 罰鍰", "0"]),
            answer_model=FakeModelClient(["**結論:** 不得任意臨時停車。\n**注意事項:**\n- 注意\n**法規依據:**\n- **《道路交通管理處罰條例》第 55 條**"]),
            search_client=seq_client,
        ),
    )
    assert seq_client.call_count == 1
    assert "search_expansion" not in result.stage_latency_ms


def test_light_violation_expansion_triggers():
    """When question contains a lamp-violation keyword, a second search for §42 must be issued."""
    result_parking = _Result(title="第55條", snippet="臨時停車規定", answer="汽車不得任意臨時停車。")
    result_lamp = _Result(title="第42條", snippet="不依規定使用燈光", answer="汽車駕駛人，不依規定使用燈光者，處罰鍰。")

    seq_client = _SeqSearchClient([
        _SearchResponse([result_parking]),
        _SearchResponse([result_lamp]),
    ])

    result = run_rag_pipeline(
        question="臨時停車使用錯誤燈號舉發哪一條",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=FakeModelClient(["SEARCH", "臨時停車 不依規定使用燈光 第42條 第55條", "0,1"]),
            answer_model=FakeModelClient(["**結論:** 同時違反第55條及第42條。\n**注意事項:**\n- 注意\n**法規依據:**\n- **《道路交通管理處罰條例》第 55 條**\n- **《道路交通管理處罰條例》第 42 條**"]),
            search_client=seq_client,
        ),
    )
    assert seq_client.call_count == 2
    assert "search_light_expansion" in result.stage_latency_ms


def test_light_violation_expansion_skipped():
    """When question has no lamp-violation keyword, only one search is issued."""
    result_normal = _Result(title="第55條", snippet="臨時停車規定", answer="汽車不得任意臨時停車。")

    seq_client = _SeqSearchClient([
        _SearchResponse([result_normal]),
    ])

    result = run_rag_pipeline(
        question="臨時停車違規罰鍰",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=FakeModelClient(["SEARCH", "臨時停車 罰鍰 第55條", "0"]),
            answer_model=FakeModelClient(["**結論:** 違規停車。\n**注意事項:**\n- 注意\n**法規依據:**\n- **《道路交通管理處罰條例》第 55 條**"]),
            search_client=seq_client,
        ),
    )
    assert seq_client.call_count == 1
    assert "search_light_expansion" not in result.stage_latency_ms


def test_light_violation_expansion_direction_indicator():
    """方向燈 keyword → expansion query includes §48 in addition to §42."""
    result_main = _Result(title="第48條", snippet="變換車道規定", answer="汽車駕駛人變換車道，應打方向燈。")
    result_lamp = _Result(title="第42條", snippet="不依規定使用燈光", answer="汽車駕駛人，不依規定使用燈光者，處罰鍰。")

    class _CapturingSearchClient:
        def __init__(self):
            self.queries = []
            self._responses = [_SearchResponse([result_main]), _SearchResponse([result_lamp])]
            self._idx = 0

        def search(self, request):
            self.queries.append(request.query)
            resp = self._responses[self._idx % len(self._responses)]
            self._idx += 1
            return resp

    client = _CapturingSearchClient()

    run_rag_pipeline(
        question="變換車道未打方向燈如何舉發",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=FakeModelClient(["SEARCH", "變換車道 方向燈 第42條 第48條", "0"]),
            answer_model=FakeModelClient(["**結論:** 違反第48條。\n**注意事項:**\n- 注意\n**法規依據:**\n- **《道路交通管理處罰條例》第 48 條**"]),
            search_client=client,
        ),
    )

    assert any("汽車駕駛人轉彎或變換車道" in q and "第48條" in q for q in client.queries)
    expansion_queries = [q for q in client.queries if "第48條" in q and "第42條" in q]
    assert expansion_queries, "方向燈 question must trigger expansion with both §42 and §48"


def test_light_expansion_secondary_result_survives_merge():
    """High-confidence light expansion results must reach answer context."""
    primary_results = [
        _Result(title=f"無關第{i}條", snippet=f"無關內容 {i}", answer=f"無關內容 {i}")
        for i in range(15)
    ]
    result_48 = _Result(
        title="道路交通管理處罰條例 第48條",
        snippet="汽車駕駛人轉彎或變換車道",
        answer="汽車駕駛人轉彎或變換車道時，有下列情形之一者，處罰鍰。",
    )
    captured = {}

    def _fake_answer(user_question, search_term, search_results,
                     rewriter_model, answer_model, settings=None, persona=None,
                     followup_context=None, conversation_context=""):
        captured["titles"] = [r.document.derived_struct_data["title"] for r in search_results]
        return "**結論:** 已引用第48條。\n**注意事項:**\n- 注意\n**法規依據:**\n- **《道路交通管理處罰條例》第 48 條**"

    import services.pipeline as _pipeline_mod
    original_fn = _pipeline_mod.generate_refined_answer
    _pipeline_mod.generate_refined_answer = _fake_answer
    try:
        seq_client = _SeqSearchClient([
            _SearchResponse(primary_results),
            _SearchResponse([result_48]),
        ])
        run_rag_pipeline(
            question="變換車道未打方向燈如何舉發",
            persona=get_persona("traffic"),
            recent_messages=[],
            **_base_kwargs(
                rewriter_model=FakeModelClient(["SEARCH", "變換車道 方向燈 第42條 第48條", "0"]),
                search_client=seq_client,
            ),
        )
    finally:
        _pipeline_mod.generate_refined_answer = original_fn

    titles = captured["titles"]
    assert "道路交通管理處罰條例 第48條" in titles[:10]


def test_merge_dedup_collapses_same_article_chunks():
    """Multiple chunks of the same 條 must collapse to one (highest-ranked) slot."""
    from services.pipeline import _merge_search_results

    primary = _SearchResponse([
        _Result(title="道路交通管理處罰條例 第 92 條"),
        _Result(title="道路交通管理處罰條例 第 92 條"),  # dup chunk of §92
        _Result(title="道路交通管理處罰條例 第 90-3 條"),
        _Result(title="道路交通管理處罰條例 第 90-3 條"),  # dup chunk
    ])
    secondary = _SearchResponse([])
    merged = _merge_search_results(primary, secondary, limit=15)
    titles = [r.document.derived_struct_data["title"] for r in merged]
    assert titles.count("道路交通管理處罰條例 第 92 條") == 1
    assert titles.count("道路交通管理處罰條例 第 90-3 條") == 1
    assert len(merged) == 2


def test_merge_promotes_secondary_article_into_top10():
    """A penalty article ranked low in primary but high in secondary must be
    promoted into the top-10 window (the §45 大型重機 case)."""
    from services.pipeline import _merge_search_results

    # §45 sits at rank 14 in primary (past the top-10 cutoff)...
    primary = _SearchResponse(
        [_Result(title=f"道路交通管理處罰條例 第 {n} 條")
         for n in [92, 2, 22, 73, 32, 90, 69, 72, 33, 31, 44, 43, 46]]
        + [_Result(title="道路交通管理處罰條例 第 45 條")]
    )
    # ...but rank 2 in the 比照 expansion.
    secondary = _SearchResponse([
        _Result(title="道路交通管理處罰條例 第 70 條"),
        _Result(title="道路交通管理處罰條例 第 45 條"),
    ])
    merged = _merge_search_results(primary, secondary, limit=15, secondary_min=3)
    titles = [r.document.derived_struct_data["title"] for r in merged]
    assert "道路交通管理處罰條例 第 45 條" in titles[:10]
    # §45 must appear exactly once (article dedup, not duplicated by promote)
    assert titles.count("道路交通管理處罰條例 第 45 條") == 1


def test_light_violation_expansion_slow_vehicle():
    """自行車 keyword → expansion query targets §73 (慢車 燈光規定), not §42."""
    result_main = _Result(title="第73條", snippet="慢車規定", answer="慢車行駛夜間應開啟燈光。")
    result_lamp = _Result(title="第73條", snippet="夜間行車未開啟燈光", answer="慢車駕駛人違規。")

    class _CapturingSearchClient2:
        def __init__(self):
            self.queries = []
            self._responses = [_SearchResponse([result_main]), _SearchResponse([result_lamp])]
            self._idx = 0

        def search(self, request):
            self.queries.append(request.query)
            resp = self._responses[self._idx % len(self._responses)]
            self._idx += 1
            return resp

    client = _CapturingSearchClient2()

    run_rag_pipeline(
        question="自行車夜間未開大燈違規",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=FakeModelClient(["SEARCH", "慢車 自行車 夜間行車未開啟燈光 第73條", "0"]),
            answer_model=FakeModelClient(["**結論:** 違反第73條。\n**注意事項:**\n- 注意\n**法規依據:**\n- **《道路交通管理處罰條例》第 73 條**"]),
            search_client=client,
        ),
    )

    expansion_queries = [q for q in client.queries if "第73條" in q and "慢車" in q]
    assert expansion_queries, "自行車 question must trigger expansion with §73 慢車 query"
    assert not any("第42條" in q and "慢車" not in q for q in expansion_queries), \
        "慢車 expansion must not use §42-only query"


# --- Phase 1A: BLOCK does not wait for rewrite ---


def test_block_does_not_wait_for_rewrite():
    """BLOCK via local regex should complete without waiting for the slow rewrite future."""

    class _SlowModel:
        provider = "test"
        model_name = "test-model"

        def generate_text(self, prompt, **kwargs):
            time.sleep(2)  # simulate slow rewrite
            return ModelResponse(
                text="rewritten",
                finish_reason="STOP",
                usage=ModelUsage(prompt=1, output=1, total=2),
            )

    # "你好" hits LOCAL_BLOCK_PATTERNS — router returns BLOCK without calling the model.
    # rewrite_future IS submitted and calls generate_text (sleeps 2s).
    # With the fix, pipeline exits before rewrite finishes.
    t0 = time.monotonic()
    result = run_rag_pipeline(
        question="你好",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(rewriter_model=_SlowModel()),
    )
    elapsed = time.monotonic() - t0

    assert result.intent == "BLOCK"
    assert "rewrite" not in result.stage_latency_ms
    assert elapsed < 1.0, f"BLOCK should not wait for slow rewrite (took {elapsed:.2f}s)"


# --- Phase 1B: Rewrite timeout falls back to local expand ---


def test_rewrite_timeout_falls_back_to_local_expand():
    """When rewrite takes longer than REWRITE_TIMEOUT_S, pipeline uses local_query_expand."""
    from services.pipeline import REWRITE_TIMEOUT_S

    class _TimeoutRewriter:
        provider = "test"
        model_name = "test-model"

        def generate_text(self, prompt, **kwargs):
            # Identify call type by prompt content so router/decompose/normalize
            # stay fast and only the rewrite call blocks past timeout.
            if "意圖分類器" in prompt:
                return ModelResponse(
                    text="SEARCH",
                    finish_reason="STOP",
                    usage=ModelUsage(prompt=1, output=1, total=2),
                )
            if "RAG 法規查詢分析器" in prompt:
                return ModelResponse(
                    text='["違停怎麼罰"]',
                    finish_reason="STOP",
                    usage=ModelUsage(prompt=1, output=1, total=2),
                )
            if "術語對齊助手" in prompt:
                return ModelResponse(
                    text='{"normalized": "違停怎麼罰", "changes": []}',
                    finish_reason="STOP",
                    usage=ModelUsage(prompt=1, output=1, total=2),
                )
            time.sleep(REWRITE_TIMEOUT_S + 2)
            return ModelResponse(
                text="never reached",
                finish_reason="STOP",
                usage=ModelUsage(prompt=1, output=1, total=2),
            )

    # "違停" matches local_query_expand → fallback query includes 第55條
    result = run_rag_pipeline(
        question="違停怎麼罰",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(rewriter_model=_TimeoutRewriter()),
    )

    assert result.intent == "SEARCH"
    assert result.error is None
    # rewrite latency should be exactly REWRITE_TIMEOUT_S * 1000 (set by fallback)
    assert result.stage_latency_ms.get("rewrite") == REWRITE_TIMEOUT_S * 1000


# --- Phase 2: Structured progress payload ---


def test_progress_event_includes_stage_and_eta():
    """Every progress callback call carries stage, message, and eta_text keys."""
    messages = []
    rewriter = FakeModelClient(["SEARCH", "rq"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result()]))

    run_rag_pipeline(
        question="酒駕罰則？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
        progress_callback=messages.append,
    )

    by_stage = {m["stage"]: m for m in messages if isinstance(m, dict)}
    for stage in ("router", "rewrite", "search", "answer"):
        assert stage in by_stage, f"missing stage: {stage}"
        assert "message" in by_stage[stage]
        assert "eta_text" in by_stage[stage]
        assert by_stage[stage]["eta_text"]  # non-empty string


# --- Phase 4: Fast cache ---


def test_fast_cache_hit_skips_router_and_rewrite():
    """Second identical question returns from fast cache without calling the model."""
    answer_cache = TTLCache(ttl_seconds=300, max_entries=64)
    slow_model = FakeModelClient(["SEARCH", "rq"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result()]))

    kwargs = dict(
        question="違停怎麼罰",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=slow_model,
            answer_model=answer_model,
            search_client=search_client,
            answer_cache=answer_cache,
        ),
    )

    # First call: runs full pipeline
    result1 = run_rag_pipeline(**kwargs)
    assert result1.intent == "SEARCH"
    assert "fast_cache_hit" not in result1.stage_latency_ms

    # Second call: same question + persona, model has no more items — must not be called
    result2 = run_rag_pipeline(**kwargs)
    assert result2.answer == result1.answer
    assert result2.stage_latency_ms == {"fast_cache_hit": 0.0}


# --- Method H: Query decomposition ---


class _PromptAwareRewriter:
    """Routes by prompt content so router/decompose/rewrite/filter can be answered independently.

    `rewrite_map` lets the test pin specific rewrite output per sub-query (matched by
    substring), avoiding thread-scheduling flakiness when sub-queries fan out in parallel.
    """

    provider = "test"
    model_name = "test-model"

    def __init__(self, *, decomposer_text, rewrite_map=None, default_rewrite="rq",
                 filter_text="0"):
        self.decomposer_text = decomposer_text
        self.rewrite_map = dict(rewrite_map or {})
        self.default_rewrite = default_rewrite
        self.filter_text = filter_text
        self.calls = []

    def generate_text(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        if "意圖分類器" in prompt:
            text = "SEARCH"
        elif "RAG 法規查詢分析器" in prompt:
            text = self.decomposer_text
        elif "搜尋關鍵字" in prompt:
            text = self.default_rewrite
            for key, val in self.rewrite_map.items():
                if key in prompt:
                    text = val
                    break
        else:
            text = self.filter_text
        return ModelResponse(
            text=text,
            finish_reason="STOP",
            usage=ModelUsage(prompt=1, output=1, total=2),
        )


def test_pipeline_decomposition_runs_parallel_searches():
    """Compound question is decomposed; each sub-query runs its own search.

    After Phase A2 optimization, sub-queries skip the LLM rewrite and use
    local_query_expand instead. "臨時停車" is hard-expanded to §55 and
    "使用錯誤燈號" is expanded to the lamp articles.
    """
    rewriter = _PromptAwareRewriter(
        decomposer_text='["臨時停車", "使用錯誤燈號"]',
        rewrite_map={},  # sub-queries no longer call rewriter; map unused
        filter_text="0,1",
    )
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result(title="法規")]))

    result = run_rag_pipeline(
        question="臨時停車使用錯誤燈號",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
    )

    assert result.intent == "SEARCH"
    assert result.error is None
    assert "decompose" in result.stage_latency_ms
    # At least one search per sub-query (light_violation_expansion may add one more).
    search_queries = [c.query for c in search_client.calls]
    assert any("臨時停車" in q and "第55條" in q for q in search_queries)
    # "使用錯誤燈號" matches 燈號 regex → appended with §42 §48 keywords
    assert any("第42條" in q and "使用錯誤燈號" in q for q in search_queries)
    assert len(search_client.calls) >= 2


def test_pipeline_decomposition_labels_subfacet_results_for_answer():
    rewriter = _PromptAwareRewriter(
        decomposer_text='["臨時停車", "使用錯誤燈號"]',
        filter_text="0,1",
    )
    result_55 = _Result(title="道路交通管理處罰條例 第55條", answer="汽車駕駛人臨時停車規定。")
    result_42 = _Result(title="道路交通管理處罰條例 第42條", answer="汽車駕駛人不依規定使用燈光。")

    class _SeqClient:
        def __init__(self):
            self.calls = []
            self._responses = [_SearchResponse([result_55]), _SearchResponse([result_42]), _SearchResponse([])]
            self._idx = 0

        def serving_config_path(self, **kwargs):
            return "serving-config"

        def search(self, request):
            self.calls.append(request)
            resp = self._responses[self._idx % len(self._responses)]
            self._idx += 1
            return resp

    captured = {}

    def _fake_answer(user_question, search_term, search_results,
                     rewriter_model, answer_model, settings=None, persona=None,
                     followup_context=None, conversation_context=""):
        captured["facets"] = [
            (getattr(r, "sub_query_index", None), getattr(r, "sub_query", None))
            for r in search_results
        ]
        captured["raw_titles"] = [
            getattr(getattr(r, "result", r), "document").derived_struct_data["title"]
            for r in search_results
            if hasattr(getattr(r, "result", r), "document")
        ]
        return "**結論:** 已保留兩個子面向。\n**注意事項:**\n- 注意\n**法規依據:**\n- **《道路交通管理處罰條例》第 55 條**\n- **《道路交通管理處罰條例》第 42 條**"

    import services.pipeline as _pipeline_mod
    original_fn = _pipeline_mod.generate_refined_answer
    _pipeline_mod.generate_refined_answer = _fake_answer
    try:
        run_rag_pipeline(
            question="臨時停車使用錯誤燈號",
            persona=get_persona("traffic"),
            recent_messages=[],
            **_base_kwargs(
                rewriter_model=rewriter,
                search_client=_SeqClient(),
            ),
        )
    finally:
        _pipeline_mod.generate_refined_answer = original_fn

    assert (1, "臨時停車") in captured["facets"]
    assert (2, "使用錯誤燈號") in captured["facets"]
    assert "道路交通管理處罰條例 第55條" in captured["raw_titles"]
    assert "道路交通管理處罰條例 第42條" in captured["raw_titles"]


def test_pipeline_single_facet_skips_decomposition_search():
    """Single-facet question runs exactly one search via the original path."""
    rewriter = _PromptAwareRewriter(
        decomposer_text='["機車行駛人行道"]',
        rewrite_map={"機車行駛人行道": "機車行駛人行道 第74-1條"},
        filter_text="0",
    )
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result(title="法規")]))

    result = run_rag_pipeline(
        question="機車行駛人行道",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
    )

    assert result.intent == "SEARCH"
    # Exactly one search call (no decomposition fan-out).
    assert len(search_client.calls) == 1


def test_pipeline_decomposition_failure_falls_back_to_single_query():
    """If decomposer returns invalid JSON, pipeline still works via single-query path."""
    rewriter = _PromptAwareRewriter(
        decomposer_text="garbage not json",
        rewrite_map={"酒駕罰則": "原查詢擴充"},
        filter_text="0",
    )
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result(title="法規")]))

    result = run_rag_pipeline(
        question="酒駕罰則",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
    )

    assert result.intent == "SEARCH"
    assert len(search_client.calls) == 1


# --- Method C+A: anti_terms strips polluting words from rewrite output ---


def test_pipeline_motorcycle_pedestrian_query_includes_article_45():
    """機車人行道規則對應 §3 + §45 — 確認 search 看到定義跳板與罰則。"""
    import pytest
    pytest.skip("依賴完整正式同義詞字典；公開版僅附範例資料（data/legal_synonyms.json）")
    rewriter = _PromptAwareRewriter(
        decomposer_text='["機車行駛人行道"]',
        rewrite_map={"機車行駛人行道": "道路交通管理 條文"},
        filter_text="0",
    )
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result(title="法規")]))

    run_rag_pipeline(
        question="機車行駛人行道",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
    )

    sent_query = search_client.calls[0].query
    # Must contain the prepended §3 definition bridge and §45 law terms.
    assert "汽車包括機車" in sent_query
    assert "第3條" in sent_query
    assert "駕車行駛人行道" in sent_query
    assert "第45條" in sent_query
    assert "汽車駕駛人" in sent_query


# --- Preset question typed by user returns fixed answer ---


def test_pipeline_typed_preset_question_returns_fixed_answer():
    """打字輸入與常見問題相同的問題 → 直接回固定答案，不走 router/search。"""
    from presets import get_preset

    router_model = FakeModelClient(["SEARCH", "rewritten"])
    search_client = _SearchClient(_SearchResponse([_Result()]))

    result = run_rag_pipeline(
        question="毒駕標準作業程序為何？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(rewriter_model=router_model, search_client=search_client),
    )

    assert result.intent == "PRESET"
    assert result.answer == get_preset("drug_driving_sop")["answer"]
    assert result.error is None
    assert "preset_matched" in result.stage_latency_ms
    # Pipeline skipped entirely: no router/search calls
    assert router_model.calls == []
    assert search_client.calls == []


def test_pipeline_typed_preset_question_without_question_mark():
    """尾端沒打問號也命中 preset。"""
    from presets import get_preset

    result = run_rag_pipeline(
        question="肇事逃逸定義為何",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(),
    )
    assert result.intent == "PRESET"
    assert result.answer == get_preset("hit_and_run_def")["answer"]


def test_pipeline_preset_match_beats_fast_cache():
    """preset 命中優先於 fast cache，快取答案不會蓋掉固定答案。"""
    from presets import get_preset

    answer_cache = TTLCache(ttl_seconds=300, max_entries=64)
    answer_cache.set(("道路交通事故定義為何？", "traffic"), "舊的 RAG 快取答案")

    result = run_rag_pipeline(
        question="道路交通事故定義為何？",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(answer_cache=answer_cache),
    )
    assert result.intent == "PRESET"
    assert result.answer == get_preset("traffic_accident_def")["answer"]


def test_pipeline_digit_after_preset_menu_still_runs_followup():
    """打字得到 preset 答案（含選單）後回數字 → 走既有第二輪 RAG，不再回 preset。"""
    from presets import get_preset

    preset_answer = get_preset("traffic_accident_def")["answer"]
    rewriter = FakeModelClient(["rewritten query", "0"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(_SearchResponse([_Result(title="法規A")]))

    result = run_rag_pipeline(
        question="1",
        persona=get_persona("traffic"),
        recent_messages=[
            Message(role="user", content="道路交通事故定義為何？"),
            Message(role="assistant", content=preset_answer),
        ],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
        ),
    )
    assert result.intent == "SEARCH"
    assert result.error is None
    assert len(search_client.calls) > 0


# --- Rerank wiring (1-1b) ---


def test_pipeline_rerank_not_called_when_disabled(monkeypatch):
    """預設 rerank_enabled=False：rerank_results 完全不被呼叫，無 rerank 延遲鍵。"""
    called = []
    monkeypatch.setattr(
        "services.pipeline.rerank_results",
        lambda *a, **k: called.append(a) or a[1],
    )
    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(),
    )
    assert result.intent == "SEARCH"
    assert called == []
    assert "rerank" not in result.stage_latency_ms


def test_pipeline_rerank_enabled_reorders_sources(monkeypatch):
    """rerank_enabled=True：重排結果反映到 sources 編號與 stage_latency_ms。"""
    captured = {}

    def fake_rerank(query, results, settings, request_id=None):
        captured["query"] = query
        captured["count"] = len(results)
        return list(reversed(results))

    monkeypatch.setattr("services.pipeline.rerank_results", fake_rerank)
    rewriter = FakeModelClient(["SEARCH", "rewritten query", "0"])
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    search_client = _SearchClient(
        _SearchResponse([_Result(title="法規A"), _Result(title="法規B")])
    )
    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            search_client=search_client,
            settings=make_settings(rerank_enabled=True),
        ),
    )
    assert result.intent == "SEARCH"
    # rerank query 用 normalized_question（此題無 dict 命中＝原問題），非 rewritten_query
    assert captured["query"] == "無關問題"
    assert captured["count"] == 2
    assert "rerank" in result.stage_latency_ms
    # 重排後 sources 順序（citation 編號依重排結果重新起算）
    assert [s["title"] for s in result.sources] == ["法規B", "法規A"]


def test_pipeline_rerank_query_uses_dict_normalized_question(monkeypatch):
    """dict 命中題：rerank query 收到展開後的 normalized_question（含罰則條號），
    避免 semantic ranker 把罰則條文排到行為規範之後（1-1c 發現的回歸，
    ebike_helmet/heavy_bike_slow_lane 即因此掉分）。"""
    import pytest
    pytest.skip("依賴完整正式同義詞字典；公開版僅附範例資料（data/legal_synonyms.json）")
    captured = {}

    def fake_rerank(query, results, settings, request_id=None):
        captured["query"] = query
        return list(results)

    monkeypatch.setattr("services.pipeline.rerank_results", fake_rerank)
    rewriter = _PromptAwareRewriter(
        decomposer_text='["機車行駛人行道"]',
        rewrite_map={"機車行駛人行道": "道路交通管理 條文"},
        filter_text="0",
    )
    answer_model = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    run_rag_pipeline(
        question="機車行駛人行道",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(
            rewriter_model=rewriter,
            answer_model=answer_model,
            settings=make_settings(rerank_enabled=True),
        ),
    )
    assert "機車行駛人行道" in captured["query"]
    assert "第45條" in captured["query"]  # dict 展開的罰則條號進了 rerank query


# --- Grounding check wiring (1-3b) ---


def test_pipeline_grounding_not_called_when_disabled(monkeypatch):
    """預設 grounding_enabled=False：check_grounding 完全不被呼叫、無延遲鍵、score=None。"""
    called = []
    monkeypatch.setattr(
        "services.pipeline.check_grounding",
        lambda *a, **k: called.append(a) or 0.9,
    )
    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(),
    )
    assert result.intent == "SEARCH"
    assert called == []
    assert "grounding" not in result.stage_latency_ms
    assert result.grounding_score is None


def test_pipeline_grounding_low_score_appends_warning(monkeypatch):
    """enabled + 低於門檻：答案尾端附警示、score 與延遲鍵齊備。"""
    captured = {}

    def fake_check(answer, sources, settings, request_id=None):
        captured["answer"] = answer
        captured["source_titles"] = [s["title"] for s in sources]
        return 0.3

    monkeypatch.setattr("services.pipeline.check_grounding", fake_check)
    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(settings=make_settings(grounding_enabled=True)),
    )
    assert result.intent == "SEARCH"
    assert result.grounding_score == 0.3
    assert "grounding" in result.stage_latency_ms
    assert "核對原始法規" in result.answer
    # 檢核吃的是生成答案與引用來源（sources 與 PipelineResult.sources 同源）
    assert "結論" in captured["answer"]
    assert captured["source_titles"] == [s["title"] for s in result.sources]


def test_pipeline_grounding_high_score_no_warning(monkeypatch):
    """enabled + 高於門檻：不附警示，score 照記。"""
    monkeypatch.setattr(
        "services.pipeline.check_grounding", lambda *a, **k: 0.95
    )
    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(settings=make_settings(grounding_enabled=True)),
    )
    assert result.grounding_score == 0.95
    assert "核對原始法規" not in result.answer


def test_pipeline_grounding_failure_leaves_answer_unchanged(monkeypatch):
    """檢核失敗（回 None）：答案不變、score=None、不阻斷主流程。"""
    monkeypatch.setattr(
        "services.pipeline.check_grounding", lambda *a, **k: None
    )
    result = run_rag_pipeline(
        question="無關問題",
        persona=get_persona("traffic"),
        recent_messages=[],
        **_base_kwargs(settings=make_settings(grounding_enabled=True)),
    )
    assert result.error is None
    assert result.grounding_score is None
    assert "核對原始法規" not in result.answer


def test_pipeline_grounding_warning_persists_in_answer_cache(monkeypatch):
    """低分警示先併入答案再進快取：快取命中回的答案帶警示、不重跑檢核。"""
    calls = []
    monkeypatch.setattr(
        "services.pipeline.check_grounding",
        lambda *a, **k: calls.append(1) or 0.2,
    )
    settings = make_settings(grounding_enabled=True)
    shared = _base_kwargs(settings=settings)
    kwargs1 = dict(shared)
    kwargs1["rewriter_model"] = FakeModelClient(["SEARCH", "rewritten keywords"])
    kwargs1["answer_model"] = FakeModelClient(["**結論:** A\n**注意事項:** C\n**法規依據:** B"])
    first = run_rag_pipeline(
        question="無關問題", persona=get_persona("traffic"), recent_messages=[], **kwargs1
    )
    kwargs2 = dict(shared)
    kwargs2["rewriter_model"] = FakeModelClient(["SEARCH", "rewritten keywords"])
    kwargs2["answer_model"] = FakeModelClient(["不該被用到"])
    second = run_rag_pipeline(
        question="無關問題", persona=get_persona("traffic"), recent_messages=[], **kwargs2
    )
    assert "核對原始法規" in first.answer
    assert "核對原始法規" in second.answer  # 快取命中仍帶警示
    assert second.answer == first.answer
    assert len(calls) == 1  # 檢核只在生成當輪跑一次
