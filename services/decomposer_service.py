"""Query decomposition service (Method H).

Detects compound legal questions and splits them into independent sub-queries.
Each sub-query then runs its own search in parallel, and results are merged
+ deduplicated before answer generation.

Example:
  "臨時停車使用錯誤燈號" → ["臨時停車", "使用錯誤燈號"]
  → 2 parallel searches → §55 (parking) + §42 (lights) both retrieved.
"""

import json
import re
from typing import List

from services.telemetry import log_event


MAX_SUB_QUERIES = 3
DECOMPOSER_TIMEOUT_S = 5.0


# --- Regex prefilter (Method A): skip LLM decomposition for obviously single-facet queries ---
# Design rule: 寧可放過、不要誤殺 — only short-circuit when the question is *clearly* single-facet.
# If in doubt, fall through to LLM decomposition.

_SHORT_QUERY_MAX_LEN = 12  # ≤12 chars + no compound signal → treat as single facet
# Rationale: real compound questions need at least 2 distinct legal actions, which
# typically requires either a connective particle or 2+ action verbs. Below 12
# chars without either, the question almost certainly maps to one law / one facet.

# Patterns that signal a pure definition / explanation question (one concept asked about)
_DEFINITION_PATTERN = re.compile(
    r"(定義|是什麼|什麼是|為何|為什麼|意思|有哪些|有什麼|如何認定|怎麼認定)"
)

# Connective particles that strongly suggest compound queries → DO NOT prefilter
_CONNECTIVE_PATTERN = re.compile(
    r"(跟|和|及|並|又|以及|加上|還有|同時|另外|且|\+|、)"
)

# Action / behavior verbs whose co-occurrence (≥2) implies multiple legal facets
# Used as a *negative* signal: if 2+ of these appear, the query likely has multiple facets.
_ACTION_VERBS = [
    "停車", "行駛", "超速", "酒駕", "闖紅燈", "肇逃", "肇事", "違停",
    "未禮讓", "未保持", "未開", "未戴", "蛇行", "逆向", "迴轉",
    "燈號", "燈光", "信號", "鳴笛", "鳴喇叭", "變換車道",
    "拒測", "酒測", "毒駕", "無照", "吊扣", "吊銷",
]
_ACTION_VERB_PATTERN = re.compile("|".join(re.escape(v) for v in _ACTION_VERBS))


def is_obviously_single_facet(question: str) -> bool:
    """Cheap regex check: return True only when the question is *clearly* single-facet.

    Designed to bias toward False (走 LLM decomposition) when uncertain.
    A True return means we are confident the question maps to one law / one facet
    and can skip the decomposer LLM call entirely.
    """
    if not question:
        return True
    q = question.strip()
    if not q:
        return True

    # Connective particles → likely compound, do NOT prefilter
    if _CONNECTIVE_PATTERN.search(q):
        return False

    # 2+ action verbs → likely compound (e.g. "臨時停車使用錯誤燈號" → 停車 + 燈號)
    action_hits = _ACTION_VERB_PATTERN.findall(q)
    if len(set(action_hits)) >= 2:
        return False

    # Definition-style questions are single facet
    if _DEFINITION_PATTERN.search(q):
        return True

    # Very short queries with at most one action verb are single facet
    if len(q) <= _SHORT_QUERY_MAX_LEN:
        return True

    return False


DECOMPOSER_PROMPT = """你是 RAG 法規查詢分析器。判斷使用者的問題是否包含多個**獨立的法規面向**（每個面向各自對應不同條文）。

判斷規則：
- 單一面向（同一條文 / 同一章節即可涵蓋）→ 輸出 ["原句"]
- 多個獨立面向（例如「臨時停車」+「使用錯誤燈號」分別對應 §55 與 §42）→ 拆成多個子查詢，最多 {max_sub} 個
- 子查詢必須是可獨立檢索的短語，不可丟失關鍵詞
- **保留原詞（重要）**：子查詢必須沿用原句的關鍵詞彙，**嚴禁改寫成同義詞或更正式的說法**。例如「超速」不可改成「違反速限規定」、「滑手機」不可改成「使用行動裝置」、「沒繫安全帶」不可改成「未依規定使用安全帶」。改寫會讓檢索命中率下降。
- 不可加入任何原句沒提到的概念

範例：
- 「機車行駛人行道」→ ["機車行駛人行道"]（單一面向）
- 「臨時停車使用錯誤燈號」→ ["臨時停車", "使用錯誤燈號"]（兩面向）
- 「酒駕肇事逃逸」→ ["酒駕", "肇事逃逸"]（兩面向）
- 「闖紅燈超速併排停車」→ ["闖紅燈", "超速", "併排停車"]（三面向）
- 「超速被攔又沒繫安全帶」→ ["超速", "沒繫安全帶"]（兩面向；**保留「超速」原詞，不可改成「違反速限規定」**）

輸出格式（嚴格 JSON 陣列，無 markdown，無說明）：
["子查詢1", "子查詢2"]

使用者問題：{question}
""".replace("{max_sub}", str(MAX_SUB_QUERIES))


_JSON_ARRAY_PATTERN = re.compile(r"\[.*?\]", re.DOTALL)


def _parse_sub_queries(raw_text: str, original: str) -> List[str]:
    """Extract sub-query list from model output. Fallback to [original] on any failure."""
    if not raw_text:
        return [original]
    match = _JSON_ARRAY_PATTERN.search(raw_text)
    if not match:
        return [original]
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return [original]
    if not isinstance(parsed, list) or not parsed:
        return [original]
    cleaned = [str(item).strip() for item in parsed if str(item).strip()]
    if not cleaned:
        return [original]
    return cleaned[:MAX_SUB_QUERIES]


def decompose_query(question: str, decomposer_model) -> List[str]:
    """Decompose a compound question into independent sub-queries.

    Returns a list of 1-N strings. Always returns [question] on any failure
    so the caller can stay on the single-search code path unchanged.
    """
    if not question or not question.strip():
        return [question]

    prompt = DECOMPOSER_PROMPT.format(question=question)
    try:
        response = decomposer_model.generate_text(
            prompt,
            temperature=0.0,
            thinking_budget=0,
        )
        sub_queries = _parse_sub_queries(response.text, question)
    except Exception as exc:
        log_event(
            "decompose_failed",
            error=str(exc)[:200],
            original=question,
        )
        return [question]

    log_event(
        "decompose_completed",
        original=question,
        sub_count=len(sub_queries),
        sub_queries=sub_queries,
    )
    return sub_queries
