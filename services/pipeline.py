import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FuturesTimeoutError
from dataclasses import dataclass
from typing import Callable, List, Optional

from config import Settings
from models import Message
from personas import Persona
from presets import match_preset_question
from rag_logic import extract_result_content, extract_result_data, extract_result_title
from services.answer_service import (
    build_citation_sources,
    generate_refined_answer,
    generate_refined_answer_streaming,
)
from services.decomposer_service import decompose_query, is_obviously_single_facet
from services.synonym_service import expand_terms_detailed
from services.term_normalizer import normalize_terms
from services.local_query_expand import local_query_expand
from services.grounding_service import GROUNDING_WARNING_BLOCK, check_grounding
from services.rerank_service import rerank_results
from services.rewrite_service import rewrite_query
from services.router_service import semantic_router
from services.search_service import search_vertex
from services.telemetry import classify_error, log_event
from services.timing import measure_ms


@dataclass(frozen=True)
class PipelineResult:
    """Immutable result from a single RAG pipeline execution."""

    answer: str
    intent: str
    stage_latency_ms: dict
    request_id: str
    error: Optional[str] = None
    # 進 answer prompt 的編號來源（[{"index", "title", "content"}]），
    # 供引用 UI 與 context_recall 評估；preset/fast-cache 路徑為空 tuple
    sources: tuple = ()
    # Check Grounding 忠實度分數（0~1）；未啟用/檢核失敗/非生成路徑為 None
    grounding_score: Optional[float] = None


@dataclass(frozen=True)
class _SubQueryResult:
    """Search result plus the decomposition facet that produced it."""

    result: object
    sub_query: str
    sub_query_index: int


_CROSS_REF_PATTERN = re.compile(r"比照小型汽車|準用.*?小型汽車|比照小型車")
REWRITE_TIMEOUT_S = 3.0
DECOMPOSE_TIMEOUT_S = 5.0
SUB_QUERY_SEARCH_TIMEOUT_S = 15.0
_LIGHT_VIOLATION_PATTERN = re.compile(r"燈號|信號燈|方向燈|警示燈|危險警告燈|未開大燈|未開燈光")
# 牌照/號牌 + 污損/塗抹/遮蔽/無法辨識 → 罰則在處罰條例 §13/§14（見
# local_query_expand 同義規則）。用於讓這類題直接走確定性 dict 展開、不空等
# LLM rewrite 逾時。與 local_query_expand 的牌照規則保持同步。
_PLATE_DEFACEMENT_PATTERN = re.compile(
    r"(?=.*(牌照|號牌))(?=.*(損毀|變造|塗抹|污損|汙損|污穢|汙穢|遮蔽|無法辨識|不能辨))"
)


def _extract_behavior_keywords(question: str) -> str:
    """Extract behavior keywords from the original question for expansion query.

    Returns a short keyword string (≤ 10 chars) stripped of vehicle-type prefix words.
    """
    # Remove common vehicle prefix words so the expansion stays behavior-focused
    cleaned = re.sub(r"大型重型機車|大型重機|小型汽車|機車|汽車|重機|輕機", "", question).strip()
    # Take up to first 20 chars to keep the query compact
    return cleaned[:20].strip()


def _result_key(result):
    """Stable-ish key for deduping Vertex results and test doubles."""
    result = getattr(result, "result", result)
    doc = getattr(result, "document", None)
    return getattr(doc, "id", None) or id(result)


_ARTICLE_KEY_PATTERN = re.compile(r"第\s*(\d+(?:-\d+)?)\s*條")


def _article_key(result):
    """Article-level dedup key: (law_name, 條號). None if the title has no 第N條.

    Different chunks of the same 條 carry different document ids, so chunk-id
    dedup (`_result_key`) lets them all through and they crowd out other
    articles in the top-10 context window. Collapsing by (law, article) keeps
    only the highest-ranked chunk per 條.
    """
    raw = getattr(result, "result", result)
    title = extract_result_title(extract_result_data(raw))
    if not title:
        return None
    match = _ARTICLE_KEY_PATTERN.search(title)
    if not match:
        return None
    law_name = title[: match.start()].strip()
    return (law_name, match.group(1))


def _dedup_by_article(results):
    """Keep only the first (highest-ranked) chunk per (law, 條號).

    Results without a parseable 條號 are always kept (cannot collapse safely).
    """
    seen_articles = set()
    out = []
    for r in results:
        akey = _article_key(r)
        if akey is not None:
            if akey in seen_articles:
                continue
            seen_articles.add(akey)
        out.append(r)
    return out


def _merge_search_results(primary, secondary, *, limit=15, secondary_min=0, secondary_insert_at=7):
    """Merge two search responses while preserving high-confidence expansion hits.

    Primary search usually returns 15 results. If expansion hits are simply
    appended and then truncated, the extra query never reaches answer context
    (which reads the first 10 sources). For high-confidence expansions, promote
    a few secondary hits into the first 10 slots.
    """
    primary_results = list(getattr(primary, "results", []))
    secondary_results = list(getattr(secondary, "results", []))

    # Dedup BY ARTICLE (法名+條號), not chunk id. Different chunks of the same 條
    # carry different document ids, so chunk-id dedup let duplicate chunks crowd
    # the top-`limit` window.
    def _dedup_key(r):
        akey = _article_key(r)
        return akey if akey is not None else _result_key(r)

    # The secondary search (e.g. the 比照 "小型汽車 + behavior" query) often ranks
    # the real penalty article (§45) far higher than the primary search does. If
    # we dedup secondary against primary first, that article keeps its low primary
    # rank and gets truncated out of the top-`limit` window. So promote the
    # secondary's top `secondary_min` articles to the FRONT, even when they also
    # appear (lower) in primary — the secondary copy wins its slot.
    promoted = []
    promoted_keys = set()
    if secondary_min > 0:
        for r in secondary_results:
            k = _dedup_key(r)
            if k in promoted_keys:
                continue
            promoted_keys.add(k)
            promoted.append(r)
            if len(promoted) >= secondary_min:
                break

    seen = set(promoted_keys)
    unique_primary = []
    for r in primary_results:
        k = _dedup_key(r)
        if k in seen:
            continue
        seen.add(k)
        unique_primary.append(r)

    unique_secondary = []
    for r in secondary_results:
        k = _dedup_key(r)
        if k in seen:
            continue
        seen.add(k)
        unique_secondary.append(r)

    if promoted:
        insert_at = min(max(0, secondary_insert_at), len(unique_primary))
        merged = (
            unique_primary[:insert_at]
            + promoted
            + unique_primary[insert_at:]
            + unique_secondary
        )
    else:
        merged = unique_primary + unique_secondary

    return merged[:limit]


def _expansion_added_count(primary_results, merged_results):
    """Count expansion results retained after merge; never returns negatives."""
    primary_ids = {_result_key(r) for r in primary_results}
    return sum(1 for r in merged_results if _result_key(r) not in primary_ids)


class _MergedResponse:
    """Lightweight container so merged result lists quack like a Vertex SearchResponse."""

    def __init__(self, results):
        self.results = results


def _maybe_expand_search(
    *, search_response, trigger, expansion_query, event_name,
    stage_key, settings, search_client, request_id, stage_latency_ms,
):
    """Run a secondary search and merge if trigger=True. Always logs the event.

    Returns the (possibly updated) search_response. Expansion failure never
    blocks the main flow — on exception the original response is returned.
    """
    if not trigger:
        log_event(event_name, request_id=request_id,
                  triggered=False, expansion_hit_count=0, latency_ms=0.0)
        return search_response
    base = list(getattr(search_response, "results", []))
    try:
        resp, ms = measure_ms(search_vertex, expansion_query, settings, search_client)
        merged = _merge_search_results(search_response, resp, limit=15, secondary_min=3)
        hit = _expansion_added_count(base, merged)
        stage_latency_ms[stage_key] = round(ms, 1)
        log_event(event_name, request_id=request_id,
                  triggered=True, expansion_hit_count=hit, latency_ms=round(ms, 1))
        return _MergedResponse(merged)
    except Exception:
        log_event(event_name, request_id=request_id,
                  triggered=True, expansion_hit_count=0, latency_ms=0.0)
        return search_response


def _interleave_dedup(result_lists, limit=15):
    """Interleave multiple ordered result lists, deduplicating by document id.

    Round-robin pick keeps each sub-query's top hits represented in the merged
    output instead of letting one sub-query monopolize the top slots.
    """
    seen_ids = set()
    merged = []
    indices = [0] * len(result_lists)
    active = True
    while active and len(merged) < limit:
        active = False
        for idx, results in enumerate(result_lists):
            if indices[idx] >= len(results):
                continue
            active = True
            r = results[indices[idx]]
            indices[idx] += 1
            rid = _result_key(r)
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            merged.append(r)
            if len(merged) >= limit:
                break
    return merged


def _notify(callback, message):
    """Fire progress callback if provided; never raise."""
    if callback is not None:
        try:
            callback(message)
        except Exception:
            pass


def run_rag_pipeline(
    question: str,
    persona: Persona,
    recent_messages: List[Message],
    *,
    rewriter_model,
    answer_model,
    search_client,
    settings: Settings,
    search_cache,
    answer_cache,
    persona_id: str,
    request_id: Optional[str] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    stream_callback: Optional[Callable[[str], None]] = None,
) -> PipelineResult:
    """Execute the full RAG pipeline: route -> rewrite -> search -> answer.

    Completely Streamlit-free. All UI feedback happens through the optional
    progress_callback, which receives a status string at each stage.
    """
    if request_id is None:
        request_id = uuid.uuid4().hex[:12]

    request_started_at = time.perf_counter()
    log_event(
        "request_started",
        request_id=request_id,
        question_len=len(question),
        persona_id=persona_id,
    )

    stage_latency_ms = {}
    error_msg = None
    answer_sources: list = []  # 進 answer prompt 的編號來源；BLOCK/錯誤路徑保持空
    grounding_score = None  # Check Grounding 分數；未啟用/快取命中/失敗保持 None

    # --- Preset match: typed question identical to a常見問題 returns its fixed
    # answer directly (same as clicking the sidebar button), skipping the whole
    # pipeline. Menus in preset answers still drive the followup flow because
    # digit replies are resolved from conversation history, not from this path.
    matched_preset = match_preset_question(question)
    if matched_preset is not None:
        log_event(
            "preset_matched",
            request_id=request_id,
            preset_id=matched_preset["id"],
        )
        return PipelineResult(
            answer=matched_preset["answer"],
            intent="PRESET",
            stage_latency_ms={"preset_matched": 0.0},
            request_id=request_id,
            error=None,
        )

    # --- Build lightweight conversation context for the answer layer ---
    # Excludes the current question (last message) and keeps only the most recent
    # 1-2 turns so the answer can resolve references like 「那機車呢」 without
    # bloating the prompt. Sources still come from this turn's search results.
    def _build_conversation_context(messages: List[Message]) -> str:
        prior = messages[:-1] if messages else []
        prior = prior[-4:]  # at most ~2 Q&A turns
        if not prior:
            return ""
        lines = []
        for msg in prior:
            limit = 200 if msg.role == "user" else 600
            label = "使用者" if msg.role == "user" else "助理"
            lines.append(f"{label}：{msg.content[:limit]}")
        return "\n".join(lines)

    conversation_context = _build_conversation_context(recent_messages)

    # --- Fast path: exact (question, persona) match skips router/rewrite/search ---
    # Digit replies (followup menu selections) must never hit the fast cache: the
    # key ("1", persona) is shared across all conversations, so a cached followup
    # answer from one question would bleed into a completely different question.
    # Questions with conversation context must also skip it: the global key omits
    # the prior turns, so a cached context-free answer would be wrong here.
    is_digit_reply = question.strip().isdigit()
    skip_fast_cache = is_digit_reply or bool(conversation_context)
    fast_cache_key = (question.strip(), persona.id if persona else None)
    cached_fast = None if skip_fast_cache else answer_cache.get(fast_cache_key)
    if cached_fast is not None:
        log_event("fast_cache_hit", request_id=request_id)
        return PipelineResult(
            answer=cached_fast,
            intent="SEARCH",
            stage_latency_ms={"fast_cache_hit": 0.0},
            request_id=request_id,
            error=None,
        )

    # --- Build history text (needed by rewrite, cheap to compute upfront) ---
    history_text = ""
    for msg in recent_messages:
        content_snippet = msg.content[:500]
        history_text += f"{msg.role}: {content_snippet}\n"

    # --- Detect followup reply: digit OR option text after a clarification menu ---
    def _match_option_digit(q: str, messages: List[Message]) -> Optional[str]:
        """Resolve a followup reply to its option number.

        Accepts the option number ("1") OR the option text the user typed instead
        of the number (e.g. "A1" → option (1) "A1（造成人員死亡）", or "汽車").
        The menu must be the most recent assistant message. Returns the number as a
        string, or None if q doesn't match any option.
        """
        import re as _re

        reply = q.strip()
        if not reply:
            return None
        menu = None
        for msg in reversed(messages):
            if msg.role == "assistant" and "直接輸入數字即可" in msg.content:
                menu = msg.content
                break
            if msg.role == "assistant":
                break
        if menu is None:
            return None
        options = _re.findall(r"\((\d+)\)\s*(.+)", menu)
        reply_cf = reply.casefold()
        for num, text in options:
            text = text.strip()
            if reply == num or reply_cf == text.casefold():
                return num
            # short label = text before first paren, e.g. "A1（造成人員死亡）" → "A1"
            short = _re.split(r"[（(]", text, maxsplit=1)[0].strip()
            if short and reply_cf == short.casefold():
                return num
        return None

    def _is_followup_reply(q: str, messages: List[Message]) -> bool:
        """Return True if q matches a preceding clarification menu option."""
        return _match_option_digit(q, messages) is not None

    def _resolve_followup_question(digit: str, messages: List[Message]) -> str:
        """Expand a numeric followup reply into '<original question> <chosen option>'.

        Looks for the last assistant message containing the clarification prompt,
        extracts the chosen option text (e.g. '(1) 汽車' → '汽車'), then finds the
        original user question that triggered the clarification and combines them.
        """
        import re as _re

        chosen_text = ""
        original_question = ""

        for i, msg in enumerate(reversed(messages)):
            idx = len(messages) - 1 - i
            if msg.role == "assistant" and "直接輸入數字即可" in msg.content:
                # Parse option lines like "(1) 汽車" or "(4) 以上皆想了解"
                match = _re.search(
                    rf"\({digit}\)\s*(.+)", msg.content
                )
                if match:
                    chosen_text = match.group(1).strip()
                # Walk back to find the user question that preceded this assistant msg
                for j in range(idx - 1, -1, -1):
                    if messages[j].role == "user":
                        original_question = messages[j].content.strip()
                        break
                break

        if original_question and chosen_text:
            return f"{original_question} {chosen_text}"
        if original_question:
            return original_question
        return digit

    def _parse_followup_context(digit: str, messages: List[Message]) -> Optional[dict]:
        """Return followup context dict for answer_service, or None if not resolvable.

        Returns:
            {
                "original_question": str,  # user question before clarification
                "chosen_option": str,      # option text the user selected
                "is_all_options": bool,    # True if user picked "以上皆想了解"
            }
        """
        import re as _re

        chosen_text = ""
        original_question = ""

        for i, msg in enumerate(reversed(messages)):
            idx = len(messages) - 1 - i
            if msg.role == "assistant" and "直接輸入數字即可" in msg.content:
                match = _re.search(rf"\({digit}\)\s*(.+)", msg.content)
                if match:
                    chosen_text = match.group(1).strip()
                for j in range(idx - 1, -1, -1):
                    if messages[j].role == "user":
                        original_question = messages[j].content.strip()
                        break
                break

        if not original_question or not chosen_text:
            return None

        return {
            "original_question": original_question,
            "chosen_option": chosen_text,
            "is_all_options": "以上皆想了解" in chosen_text,
        }

    # --- Intent classification + Query rewrite (parallel) ---
    followup_context = None
    _notify(progress_callback, {"stage": "router", "message": "正在判斷問題類型", "eta_text": "約 1-2 秒"})
    matched_digit = _match_option_digit(question, recent_messages)
    if matched_digit is not None:
        # Bypass router: treat the menu reply (digit or option text) as SEARCH
        # Resolve to the full option text before rewriting
        resolved_question = _resolve_followup_question(matched_digit, recent_messages)
        followup_context = _parse_followup_context(matched_digit, recent_messages)
        intent = "SEARCH"
        stage_latency_ms["router"] = 0.0
        log_event("router_completed", request_id=request_id, intent=intent,
                  latency_ms=0.0, followup_bypass=True, resolved_question_len=len(resolved_question))
        rewritten_query, stage_latency_ms["rewrite"] = measure_ms(
            rewrite_query, history_text, resolved_question, rewriter_model, persona
        )
        log_event(
            "rewrite_completed",
            request_id=request_id,
            rewritten_query=rewritten_query,
            latency_ms=round(stage_latency_ms["rewrite"], 1),
        )
    else:
        pool = ThreadPoolExecutor(max_workers=2)
        router_future = pool.submit(measure_ms, semantic_router, question, rewriter_model)
        rewrite_future = pool.submit(measure_ms, rewrite_query, history_text, question, rewriter_model, persona)
        try:
            intent, stage_latency_ms["router"] = router_future.result()
        except Exception:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        if intent == "BLOCK":
            pool.shutdown(wait=False, cancel_futures=True)
            log_event("rewrite_cancelled", request_id=request_id, reason="blocked_intent")
        else:
            pool.shutdown(wait=False)  # rewrite_future may still be running; fetched below with timeout
        log_event(
            "router_completed",
            request_id=request_id,
            intent=intent,
            latency_ms=round(stage_latency_ms["router"], 1),
        )

    if intent == "BLOCK":
        final_answer = "抱歉，本系統僅提供法規查詢服務，無法回應其他問題。"
        log_event(
            "request_blocked",
            request_id=request_id,
            reason="non_regulatory_or_disallowed",
        )
    else:
        # Initialized here so followup path (which skips Method C) still defines them.
        normalized_question = question
        term_norm_appended: List[str] = []
        term_norm_anti: List[str] = []
        term_norm_changes: List[str] = []
        term_norm_source = "skipped"
        try:
            _notify(progress_callback, {"stage": "rewrite", "message": "正在分析關鍵字並改寫查詢", "eta_text": "約 3-5 秒"})

            # --- Method C: Term normalization (dict first, LLM fallback) ---
            # Skip on followup turns (already-resolved option text).
            if not followup_context:
                norm_started = time.perf_counter()
                expanded_q, dict_hits, appended_words, anti_words = expand_terms_detailed(question)
                if dict_hits:
                    normalized_question = expanded_q
                    term_norm_source = "dict"
                    term_norm_changes = dict_hits
                    term_norm_appended = appended_words
                    term_norm_anti = anti_words
                else:
                    # Dict missed → LLM normalizer fallback
                    try:
                        normalized_q, llm_changes = normalize_terms(question, rewriter_model)
                    except Exception:
                        normalized_q, llm_changes = question, []
                    if normalized_q != question and llm_changes:
                        normalized_question = normalized_q
                        term_norm_source = "llm"
                        term_norm_changes = llm_changes
                        # LLM may rewrite mid-sentence; treat the suffix delta as appended
                        if normalized_q.startswith(question):
                            tail = normalized_q[len(question):].strip()
                            term_norm_appended = tail.split() if tail else []
                stage_latency_ms["term_normalize"] = round(
                    (time.perf_counter() - norm_started) * 1000, 1
                )
                log_event(
                    "term_normalize_used",
                    request_id=request_id,
                    source=term_norm_source,
                    original=question,
                    normalized=normalized_question,
                    changes=term_norm_changes,
                    appended=term_norm_appended,
                    anti=term_norm_anti,
                    latency_ms=stage_latency_ms["term_normalize"],
                )

            # --- Method H: Query decomposition for compound questions ---
            # Only attempt on non-followup turns (followup already has resolved_question).
            # Cheap regex prefilter (Method A): skip LLM call for obviously single-facet queries.
            # Always evaluate against the ORIGINAL question — Method C's expansion always
            # inflates the string past 12 chars and would defeat the prefilter.
            sub_queries = [normalized_question]
            decomposition_used = False
            decompose_skipped_by_prefilter = False
            if not followup_context:
                if is_obviously_single_facet(question):
                    decompose_skipped_by_prefilter = True
                    stage_latency_ms["decompose"] = 0.0
                    log_event(
                        "decompose_used",
                        request_id=request_id,
                        used=False,
                        sub_count=1,
                        sub_queries=[normalized_question],
                        latency_ms=0.0,
                        prefilter_skipped=True,
                    )
                else:
                    decompose_started = time.perf_counter()
                    try:
                        # 用「原始問題」而非 normalized_question 拆解：term_normalizer
                        # 會把口語詞改寫成正式用語（如「超速」→「違反速限規定」），
                        # 反而讓拆出的子問題檢索命中率下降（§40 稀釋）。decomposer
                        # 的 prompt 本就設計來吃口語問題，餵原始問題最符合其意圖。
                        sub_queries = decompose_query(question, rewriter_model)
                    except Exception:
                        sub_queries = [normalized_question]
                    stage_latency_ms["decompose"] = round(
                        (time.perf_counter() - decompose_started) * 1000, 1
                    )
                    decomposition_used = len(sub_queries) > 1
                    log_event(
                        "decompose_used",
                        request_id=request_id,
                        used=decomposition_used,
                        sub_count=len(sub_queries),
                        sub_queries=sub_queries,
                        latency_ms=stage_latency_ms["decompose"],
                        prefilter_skipped=False,
                    )

            if decomposition_used:
                # Discard the already-launched single-question rewrite_future.
                try:
                    rewrite_future.cancel()
                except Exception:
                    pass

                # Sub-queries are already short, focused phrases (e.g. "臨時停車",
                # "使用錯誤燈號") — the LLM rewrite's 4+1 dimension expansion adds
                # ~10s while delivering little marginal recall benefit at this scope.
                # Use the cheap local_query_expand regex pass instead (0ms).
                rewrite_started = time.perf_counter()
                sub_rewrites = [local_query_expand(sq) for sq in sub_queries]
                stage_latency_ms["rewrite"] = round(
                    (time.perf_counter() - rewrite_started) * 1000, 1
                )

                sub_search_results = []
                search_started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=max(2, len(sub_rewrites))) as sub_pool:
                    search_futures = [
                        sub_pool.submit(search_vertex, rq, settings, search_client)
                        for rq in sub_rewrites
                    ]
                    for fut in search_futures:
                        try:
                            resp = fut.result(timeout=SUB_QUERY_SEARCH_TIMEOUT_S)
                            sub_search_results.append(list(getattr(resp, "results", [])))
                        except Exception:
                            sub_search_results.append([])
                stage_latency_ms["search"] = round(
                    (time.perf_counter() - search_started) * 1000, 1
                )

                labeled_sub_results = [
                    [
                        _SubQueryResult(
                            result=r,
                            sub_query=sub_queries[idx],
                            sub_query_index=idx + 1,
                        )
                        for r in results
                    ]
                    for idx, results in enumerate(sub_search_results)
                ]
                merged_results = _interleave_dedup(labeled_sub_results, limit=15)
                search_response = _MergedResponse(merged_results)
                rewritten_query = " ".join(sub_rewrites)

                log_event(
                    "rewrite_completed",
                    request_id=request_id,
                    rewritten_query=rewritten_query,
                    latency_ms=stage_latency_ms["rewrite"],
                    decomposition_used=True,
                    sub_count=len(sub_queries),
                )
                log_event(
                    "search_completed",
                    request_id=request_id,
                    result_count=len(merged_results),
                    latency_ms=stage_latency_ms["search"],
                    cache_hit=False,
                    decomposition_used=True,
                    per_sub_counts=[len(r) for r in sub_search_results],
                )
            else:
                if "rewrite" not in stage_latency_ms:
                    # If the dict already produced a fully-normalized question
                    # (e.g. 機車行駛人行道 → 第45條第1項第6款 駕車行駛人行道 ...),
                    # the heavyweight LLM rewrite tends to inject contradictory
                    # terms (like §45 汽車爭道). Skip it and trust the dict.
                    #
                    # Also skip for 牌照污損 questions: the §13/§14 罰則 live behind a
                    # term-mismatch (道安規則 §11 行為規範 vs 處罰條例 罰則), the LLM
                    # rewrite for them routinely times out (~10s vs the 3s budget), and
                    # the post-timeout dict fallback runs anyway. Expanding here (after
                    # decompose) keeps it a SINGLE search over the full §13+§14 query —
                    # prepending the same terms before decompose splits them apart and
                    # interleave drops §14 (verified 2026-06-11).
                    if (term_norm_source == "dict" and term_norm_appended) or (
                        _PLATE_DEFACEMENT_PATTERN.search(normalized_question)
                    ):
                        try:
                            rewrite_future.cancel()
                        except Exception:
                            pass
                        rewritten_query = local_query_expand(normalized_question)
                        stage_latency_ms["rewrite"] = 0.0
                        log_event(
                            "rewrite_skipped_dict_hit",
                            request_id=request_id,
                            normalized=normalized_question,
                            rewritten_query=rewritten_query,
                        )
                    else:
                        try:
                            rewritten_query, stage_latency_ms["rewrite"] = rewrite_future.result(
                                timeout=REWRITE_TIMEOUT_S
                            )
                        except _FuturesTimeoutError:
                            rewritten_query = local_query_expand(normalized_question)
                            stage_latency_ms["rewrite"] = REWRITE_TIMEOUT_S * 1000
                            log_event(
                                "rewrite_timeout",
                                request_id=request_id,
                                fallback_query=rewritten_query,
                                original=normalized_question,
                            )
                # Method C: post-process rewrite output with term-normalization data
                # (covers the single-facet path where rewrite_future ran on the
                # original question and didn't see the dict/LLM expansions).
                # Order matters:
                #   1. strip anti_terms first (kill polluting words like "小型汽車" before they bias the search)
                #   2. prepend missing law terms so Vertex BM25 weights them highly
                rewritten_query_pre = rewritten_query
                anti_removed: List[str] = []
                for bad in term_norm_anti:
                    if bad in rewritten_query:
                        rewritten_query = rewritten_query.replace(bad, " ").strip()
                        anti_removed.append(bad)
                if term_norm_appended:
                    missing = [w for w in term_norm_appended if w not in rewritten_query]
                    if missing:
                        rewritten_query = " ".join(missing) + " " + rewritten_query
                # Collapse runs of whitespace produced by anti_terms removal
                rewritten_query = " ".join(rewritten_query.split())
                log_event(
                    "rewrite_completed",
                    request_id=request_id,
                    rewritten_query=rewritten_query,
                    latency_ms=round(stage_latency_ms["rewrite"], 1),
                    term_norm_appended=term_norm_appended,
                    term_norm_anti_removed=anti_removed,
                    pre_post_diff=rewritten_query != rewritten_query_pre,
                )

            # --- Search with cache (skip if decomposition already populated search_response) ---
            if not decomposition_used:
                _notify(progress_callback, {"stage": "search", "message": "正在檢索法規條文", "eta_text": "通常少於 1 秒"})
                cached_search = search_cache.get(rewritten_query)
                if cached_search is not None:
                    search_response = cached_search
                    stage_latency_ms["search"] = 0.0
                    log_event(
                        "search_completed",
                        request_id=request_id,
                        result_count=len(search_response.results) if hasattr(search_response, "results") else 0,
                        latency_ms=0.0,
                        cache_hit=True,
                    )
                else:
                    search_response, stage_latency_ms["search"] = measure_ms(
                        search_vertex, rewritten_query, settings, search_client
                    )
                    search_cache.set(rewritten_query, search_response)
                    result_count = len(search_response.results) if hasattr(search_response, "results") else 0
                    log_event(
                        "search_completed",
                        request_id=request_id,
                        result_count=result_count,
                        latency_ms=round(stage_latency_ms["search"], 1),
                        cache_hit=False,
                    )

            # --- Cross-reference expansion (second search if 比照/準用 detected) ---
            # Guarded: only trigger when the ORIGINAL question references a vehicle
            # category that maps to the 比照小型汽車 rule (§92 大型重機). Otherwise
            # an unrelated chunk containing "比照小型汽車" can hijack the search
            # for queries like 「機車行駛人行道」 and crowd out the real §45 hit.
            primary_contents = " ".join(
                extract_result_content(r) for r in getattr(search_response, "results", [])
            )
            question_signals_large_motorcycle = bool(
                re.search(r"大型重機|大重|大型重型機車", question)
            )
            cross_ref_trigger = (
                question_signals_large_motorcycle
                and _CROSS_REF_PATTERN.search(primary_contents)
            )
            cross_ref_query = (
                f"小型汽車 {_extract_behavior_keywords(question)}".strip()
                if cross_ref_trigger else ""
            )
            search_response = _maybe_expand_search(
                search_response=search_response,
                trigger=cross_ref_trigger,
                expansion_query=cross_ref_query,
                event_name="cross_reference_expansion",
                stage_key="search_expansion",
                settings=settings,
                search_client=search_client,
                request_id=request_id,
                stage_latency_ms=stage_latency_ms,
            )

            # --- Motorcycle lane expansion (safety net for §45 recall) ---
            # Plain 機車 (250cc 以下) is a 廣義「汽車」 under §3(8), so its lane
            # violations fall under the 汽車章 §45 (爭道行駛; 款13「機車不在規定
            # 車道行駛」). A bare 機車 query does NOT trigger the 大型重機 cross-ref
            # above, and §45 ranks too low in primary to survive the top-10 window,
            # so the answer layer never sees the penalty. Run a parallel §45 lane
            # search when the question is about a (non-large) motorcycle on a 車道
            # — excludes 人行道 (handled by the dict's §45款6 path).
            is_plain_motorcycle = "機車" in question and not question_signals_large_motorcycle
            moto_lane_trigger = (
                is_plain_motorcycle and "車道" in question and "人行道" not in question
            )
            search_response = _maybe_expand_search(
                search_response=search_response,
                trigger=moto_lane_trigger,
                expansion_query="汽車駕駛人 爭道行駛 機車不在規定車道行駛 第45條",
                event_name="motorcycle_lane_expansion",
                stage_key="search_moto_lane_expansion",
                settings=settings,
                search_client=search_client,
                request_id=request_id,
                stage_latency_ms=stage_latency_ms,
            )

            # --- Light violation expansion (safety net for §42/§48/§73 recall) ---
            light_trigger = bool(_LIGHT_VIOLATION_PATTERN.search(question))
            if light_trigger:
                if "方向燈" in question or "變換車道" in question:
                    light_query = "不依規定使用燈光 變換車道 方向燈 第42條 第48條"
                elif "慢車" in question or "自行車" in question:
                    light_query = "慢車 夜間行車未開啟燈光 第73條"
                else:
                    light_query = "不依規定使用燈光 第42條"
            else:
                light_query = ""
            search_response = _maybe_expand_search(
                search_response=search_response,
                trigger=light_trigger,
                expansion_query=light_query,
                event_name="light_violation_expansion",
                stage_key="search_light_expansion",
                settings=settings,
                search_client=search_client,
                request_id=request_id,
                stage_latency_ms=stage_latency_ms,
            )

            # --- Rerank（1-1）：語意重排，僅 RERANK_ENABLED 時執行 ---
            # 放在所有 expansion merge 之後、citation 編號之前，所有檢索路徑
            # （單一/decomposition/expansion/快取命中）都收斂到這裡。query 用
            # normalized_question（字典展開後含罰則條號，讓 ranker 不把罰則條文
            # 排到行為規範之後；followup 用解析後全文），不用關鍵字堆疊的
            # rewritten_query——後者維度雜訊太多會稀釋語意。
            if getattr(settings, "rerank_enabled", False):
                reranked, stage_latency_ms["rerank"] = measure_ms(
                    rerank_results,
                    resolved_question if followup_context else normalized_question,
                    list(getattr(search_response, "results", [])),
                    settings,
                    request_id=request_id,
                )
                search_response = _MergedResponse(reranked)

            # --- Answer generation with cache ---
            # 組引用來源（編號與 answer prompt 的 [n] 一致）；answer cache 命中時也需要
            answer_sources = build_citation_sources(
                getattr(search_response, "results", []),
                max_sources=settings.answer_max_context_sources if settings else 10,
            )
            _notify(progress_callback, {"stage": "answer", "message": "正在依據法規撰寫回覆", "eta_text": "首字約 5-8 秒內出現"})
            answer_cache_key = (question, rewritten_query, persona_id, conversation_context, bool(followup_context))
            cached_answer = answer_cache.get(answer_cache_key)
            if cached_answer is not None:
                final_answer = cached_answer
                stage_latency_ms["answer"] = 0.0
                log_event(
                    "answer_completed",
                    request_id=request_id,
                    used_insufficient_fallback=("資料不足" in final_answer),
                    answer_chars=len(final_answer),
                    latency_ms=0.0,
                    cache_hit=True,
                )
            else:
                search_results = getattr(search_response, "results", [])
                answer_question = resolved_question if followup_context else question
                if stream_callback:
                    final_answer, stage_latency_ms["answer"] = measure_ms(
                        generate_refined_answer_streaming,
                        user_question=answer_question,
                        search_term=rewritten_query,
                        search_results=search_results,
                        rewriter_model=rewriter_model,
                        answer_model=answer_model,
                        settings=settings,
                        persona=persona,
                        stream_callback=stream_callback,
                        followup_context=followup_context,
                        conversation_context=conversation_context,
                    )
                else:
                    final_answer, stage_latency_ms["answer"] = measure_ms(
                        generate_refined_answer,
                        user_question=answer_question,
                        search_term=rewritten_query,
                        search_results=search_results,
                        rewriter_model=rewriter_model,
                        answer_model=answer_model,
                        settings=settings,
                        persona=persona,
                        followup_context=followup_context,
                        conversation_context=conversation_context,
                    )
                suspect_truncated = (
                    "系統暫時無法穩定生成完整回覆" in final_answer
                    or (len(final_answer) < 300 and "**結論:**" not in final_answer)
                )
                # --- Grounding check（1-3）：僅 GROUNDING_ENABLED 時執行 ---
                # 放在快取寫入之前：低分警示併入答案後才進快取，快取命中
                # 與新生成的呈現一致。檢核失敗回 None、不阻斷主流程。
                if (
                    getattr(settings, "grounding_enabled", False)
                    and answer_sources
                    and not suspect_truncated
                ):
                    grounding_score, stage_latency_ms["grounding"] = measure_ms(
                        check_grounding,
                        final_answer,
                        answer_sources,
                        settings,
                        request_id=request_id,
                    )
                    if (
                        grounding_score is not None
                        and grounding_score < getattr(settings, "grounding_threshold", 0.6)
                    ):
                        final_answer += GROUNDING_WARNING_BLOCK
                if not suspect_truncated:
                    answer_cache.set(answer_cache_key, final_answer)
                    if not skip_fast_cache:
                        answer_cache.set(fast_cache_key, final_answer)
                log_event(
                    "answer_completed",
                    request_id=request_id,
                    used_insufficient_fallback=("資料不足" in final_answer),
                    answer_chars=len(final_answer),
                    suspect_truncated=suspect_truncated,
                    latency_ms=round(stage_latency_ms["answer"], 1),
                    cache_hit=False,
                )
        except Exception as e:
            final_answer = "發生錯誤，請稍後再試。"
            error_msg = str(e)
            log_event(
                "request_failed",
                request_id=request_id,
                error_type=classify_error(e),
                error_message=error_msg,
            )

    log_event(
        "request_completed",
        request_id=request_id,
        total_latency_ms=round((time.perf_counter() - request_started_at) * 1000, 1),
        intent=intent,
        stage_latency_ms={k: round(v, 1) for k, v in stage_latency_ms.items()},
        response_len=len(final_answer),
        persona_id=persona_id,
    )

    return PipelineResult(
        answer=final_answer,
        intent=intent,
        stage_latency_ms=stage_latency_ms,
        request_id=request_id,
        error=error_msg,
        sources=tuple(answer_sources),
        grounding_score=grounding_score,
    )
