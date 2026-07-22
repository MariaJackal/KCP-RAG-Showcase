"""Golden set 評估核心：純函式指標計算，無 I/O、無 GCP 依賴。

供 run_golden_eval.py（執行器）與 tests/test_golden_eval.py（單元測試）共用。

指標定義（3-1 計畫）：
- 法條命中率 law_hit_rate：expected_laws 中「法規名(或別名)出現在答案」且
  「第N條 條號樣式出現在答案」的比例。
- 關鍵字涵蓋率 keyword_coverage：expected_keywords 出現在答案的比例。
- context_recall：預留欄位，PipelineResult 尚未帶出檢索內容，一律 None
  （待 1-2 引用功能讓 pipeline 暴露 sources 後補上）。

已知限制：
- 法規名比對為子字串比對，需靠 law_aliases 涵蓋常見簡稱。
- 條號支援阿拉伯數字（第35條/第 35 條/第３５條/第35-1條/第35條之1）
  與中文數字（第三十五條/第一百八十五條之三），1~999 條。
"""

import json
import re
from pathlib import Path

# 全形數字 → 半形
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_text(text):
    """移除所有空白、全形數字轉半形，讓「第 35 條」與「第35條」一致。"""
    if not text:
        return ""
    return re.sub(r"\s+", "", text.translate(_FW_DIGITS))


_CN_DIGITS = "零一二三四五六七八九"


def _cn_numeral_variants(n):
    """1~999 → 中文數字寫法清單（法規引文樣式，如 185→一百八十五、110→一百一十/一百十）。"""
    if n < 10:
        return [_CN_DIGITS[n]]
    if n < 20:
        return ["十" + (_CN_DIGITS[n % 10] if n % 10 else "")]
    if n < 100:
        tens, ones = divmod(n, 10)
        return [_CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")]
    hundreds, rem = divmod(n, 100)
    head = _CN_DIGITS[hundreds] + "百"
    if rem == 0:
        return [head]
    if rem < 10:
        return [head + "零" + _CN_DIGITS[rem]]
    tens, ones = divmod(rem, 10)
    tail = _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    variants = [head + tail]
    if tens == 1:  # 一百一十五 / 一百十五 兩種慣用寫法
        variants.append(head + "十" + (_CN_DIGITS[ones] if ones else ""))
    return variants


def article_regex(article):
    """條號字串 → 比對答案用的 regex。

    "35"     → 第35條／第三十五條，且排除誤中「第35條之1」「第35-1條」
    "35-1" / "35之1" → 「第35條之1」「第35-1條」「第三十五條之一」
                       （答案標題常用阿拉伯數字，法規引文常用中文數字），
                       且排除「第35條之11」「第35-11條」
    """
    article = str(article).strip().translate(_FW_DIGITS)
    m = re.fullmatch(r"(\d+)(?:[-之](\d+))?", article)
    if not m:
        raise ValueError(f"無法解析條號: {article!r}")
    base, sub = m.group(1), m.group(2)
    cn_bases = [re.escape(v) for v in _cn_numeral_variants(int(base))]
    if sub:
        cn_subs = [re.escape(v) for v in _cn_numeral_variants(int(sub))]
        pats = [rf"第{base}條之{sub}(?!\d)", rf"第{base}-{sub}條"]
        pats += [rf"第{cb}條之{cs}" for cb in cn_bases for cs in cn_subs]
        return re.compile("(?:" + "|".join(pats) + ")")
    pats = [rf"第{base}條(?!之)"] + [rf"第{cb}條(?!之)" for cb in cn_bases]
    return re.compile("(?:" + "|".join(pats) + ")")



def law_hit(answer_norm, expected_law):
    """單一 expected_law 是否命中（法規名或別名出現 + 條號出現）。

    expected_law: {"law": str, "article": str, "law_aliases": [str, ...]?,
                   "alt_laws": [expected_law, ...]?}
    alt_laws 為「等價法源」：主法源或任一 alt 命中即算命中（例如條例 §65
    與細則 §67 內容逐字等價，答案引其一皆屬正確）。
    answer_norm: 已 normalize_text 過的答案。
    """
    names = [expected_law["law"]] + list(expected_law.get("law_aliases", []))
    name_found = any(normalize_text(n) in answer_norm for n in names)
    article_found = bool(article_regex(expected_law["article"]).search(answer_norm))
    if name_found and article_found:
        return True
    return any(law_hit(answer_norm, alt) for alt in expected_law.get("alt_laws", []))


def evaluate_answer(answer, case, sources=None):
    """對單題計算指標。回傳 dict（不含延遲等執行面資訊，由 runner 補）。

    sources: PipelineResult.sources（[{"index","title","content"}]）。
    提供時計算 context_recall（expected_laws 出現於檢索來源的比例）；
    None 或空時 context_recall 為 None（preset/BLOCK 路徑無來源）。
    """
    answer_norm = normalize_text(answer)

    expected_laws = case.get("expected_laws", [])
    law_results = [
        {
            "law": el["law"],
            "article": el["article"],
            "hit": law_hit(answer_norm, el),
        }
        for el in expected_laws
    ]
    laws_hit = sum(1 for r in law_results if r["hit"])

    keywords = case.get("expected_keywords", [])
    kw_results = [
        {"keyword": kw, "hit": normalize_text(kw) in answer_norm} for kw in keywords
    ]
    kws_hit = sum(1 for r in kw_results if r["hit"])

    context_recall = None
    if sources and expected_laws:
        sources_norm = normalize_text(
            " ".join(f"{s.get('title', '')} {s.get('content', '')}" for s in sources)
        )
        recalled = sum(1 for el in expected_laws if law_hit(sources_norm, el))
        context_recall = recalled / len(expected_laws)

    return {
        "id": case.get("id", "unknown"),
        "category": case.get("category", ""),
        "law_hit_rate": (laws_hit / len(expected_laws)) if expected_laws else None,
        "laws": law_results,
        "keyword_coverage": (kws_hit / len(keywords)) if keywords else None,
        "keywords": kw_results,
        "context_recall": context_recall,
    }


def aggregate(per_question):
    """彙總所有題目的指標。None（該題無此指標）不計入分母。"""
    def _mean(key):
        vals = [q[key] for q in per_question if q.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    full_law_hit = [
        q for q in per_question if q.get("law_hit_rate") is not None and q["law_hit_rate"] >= 1.0
    ]
    law_scored = [q for q in per_question if q.get("law_hit_rate") is not None]
    return {
        "total_questions": len(per_question),
        "avg_law_hit_rate": _mean("law_hit_rate"),
        "questions_all_laws_hit": len(full_law_hit),
        "questions_with_expected_laws": len(law_scored),
        "avg_keyword_coverage": _mean("keyword_coverage"),
        "avg_context_recall": _mean("context_recall"),
    }


def load_golden_set(path):
    """讀取 JSONL golden set，逐行解析並驗證必要欄位。"""
    cases = []
    seen_ids = set()
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        case = json.loads(line)
        for field in ("id", "question", "category"):
            if not case.get(field):
                raise ValueError(f"golden set 第 {lineno} 行缺少欄位 {field!r}")
        if case["id"] in seen_ids:
            raise ValueError(f"golden set 第 {lineno} 行 id 重複: {case['id']!r}")
        seen_ids.add(case["id"])
        for el in case.get("expected_laws", []):
            article_regex(el["article"])  # 提前驗證條號格式
            for alt in el.get("alt_laws", []):
                article_regex(alt["article"])
        cases.append(case)
    return cases
