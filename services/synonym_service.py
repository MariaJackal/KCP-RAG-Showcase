"""同義詞字典硬性替換（方案 C 的字典層）。

讀取 data/legal_synonyms.json，在 rewrite 之前先做字串追加，
把使用者口語詞補上對應的法規正式用語。

設計原則：
- append（追加）而非 replace（替換）：保留使用者原句，避免改錯語意
- context_terms 條件比對：避免對「機車」這類高頻詞無條件追加 §74-1
- 命中 ≥1 規則就返回 hits 列表，pipeline 據此決定是否再呼叫 LLM normalizer
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple


SYNONYM_PATH = Path(__file__).parent.parent / "data" / "legal_synonyms.json"


@lru_cache(maxsize=1)
def _load_rules() -> list:
    """讀取字典 JSON；快取以免每次查詢都讀檔。"""
    if not SYNONYM_PATH.exists():
        return []
    try:
        with SYNONYM_PATH.open(encoding="utf-8") as f:
            return json.load(f).get("rules", [])
    except (json.JSONDecodeError, OSError):
        return []


def _rule_matches(query: str, rule: dict) -> bool:
    """判斷 rule 是否命中：必須命中至少一個 user_term，且若有 context_terms 則必須也命中其中之一；
    若有 exclude_terms 且命中其中之一，則不觸發（用於排除非違規情境，如考照、資格）。"""
    user_terms = rule.get("user_terms", [])
    hit_user = any(term in query for term in user_terms)
    if not hit_user:
        return False
    exclude_terms = rule.get("exclude_terms")
    if exclude_terms and any(ex in query for ex in exclude_terms):
        return False
    context_terms = rule.get("context_terms")
    if context_terms:
        return any(ctx in query for ctx in context_terms)
    return True


def expand_terms(query: str) -> Tuple[str, List[str]]:
    """字典硬性追加。回傳 (擴展後查詢, 命中的規則 category 清單)。

    命中規則時：把該規則的 legal_terms 追加（或前置）到 query；去重。
    無命中時：回傳 (原查詢, [])，呼叫端可決定是否再走 LLM normalizer。
    """
    expanded, hits, _, _ = _expand_with_details(query)
    return expanded, hits


def expand_terms_detailed(query: str) -> Tuple[str, List[str], List[str], List[str]]:
    """同 expand_terms，但多回傳 (追加詞清單, 反向詞清單)。

    - appended：實際補進 query 的法規詞（pipeline 用來補到 rewrite 結果上）
    - anti_terms：依命中規則收集到的「不該出現在 query 的詞」（pipeline 用來剔除 rewrite 結果裡的污染詞）
    """
    return _expand_with_details(query)


def _expand_with_details(query: str) -> Tuple[str, List[str], List[str], List[str]]:
    if not query or not query.strip():
        return query, [], [], []

    hits: List[str] = []
    prepended: List[str] = []
    appended: List[str] = []
    anti: List[str] = []
    for rule in _load_rules():
        if not _rule_matches(query, rule):
            continue
        hits.append(rule.get("category", "unknown"))
        target = prepended if rule.get("prepend") else appended
        for legal in rule.get("legal_terms", []):
            if legal in query or legal in prepended or legal in appended:
                continue
            target.append(legal)
        for bad in rule.get("anti_terms", []):
            if bad not in anti:
                anti.append(bad)

    if not prepended and not appended:
        return query, hits, [], anti

    parts = []
    if prepended:
        parts.append(" ".join(prepended))
    parts.append(query)
    if appended:
        parts.append(" ".join(appended))
    expanded = " ".join(parts)
    return expanded, hits, prepended + appended, anti


def clear_cache():
    """測試用：清掉字典 cache，下次 expand_terms 會重新讀檔。"""
    _load_rules.cache_clear()
