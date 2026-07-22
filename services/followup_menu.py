"""Followup menu: the single deterministic source for clarification menus.

The first-round answer prompt no longer generates clarification menus (the LLM
was non-deterministic and produced illogical options). This module decides — by
rule table — whether a question is missing a key dimension and, if so, appends
the (1)(2)(3)(4) menu. If no dimension is missing, it appends a neutral closing
line instead so the answer doesn't end abruptly.

Rule table design: each FollowupRule maps question keywords → menu to append.
A rule fires only when its keywords match AND the dimension is not already given.
"Already given" is detected two ways: literal substrings (already_specified) and
regex patterns (already_specified_patterns) — the latter catches concrete values
the user supplies in an unanticipated form (e.g. an exact 酒測值 "0.18" that no
literal keyword would match). Rules are evaluated in order; first match wins.
To add a new dimension, append a FollowupRule — no other code changes needed.
"""
import re
from typing import List, NamedTuple, Optional, Tuple


class FollowupRule(NamedTuple):
    name: str                     # rule id (used in telemetry logs)
    keywords: List[str]           # any match in question → rule is a candidate
    already_specified: List[str]  # any literal match in question → user already told us; skip
    menu_title: str               # first line after ---
    menu_options: List[str]       # 3 specific options; "(N) 以上皆想了解" appended automatically
    already_specified_patterns: Tuple[str, ...] = ()  # any regex match → user already told us; skip


# ── Rule table ────────────────────────────────────────────────────────────────
# Order = priority (more specific first, vehicle_type is catch-all last).
# To add a new dimension: insert a FollowupRule at the appropriate position.
# To disable a rule temporarily: comment it out.

FOLLOWUP_RULES: List[FollowupRule] = [
    # 酒駕：濃度區間是關鍵
    FollowupRule(
        name="alcohol_level",
        keywords=["酒駕", "酒測", "酒後", "酒精"],
        already_specified=["0.15", "0.25", "拒測", "拒絕酒測", "mg", "毫克", "每公升"],
        # 任何具體酒測小數值（0.18 / 0.55 / .3 等）即視為已指明濃度，不再追問區間
        already_specified_patterns=(r"\d*\.\d+",),
        menu_title="想提供更精確的答案，請問駕駛人的酒測情形是：",
        menu_options=[
            "吐氣酒精濃度 0.15–0.25 mg/L",
            "吐氣酒精濃度 0.25 mg/L 以上",
            "拒絕酒測",
        ],
    ),
    # 事故：分級（A1/A2/A3）是關鍵
    FollowupRule(
        name="incident_level",
        keywords=["事故", "車禍", "肇事", "撞", "追撞"],
        already_specified=["A1", "A2", "A3", "死亡", "受傷", "車損", "財損"],
        menu_title="想提供更精確的答案，請問本案事故等級是：",
        menu_options=[
            "A1（造成人員死亡）",
            "A2（造成人員受傷）",
            "A3（僅車損或財損）",
        ],
    ),
    # 道路類型：場域影響違規條文
    FollowupRule(
        name="road_type",
        keywords=["違規停車", "臨時停車", "超速", "未禮讓", "路口", "並排"],
        already_specified=["高速公路", "快速公路", "人行道", "學校", "斑馬線", "行人穿越道"],
        menu_title="想提供更精確的答案，請問該地點是：",
        menu_options=[
            "一般道路",
            "高速公路或快速公路",
            "人行道、行人穿越道或學校區域",
        ],
    ),
    # 車輛類型：最廣泛的 catch-all（放最後）
    FollowupRule(
        name="vehicle_type",
        keywords=[
            "燈號", "燈光", "方向燈", "大燈", "警示燈",
            "闖紅燈", "未戴安全帽",
            "駕駛人", "舉發", "處罰", "罰鍰",
        ],
        already_specified=[
            # 車種已指明
            "汽車", "機車", "慢車", "自行車",
            "聯結車", "大型車", "大客車", "大貨車",
            "電動車", "電動自行車",
            # 酒駕/道路類型已指明（避免被這個 catch-all 誤補）
            "酒駕", "酒測", "酒後", "酒精",
            "高速公路", "快速公路", "人行道", "學校", "斑馬線",
        ],
        menu_title="想提供更精確的答案，請問你的情況是：",
        menu_options=[
            "汽車",
            "機車（大型重機／普通重機／輕型機車）",
            "慢車（自行車／電動自行車）",
        ],
    ),
]

# ── Constants ─────────────────────────────────────────────────────────────────
MENU_MARKER = "直接輸入數字即可"
# When no followup menu is appended, the answer would otherwise end abruptly on a
# 條文 quote. Append a neutral closing so it reads as a deliberate ending, not a
# truncation. Must NOT contain MENU_MARKER (would falsely trigger followup-reply
# detection in pipeline._is_followup_reply).
CLOSING_MARKER = "補充提示:"
CLOSING_TEXT = (
    "\n\n---\n**補充提示:** 若涉及特定車種、道路型態或事故情節，"
    "歡迎補充說明以取得更精確的條文。"
)
# When the answer is the "no data" fixed phrase, appending a clarification menu
# is nonsensical (the menu implies we can answer once the user picks an option,
# but we already claimed no data). Suppress the menu in that case.
_NO_DATA_PHRASE = "目前資料庫中沒有直接對應此問題的法規資料"
_VEHICLE_MENU_OPTIONS = ("(1) 汽車", "(2) 機車", "(3) 慢車")
_AUTO_PARKING_CONTEXT_TERMS = ("臨時停車", "臨停", "違規停車", "違停", "第55條", "第 55 條", "第56條", "第 56 條")


# ── Public API ─────────────────────────────────────────────────────────────────
def already_has_menu(answer: str) -> bool:
    return MENU_MARKER in answer


def _append_closing(answer: str) -> str:
    """Append a neutral closing line when no menu is added (idempotent)."""
    if CLOSING_MARKER in answer:
        return answer
    return answer.rstrip() + CLOSING_TEXT


def find_matching_rule(question: str) -> Optional[FollowupRule]:
    """Return first rule whose keywords match and dimension is not already specified."""
    for rule in FOLLOWUP_RULES:
        if not any(kw in question for kw in rule.keywords):
            continue
        if any(spec in question for spec in rule.already_specified):
            continue
        if any(re.search(p, question) for p in rule.already_specified_patterns):
            continue
        return rule
    return None


def build_menu(rule: FollowupRule) -> str:
    lines = ["\n\n---", rule.menu_title]
    for i, opt in enumerate(rule.menu_options, start=1):
        lines.append(f"({i}) {opt}")
    lines.append(f"({len(rule.menu_options) + 1}) 以上皆想了解")
    lines.append("")
    lines.append(MENU_MARKER)
    return "\n".join(lines)


def _is_auto_parking_context(answer: str, question: str) -> bool:
    text = f"{question}\n{answer}"
    return (
        "汽車駕駛人" in answer
        and any(term in text for term in _AUTO_PARKING_CONTEXT_TERMS)
    )


def _has_vehicle_menu(answer: str) -> bool:
    return all(opt in answer for opt in _VEHICLE_MENU_OPTIONS)


def strip_inconsistent_vehicle_menu(answer: str, question: str) -> str:
    """Remove a vehicle menu when the answer already fixed an automotive context."""
    if not already_has_menu(answer):
        return answer
    if not _is_auto_parking_context(answer, question):
        return answer
    if not _has_vehicle_menu(answer):
        return answer
    marker_at = answer.rfind(MENU_MARKER)
    menu_start = answer.rfind("\n---", 0, marker_at)
    if menu_start == -1:
        return answer
    return answer[:menu_start].rstrip()


def append_menu_if_missing(answer: str, question: str) -> Tuple[str, Optional[str]]:
    """Append a clarification menu if the LLM forgot one.

    Returns (final_answer, rule_name_or_None).
    rule_name is non-None only when a menu was appended (for telemetry).
    """
    answer = strip_inconsistent_vehicle_menu(answer, question)
    if already_has_menu(answer):
        return answer, None
    if _NO_DATA_PHRASE in answer:
        return answer, None
    rule = find_matching_rule(question)
    if rule is None:
        return _append_closing(answer), None
    if rule.name == "vehicle_type" and _is_auto_parking_context(answer, question):
        return _append_closing(answer), None
    return answer.rstrip() + build_menu(rule), rule.name
