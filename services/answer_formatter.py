"""Post-processor for answer text.

Pure functions — no I/O, no model calls.
Currently provides bold_law_references() which wraps law article citations
in Markdown bold markers (**...**) as a deterministic safety net.
"""
import re

# Matches patterns like:
#   《道路交通管理處罰條例》第 21 條
#   《道路交通管理處罰條例》第21條第2項
#   《道路交通管理處罰條例》第二十一條第三項第一款
#   《道路交通安全規則》第 3 條之 1
_SECTION_SHORTHAND = re.compile(r"§\s*\d+(?:[-之]\d+)?")

_LAW_PATTERN = re.compile(
    r"(《[^》]+》\s*第\s*[0-9一二三四五六七八九十百千零〇]+"
    r"(?:\s*條之\s*[0-9一二三四五六七八九十]+|\s*條)"
    r"(?:\s*第\s*[0-9一二三四五六七八九十]+\s*項)?"
    r"(?:\s*第\s*[0-9一二三四五六七八九十]+\s*款)?)"
)

_PARTIAL_BOLD_LAW_SUFFIX = re.compile(
    r"\*\*(?P<cite>《[^》]+》\s*第\s*[0-9一二三四五六七八九十百千零〇]+"
    r"(?:\s*條之\s*[0-9一二三四五六七八九十]+|\s*條)"
    r"(?:\s*第\s*[0-9一二三四五六七八九十]+\s*項)?)\*\*"
    r"(?P<suffix>"
    r"\s*第\s*[0-9一二三四五六七八九十]+\s*項\s*第\s*[0-9一二三四五六七八九十]+\s*款"  # 項+款
    r"|\s*第\s*[0-9一二三四五六七八九十]+\s*項"                                       # 僅項
    r"|\s*第\s*[0-9一二三四五六七八九十]+\s*款"                                       # 僅款（保留現有行為）
    r")"
)

_EMPTY_CONCLUSION_THEN_BASIS = re.compile(
    r"(?P<head>\*\*結論[:：]\*\*\s*)"
    r"(?P<basis>\*\*依據[:：]\*\*\s*)"
    r"(?P<body>.*?)(?=\n\s*\*\*(?:注意事項|法規依據)[:：]\*\*)",
    re.DOTALL,
)


def bold_law_references(text: str) -> str:
    """Wrap 《法規》第N條 citations in **...** unless already bolded.

    - Idempotent: skips matches that are already inside ** ... **.
    - Skips matches inside a code span (odd number of backticks before match
      on the same line).
    - Safe to call multiple times on the same text.
    """
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        start = match.start()
        end = match.end()

        # Skip if already wrapped in **
        before = text[max(0, start - 2):start]
        after = text[end:end + 2]
        if before.endswith("**") and after.startswith("**"):
            return match.group(0)

        # Skip if inside a code span (odd backtick count before match on this line)
        line_start = text.rfind("\n", 0, start) + 1
        line_prefix = text[line_start:start]
        if line_prefix.count("`") % 2 == 1:
            return match.group(0)

        return f"**{match.group(0)}**"

    return _PARTIAL_BOLD_LAW_SUFFIX.sub(
        r"**\g<cite>\g<suffix>**",
        _LAW_PATTERN.sub(_replace, text),
    )


def repair_answer_structure(text: str) -> str:
    """Fix narrow, recurring answer-structure slips without changing content.

    The model sometimes emits:
        **結論:**
        **依據:**
        <actual conclusion sentence>
        **注意事項:**

    The frontend treats "依據" as a section header, leaving 結論 empty. When the
    conclusion is blank and this exact pattern appears, move the misplaced
    paragraph back under 結論 and remove the stray 依據 header.
    """
    if not text:
        return text
    text = _PARTIAL_BOLD_LAW_SUFFIX.sub(r"**\g<cite>\g<suffix>**", text)

    def _move_basis(match: re.Match) -> str:
        body = match.group("body").strip()
        if not body:
            return match.group(0)
        return f"{match.group('head')}{body}\n\n"

    repaired = _EMPTY_CONCLUSION_THEN_BASIS.sub(_move_basis, text, count=1)
    return _repair_motorcycle_sidewalk_wording(repaired)


def _repair_motorcycle_sidewalk_wording(text: str) -> str:
    """Prefer precise 適用 wording over misleading 比照 for motorcycle sidewalks."""
    if not all(term in text for term in ("機車", "人行道", "第 3 條", "第 45 條")):
        return text
    replacements = {
        "將比照汽車駕駛人行駛人行道之規定，處以罰鍰": (
            "依**《道路交通管理處罰條例》第 45 條第 1 項第 6 款**，"
            "適用汽車駕駛人行駛人行道之裁罰規定，處以罰鍰"
        ),
        "比照汽車駕駛人行駛人行道之規定，處以罰鍰": (
            "依**《道路交通管理處罰條例》第 45 條第 1 項第 6 款**，"
            "適用汽車駕駛人行駛人行道之裁罰規定，處以罰鍰"
        ),
    }
    repaired = text
    for old, new in replacements.items():
        repaired = repaired.replace(old, new)
    return repaired


_ATTACHMENT_NOTICE = "道路交通安全規則附件請參閱全國法規資料庫"
_ATTACHMENT_LAW_NAME = "道路交通安全規則"
_CONCLUSION_HEADER = re.compile(r"\*\*\s*結論\s*[:：]\s*\*\*")
_SECTION_AFTER_CONCLUSION = re.compile(
    r"\n\s*\*\*\s*(?:注意事項|法規依據|依據)\s*[:：]\s*\*\*"
)


def append_law_attachment_notice(text: str) -> str:
    """Add a notice when the answer cites 道路交通安全規則.

    The notice points readers to 全國法規資料庫 for that law's attachments. When
    the answer has a 結論 section, the 結論 now holds two points (its conclusion
    plus this notice), so both are rendered as bullet items to mirror 注意事項.
    When there is no 結論 section the notice is appended as a plain line after
    法規依據 (end of text). Idempotent: returns text unchanged when the notice is
    already present or the law is not cited.
    """
    if not text or _ATTACHMENT_LAW_NAME not in text:
        return text
    if _ATTACHMENT_NOTICE in text:
        return text

    conclusion = _CONCLUSION_HEADER.search(text)
    if conclusion:
        nxt = _SECTION_AFTER_CONCLUSION.search(text, conclusion.end())
        if nxt:
            body = text[conclusion.end():nxt.start()].strip()
            tail = text[nxt.start():]
            if body and "\n\n" not in body:
                head = text[:conclusion.end()]
                bullet_body = body if body.startswith("- ") else f"- {body}"
                return f"{head}\n{bullet_body}\n- {_ATTACHMENT_NOTICE}{tail}"
            head = text[:nxt.start()].rstrip()
            return f"{head}\n\n- {_ATTACHMENT_NOTICE}{tail}"

    return text.rstrip() + f"\n\n{_ATTACHMENT_NOTICE}"


def has_section_shorthand(text: str) -> bool:
    """Return True if text contains §N abbreviations that should have been expanded."""
    return bool(_SECTION_SHORTHAND.search(text))


# 法條引用的兩種條號結構都要吃：「第 31-1 條」「第 31之1 條」（之/- 在條前）
# 與「第 31 條之 1」（法規正式寫法，之在條後）。單位含「條」（法律）與
# 「點」（行政規則，如道路交通事故處理規範）。
# groups: 1法規名/2基準號/3之N前置/4單位/5之N後置
_LAW_REF_CORE = (
    r"《([^》]+)》\s*第\s*([0-9]+)(?:\s*[-之]\s*([0-9]+))?\s*(條|點)(?:\s*之\s*([0-9]+))?"
)
# 結論/注意事項中的法條引用（bold_law_references 已包粗體），
# 後面可能緊接「規定」——交叉索引 [n] 插在整個片語之後
_UPPER_LAW_MENTION = re.compile(r"\*\*" + _LAW_REF_CORE + r"[^*]*\*\*(?:規定)?")
# 法規依據段的法條標題（同樣是粗體），[n] 插在 ** 之後
_BASIS_LAW_HEADING = re.compile(r"\*\*" + _LAW_REF_CORE + r"[^*]*\*\*")
_BASIS_SPLIT = re.compile(r"法規依據")


def _law_key_from_match(m) -> tuple:
    """(法規名, 單位, 基準號) 正規化鍵——「31之1」「31-1」「31條之1」同鍵；項款不參與。"""
    law = m.group(1).strip()
    base = m.group(2)
    unit = m.group(4)
    sub = m.group(3) or m.group(5)
    return law, unit, f"{base}-{sub}" if sub else base


def link_law_mentions(answer: str) -> tuple:
    """確定性交叉索引：結論/注意事項提及的法條 ↔ 法規依據對應條目，標同號 [n]。

    規則（Paul 2026-07-19 拍板）：
    - 依上段（結論+注意事項）首次提及順序編 [1][2][3]…；同條同號
    - 只有法規依據段存在對應條目（同法規名+同基準條號）才編號，
      兩處都插純文字 [n]（不可點，僅指引讀者到下方看全文）
    - 上段有提及、依據段無對應者不編號，收集回傳供 telemetry 監測
      （可能是定義條款引用/資料覆蓋缺口/幻覺，見計畫文件 2026-07-19 分析）
    回傳 (加註後答案, unmatched 清單 [(法規名, 條號), ...])。
    """
    if not answer:
        return answer, []
    split = _BASIS_SPLIT.search(answer)
    if not split:
        return answer, []
    upper, basis = answer[: split.start()], answer[split.start():]

    basis_keys = {
        _law_key_from_match(m) for m in _BASIS_LAW_HEADING.finditer(basis)
    }

    numbering = {}  # law_key -> n
    unmatched = []
    for m in _UPPER_LAW_MENTION.finditer(upper):
        key = _law_key_from_match(m)
        if key in numbering:
            continue
        if key in basis_keys:
            numbering[key] = len(numbering) + 1
        elif key not in unmatched:
            unmatched.append(key)
    if not numbering:
        return answer, unmatched

    def _annotate(m):
        n = numbering.get(_law_key_from_match(m))
        return f"{m.group(0)} [{n}]" if n else m.group(0)

    upper = _UPPER_LAW_MENTION.sub(_annotate, upper)
    basis = _BASIS_LAW_HEADING.sub(_annotate, basis)
    return upper + basis, unmatched


def ensure_reference_line(text: str, source_titles: list) -> str:
    """Append 參考依據 line if absent and source_titles are available.

    Idempotent: returns text unchanged if 參考依據 already present or no titles.
    """
    if not text or not source_titles:
        return text
    if "參考依據" in text:
        return text
    seen = []
    for t in source_titles:
        if t and t not in seen:
            seen.append(t)
    if not seen:
        return text
    titles_str = "、".join(seen)
    return text.rstrip() + f"\n\n參考依據：**[{titles_str}]**"
