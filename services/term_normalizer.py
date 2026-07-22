"""LLM 術語對齊兜底（方案 C 的 LLM 層）。

當字典（services/synonym_service.py）0 命中時，呼叫 gemini-2.5-flash
做「口語 → 法規概念詞」的對齊，補上字典涵蓋不到的長尾詞。

安全網：
- 5 秒 timeout
- 失敗 / 解析失敗 / 空輸出 → fallback 為原句，不擋 pipeline
- temperature=0, thinking_budget=0 → 盡量壓低延遲
- regex guard：輸出若含條號片段（第N條/§N）一律剝除，並記 telemetry
"""

import json
import re
from typing import List, Tuple

from services.telemetry import log_event


NORMALIZER_TIMEOUT_S = 5.0

# Guard pattern: match any article-number fragment the LLM might hallucinate
_ARTICLE_PATTERN = re.compile(
    r"(?:第\s*\d+(?:-\d+)?\s*條(?:第\s*\d+\s*[項款])*|§\s*\d+(?:-\d+)?(?:\s*\(\d+\))?|刑法第\s*\d+(?:-\d+)?\s*條)"
)

NORMALIZER_PROMPT = """你是台灣交通法規術語對齊助手。任務：把使用者的口語問題改寫為法規正式用語中的概念詞。

規則：
1. 把任意交通口語對齊到法規會使用的「概念詞／行為描述／主體／地點」，即使該詞未在示範中列出，也要嘗試對齊。
2. **嚴禁輸出任何條號**（例如「第45條」「第92條」「§35」「刑法第185-4條」等），條號由字典與檢索決定，不在本步驟判斷。
3. 不可增加原句沒有的行為面向、不可推測不確定的情節。
4. 若使用者用詞已是法規正式用語，原樣輸出，changes 為空。
5. 若無對應概念詞，原樣輸出，changes 為空，**不可硬塞詞**。

示範（只展示形態，未列出的口語照同樣方式對齊）：
- 「騎樓停車」→ normalized: "騎樓 停車 人行道 駕車行駛人行道", changes: ["騎樓→人行道"]
- 「紅線臨停」→ normalized: "紅線 臨時停車 禁止臨時停車", changes: ["紅線→禁止臨時停車"]
- 「迴轉沒打方向燈」→ normalized: "迴車 變換方向 方向燈 顯示燈光", changes: ["迴轉→迴車", "方向燈→顯示燈光"]
- 「大重機」→ normalized: "大型重型機車 比照小型汽車", changes: ["大重機→大型重型機車"]
- 「肇逃」→ normalized: "肇事逃逸", changes: ["肇逃→肇事逃逸"]
- 「紅單」→ normalized: "舉發違反道路交通管理事件通知單", changes: ["紅單→舉發通知單"]

輸出格式（嚴格 JSON，無 markdown，無說明）：
{"normalized": "<改寫後字串>", "changes": ["<改了哪些詞>"]}

使用者問題：%s
"""


_JSON_OBJECT_PATTERN = re.compile(r"\{.*?\}", re.DOTALL)


def _strip_article_numbers(text: str) -> Tuple[str, bool]:
    """移除 text 中任何條號片段，回傳 (cleaned_text, was_stripped)。"""
    cleaned = _ARTICLE_PATTERN.sub("", text).strip()
    # Collapse multiple spaces left by removal
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned, cleaned != text


def _parse_response(raw_text: str, original: str) -> Tuple[str, List[str]]:
    """從 LLM 輸出抽出 normalized 字串與 changes。失敗時 fallback 為 (原句, [])。"""
    if not raw_text:
        return original, []
    match = _JSON_OBJECT_PATTERN.search(raw_text)
    if not match:
        return original, []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return original, []
    normalized = parsed.get("normalized") if isinstance(parsed, dict) else None
    if not isinstance(normalized, str) or not normalized.strip():
        return original, []
    changes = parsed.get("changes", []) if isinstance(parsed, dict) else []
    if not isinstance(changes, list):
        changes = []
    return normalized.strip(), [str(c) for c in changes]


def normalize_terms(query: str, normalizer_model) -> Tuple[str, List[str]]:
    """呼叫 LLM 對齊術語。回傳 (normalized_query, changes)。

    任何失敗都 fallback 為 (原句, [])，呼叫端可繼續走原流程。
    """
    if not query or not query.strip():
        return query, []

    prompt = NORMALIZER_PROMPT % query
    try:
        response = normalizer_model.generate_text(
            prompt,
            temperature=0.0,
            thinking_budget=0,
        )
        normalized, changes = _parse_response(response.text, query)
    except Exception as exc:
        log_event(
            "term_normalize_failed",
            error=str(exc)[:200],
            original=query,
        )
        return query, []

    # Structural guard: strip any article numbers the LLM injected despite the prompt
    normalized, was_stripped = _strip_article_numbers(normalized)
    if was_stripped:
        log_event(
            "normalizer_stripped_article",
            original=query,
            normalized_before_strip=normalized,
        )
        # If stripping emptied the result, fall back to original
        if not normalized:
            normalized = query
            changes = []

    log_event(
        "term_normalize_completed",
        original=query,
        normalized=normalized,
        changes=changes,
        changed=normalized != query,
    )
    return normalized, changes
