import time

from google.api_core.exceptions import ResourceExhausted

from rag_logic import (
    extract_result_content,
    extract_result_data,
    extract_result_title,
    mapping_get,
    parse_related_result_ids,
)
from services.answer_formatter import (
    append_law_attachment_notice,
    bold_law_references,
    has_section_shorthand,
    repair_answer_structure,
)
from services.answer_prompts import build_answer_prompt, build_followup_answer_prompt
from services.followup_menu import append_menu_if_missing
from services.telemetry import log_event

_DEFAULT_MAX_CONTEXT_CHARS = 12000
_DEFAULT_MAX_CONTEXT_SOURCES = 10


def filter_irrelevant_results(results, query, rewriter_model):
    """Use the rewrite model to keep only results related to the query."""
    if not results:
        return []

    candidates = []
    for i, result in enumerate(results):
        data = extract_result_data(result)
        title = extract_result_title(data)
        snippet = extract_result_content(data)
        candidates.append(f"ID:{i} | 標題: {title} | 摘要: {snippet[:200]}...")

    prompt = f"""
    你是法規檢索結果篩選助手。

    使用者問題:
    {query}

    候選結果:
    {chr(10).join(candidates)}

    請只輸出與問題直接相關的候選 ID，以逗號分隔。
    若都不相關，請只輸出 None。
    """

    try:
        response = rewriter_model.generate_text(prompt, temperature=0.0)
        decision = (response.text or "").strip()
        if "None" in decision or not decision:
            return []

        valid_ids = parse_related_result_ids(decision, len(results))
        return [results[i] for i in valid_ids]
    except Exception as exc:
        log_event("search_filter_failed", severity="WARNING", error_message=str(exc))
        return results


def _build_fallbacks(user_question, source_titles):
    """Return (insufficient, from_context) fallback callables."""

    def _join_sources(limit=None):
        names = source_titles if limit is None else source_titles[:limit]
        return ", ".join(sorted(set(names))) if names else "無"

    def _fallback_insufficient():
        sources = _join_sources()
        return (
            "**結論:**\n"
            "目前檢索到的資料不足，無法提供完整且可靠的回答。\n"
            "**法規依據:**\n"
            "現有結果無法支持明確法規判斷。\n"
            "**注意事項:**\n"
            "請補充更具體的情境、對象、程序階段或法規關鍵字後再試。\n"
            f"參考依據：**[{sources}]**"
        )

    def _fallback_from_context():
        sources = _join_sources(limit=5)
        q = user_question or ""
        if any(k in q for k in ["身分證", "個資", "查詢民眾", "個人資料", "非因公"]):
            return (
                "**結論:**\n"
                "個人資料查詢通常需有法定依據與職務必要性，不能任意查詢。\n"
                "**法規依據:**\n"
                "請依你所在機關適用的個資、警政與內部授權規範辦理。\n"
                "**注意事項:**\n"
                "應確認查詢目的、權限來源、留存紀錄與是否屬於執行職務所必需。\n"
                f"參考依據：**[{sources}]**"
            )
        return (
            "**結論:**\n"
            "目前可根據檢索內容提供初步方向，但仍建議回到原始法規或作業規範再確認。\n"
            "**法規依據:**\n"
            "回答來自目前檢索到的片段內容，未必涵蓋完整條文脈絡。\n"
            "**注意事項:**\n"
            "若涉及處分、通報、蒐證或權限判斷，請再核對完整條文與最新版本。\n"
            f"參考依據：**[{sources}]**"
        )

    return _fallback_insufficient, _fallback_from_context


def _emit_stream(stream_callback, text):
    """Deliver a streamed chunk without letting callback failures abort the answer."""
    if stream_callback is None or not text:
        return
    try:
        stream_callback(text)
    except Exception as exc:
        log_event("stream_callback_failed", severity="WARNING", error_message=str(exc))


def _call_generate_text(model, prompt, settings):
    """Call model.generate_text with answer-specific parameters from settings."""
    kwargs = {"temperature": 0.0}
    if settings is not None:
        kwargs["max_output_tokens"] = settings.answer_max_output_tokens
        if settings.answer_thinking_budget and settings.answer_thinking_budget > 0:
            kwargs["thinking_budget"] = settings.answer_thinking_budget
        if settings.answer_thinking_level:
            kwargs["thinking_level"] = settings.answer_thinking_level
    else:
        kwargs["max_output_tokens"] = 32768
    return model.generate_text(prompt, **kwargs)


def generate_refined_answer(
    user_question,
    search_term,
    search_results,
    rewriter_model,
    answer_model,
    settings=None,
    persona=None,
    followup_context=None,
    conversation_context="",
):
    """Prefer a complete answer, but fall back conservatively when needed.

    If followup_context is provided the second-round focused prompt is used
    and bold_law_references() is applied to the returned answer.
    conversation_context (first-round only) lets the answer resolve references
    to earlier turns; law sources still come from this turn's search results.
    """
    context_text, source_titles, context_truncated = _build_context(
        search_results,
        max_chars=settings.answer_max_context_chars if settings else _DEFAULT_MAX_CONTEXT_CHARS,
        max_sources=settings.answer_max_context_sources if settings else _DEFAULT_MAX_CONTEXT_SOURCES,
    )
    log_event(
        "answer_context_built",
        context_chars=len(context_text),
        context_sources_count=len(source_titles),
        context_truncated=context_truncated,
    )

    _fallback_insufficient, _fallback_from_context = _build_fallbacks(
        user_question, source_titles
    )

    if not search_results:
        return _fallback_insufficient()

    if followup_context:
        prompt = build_followup_answer_prompt(user_question, context_text, persona, followup_context)
    else:
        prompt = build_answer_prompt(user_question, context_text, persona, conversation_context)

    for attempt in range(3):
        try:
            response = _call_generate_text(answer_model, prompt, settings)
            answer = response.text
            if answer:
                formatted = append_law_attachment_notice(repair_answer_structure(bold_law_references(answer)))
                if has_section_shorthand(formatted):
                    log_event("section_shorthand_detected", severity="WARNING",
                              preview=formatted[:200])
                if not followup_context:
                    formatted, appended_rule = append_menu_if_missing(formatted, user_question)
                    if appended_rule:
                        log_event("followup_menu_appended", severity="INFO",
                                  rule=appended_rule, question=user_question[:100])
                return formatted
        except ResourceExhausted:
            time.sleep(1 * (2 ** attempt))
        except Exception:
            break

    try:
        response = _call_generate_text(rewriter_model, prompt, settings)
        answer = response.text
        if answer:
            formatted = append_law_attachment_notice(repair_answer_structure(bold_law_references(answer)))
            if has_section_shorthand(formatted):
                log_event("section_shorthand_detected", severity="WARNING",
                          preview=formatted[:200])
            if not followup_context:
                formatted, appended_rule = append_menu_if_missing(formatted, user_question)
                if appended_rule:
                    log_event("followup_menu_appended", severity="INFO",
                              rule=appended_rule, question=user_question[:100])
            return formatted
    except Exception:
        pass

    return _fallback_from_context()


def generate_refined_answer_streaming(
    user_question,
    search_term,
    search_results,
    rewriter_model,
    answer_model,
    settings=None,
    persona=None,
    stream_callback=None,
    followup_context=None,
    conversation_context="",
):
    """Streaming variant: yields answer chunks via stream_callback, returns full answer.

    If followup_context is provided the second-round focused prompt is used.
    bold_law_references() is NOT applied here (streaming chunks cannot be
    post-processed without front-end flicker); the prompt instructs the LLM
    to bold law references itself.
    """
    context_text, source_titles, context_truncated = _build_context(
        search_results,
        max_chars=settings.answer_max_context_chars if settings else _DEFAULT_MAX_CONTEXT_CHARS,
        max_sources=settings.answer_max_context_sources if settings else _DEFAULT_MAX_CONTEXT_SOURCES,
    )
    log_event(
        "answer_context_built",
        context_chars=len(context_text),
        context_sources_count=len(source_titles),
        context_truncated=context_truncated,
    )

    _fallback_insufficient, _fallback_from_context = _build_fallbacks(
        user_question, source_titles
    )

    if not search_results:
        fallback = _fallback_insufficient()
        _emit_stream(stream_callback, fallback)
        return fallback

    if followup_context:
        prompt = build_followup_answer_prompt(user_question, context_text, persona, followup_context)
    else:
        prompt = build_answer_prompt(user_question, context_text, persona, conversation_context)

    stream_kwargs = {"temperature": 0.0}
    if settings is not None:
        stream_kwargs["max_output_tokens"] = settings.answer_max_output_tokens
        if settings.answer_thinking_budget and settings.answer_thinking_budget > 0:
            stream_kwargs["thinking_budget"] = settings.answer_thinking_budget
        if settings.answer_thinking_level:
            stream_kwargs["thinking_level"] = settings.answer_thinking_level
    else:
        stream_kwargs["max_output_tokens"] = 32768

    for attempt in range(3):
        try:
            chunks = []
            last_finish_reason = None
            last_usage = None
            for chunk in answer_model.stream_text(prompt, **stream_kwargs):
                if chunk.text:
                    chunks.append(chunk.text)
                    _emit_stream(stream_callback, chunk.text)
                if chunk.finish_reason:
                    last_finish_reason = chunk.finish_reason
                if chunk.usage and chunk.usage.total is not None:
                    last_usage = chunk.usage
            full_answer = "".join(chunks).strip()
            if full_answer:
                full_answer = append_law_attachment_notice(repair_answer_structure(bold_law_references(full_answer)))
            if full_answer and not followup_context:
                full_answer, appended_rule = append_menu_if_missing(full_answer, user_question)
                if appended_rule:
                    log_event("followup_menu_appended", severity="INFO",
                              rule=appended_rule, question=user_question[:100])
            log_event(
                "answer_stream_done",
                provider=getattr(answer_model, "provider", "unknown"),
                model=getattr(answer_model, "model_name", "unknown"),
                finish_reason=last_finish_reason,
                chars=len(full_answer),
                usage={
                    "prompt": last_usage.prompt if last_usage else None,
                    "output": last_usage.output if last_usage else None,
                    "thoughts": last_usage.thoughts if last_usage else None,
                    "total": last_usage.total if last_usage else None,
                },
                thinking_budget=settings.answer_thinking_budget if settings else None,
                thinking_level=settings.answer_thinking_level if settings else None,
                max_output_tokens=settings.answer_max_output_tokens if settings else 32768,
            )
            if full_answer:
                return full_answer
        except ResourceExhausted:
            time.sleep(1 * (2 ** attempt))
        except Exception as exc:
            log_event("answer_stream_failed", severity="WARNING", error_message=str(exc))
            break

    try:
        response = _call_generate_text(rewriter_model, prompt, settings)
        answer = response.text
        if answer:
            answer = append_law_attachment_notice(repair_answer_structure(bold_law_references(answer)))
            _emit_stream(stream_callback, answer)
            return answer
    except Exception:
        pass

    fallback = _fallback_from_context()
    _emit_stream(stream_callback, fallback)
    return fallback


def _unwrap_result(result):
    return getattr(result, "result", result)


def _result_facet_prefix(result):
    sub_query = getattr(result, "sub_query", None)
    if not sub_query:
        return ""
    sub_query_index = getattr(result, "sub_query_index", None)
    if sub_query_index:
        return f"[子面向 {sub_query_index}：{sub_query}]\n"
    return f"[子面向：{sub_query}]\n"


def build_citation_sources(results, max_sources=_DEFAULT_MAX_CONTEXT_SOURCES):
    """組出引用來源清單，編號與 _build_context 進 prompt 的 [n] 一致。

    回傳 [{"index": 1-based, "title": str, "content": str}, ...]。
    注意：編號邏輯必須與 _build_context 同步（enumerate(results[:max_sources], 1)），
    答案中的 [n] 引用標註才能對回正確的來源原文。
    """
    sources = []
    for idx, result in enumerate(results[:max_sources], start=1):
        raw_result = _unwrap_result(result)
        data = extract_result_data(raw_result)
        sources.append({
            "index": idx,
            "title": extract_result_title(data),
            "content": extract_result_content(data),
        })
    return sources


def _build_context(results, max_chars, max_sources):
    """Build bounded context text to avoid oversized prompts."""
    if not results:
        return "無可用參考資料。", ["無"], False

    context_chunks = []
    source_titles = []
    total_chars = 0
    truncated = False

    for idx, result in enumerate(results[:max_sources], start=1):
        raw_result = _unwrap_result(result)
        data = extract_result_data(raw_result)
        title = extract_result_title(data)
        content = extract_result_content(data)
        facet_prefix = _result_facet_prefix(result)
        chunk = f"{facet_prefix}[{idx}] 資料 [{title}]: {content}\n\n"

        if total_chars + len(chunk) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 0:
                source_titles.append(title)
                context_chunks.append(chunk[:remaining])
            truncated = True
            break

        source_titles.append(title)
        context_chunks.append(chunk)
        total_chars += len(chunk)

    if len(results) > max_sources:
        truncated = True

    if not source_titles:
        return "無可用參考資料。", ["無"], True

    context_text = "".join(context_chunks)
    return context_text, source_titles, truncated
