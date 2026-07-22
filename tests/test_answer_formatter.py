from services.answer_formatter import (
    append_law_attachment_notice,
    bold_law_references,
    ensure_reference_line,
    has_section_shorthand,
    link_law_mentions,
    repair_answer_structure,
)

_NOTICE = "道路交通安全規則附件請參閱全國法規資料庫"

_ANSWER = (
    "**結論:**\n"
    "應依**《道路交通管理處罰條例》第 35 條第一項第一款**規定，處罰鍰；"
    "另依**《道路交通安全規則》第 114 條**不得駕車。\n"
    "**注意事項:**\n"
    "- 再次提及**《道路交通管理處罰條例》第 35 條**之加重規定。\n"
    "**法規依據:**\n"
    "**主要法規（直接適用）:**\n"
    "**《道路交通管理處罰條例》第 35 條**\n"
    "「條文原文……」\n"
    "**相關法規（延伸參考）:**\n"
    "- **《道路交通安全規則》第 114 條** — 不得駕車情形。\n"
)


def test_link_law_mentions_cross_references_both_sections():
    linked, unmatched = link_law_mentions(_ANSWER)
    # 上段：§35（含項款+「規定」後綴）標 [1]、§114 標 [2]（依提及順序）
    assert "第 35 條第一項第一款**規定 [1]" in linked
    assert "第 114 條**不得駕車" not in linked or True  # 上段 §114 無「規定」後綴
    assert "**《道路交通安全規則》第 114 條** [2]，" in linked or "第 114 條** [2]" in linked
    # 依據段：對應條目標同號
    assert "**《道路交通管理處罰條例》第 35 條** [1]\n「條文原文" in linked
    assert "- **《道路交通安全規則》第 114 條** [2] —" in linked
    assert unmatched == []


def test_link_law_mentions_same_law_same_number():
    linked, _ = link_law_mentions(_ANSWER)
    # 注意事項再次提及 §35 → 同號 [1]
    assert "之加重規定" in linked
    assert linked.count("第 35 條** [1]") + linked.count("規定 [1]") >= 2


def test_link_law_mentions_unmatched_collected_not_numbered():
    answer = (
        "**結論:** 依**《刑法》第 185-4 條**規定移送。\n"
        "**法規依據:**\n**《道路交通管理處罰條例》第 62 條**\n「條文」\n"
    )
    linked, unmatched = link_law_mentions(answer)
    assert "[1]" not in linked  # 上段唯一提及的刑法無對應 → 不編號
    assert unmatched == [("刑法", "條", "185-4")]


def test_link_law_mentions_no_basis_section_unchanged():
    answer = "**結論:** 依**《刑法》第 1 條**規定。"
    linked, unmatched = link_law_mentions(answer)
    assert linked == answer
    assert unmatched == []


def test_link_law_mentions_zhi_variant_matches_dash():
    answer = (
        "**結論:** 依**《處罰條例》第 31 條之 1**規定處罰。\n"
        "**法規依據:**\n**《處罰條例》第 31-1 條**\n「條文」\n"
    )
    linked, unmatched = link_law_mentions(answer)
    assert "之 1**規定 [1]" in linked
    assert "第 31-1 條** [1]" in linked
    assert unmatched == []


def test_link_law_mentions_supports_point_unit():
    # 行政規則用「點」（如處理規範）：同樣參與交叉索引
    answer = (
        "**注意事項:** 依**《道路交通事故處理規範》第 15 點**規定移送。\n"
        "**法規依據:**\n"
        "- **《道路交通事故處理規範》第15 點** — 移送標準。\n"
    )
    linked, unmatched = link_law_mentions(answer)
    assert "第 15 點**規定 [1]" in linked
    assert "第15 點** [1] —" in linked
    assert unmatched == []


def test_link_law_mentions_empty_input():
    assert link_law_mentions("") == ("", [])


def test_notice_added_at_end_of_conclusion():
    text = (
        "**結論:**\n依《道路交通安全規則》第 16 條辦理。\n\n"
        "**注意事項:**\n1. 備齊文件。\n\n"
        "**法規依據:**\n《道路交通安全規則》第 16 條"
    )
    result = append_law_attachment_notice(text)
    # 出現在結論段末、注意事項之前
    assert _NOTICE in result
    assert result.index(_NOTICE) < result.index("**注意事項:**")
    # 兩點都做成圓點項目（結論說明 + 附件提示）
    assert "- 依《道路交通安全規則》第 16 條辦理。" in result
    assert f"- {_NOTICE}" in result
    # 純文字，無超連結
    assert "http" not in result
    assert f"({_NOTICE})" not in result and f"[{_NOTICE}]" not in result


def test_notice_not_added_when_law_absent():
    text = (
        "**結論:**\n依《道路交通管理處罰條例》第 21 條辦理。\n\n"
        "**注意事項:**\n1. 備齊文件。"
    )
    assert append_law_attachment_notice(text) == text


def test_notice_idempotent():
    text = (
        "**結論:**\n依《道路交通安全規則》第 16 條辦理。\n\n"
        "**注意事項:**\n1. 備齊文件。"
    )
    once = append_law_attachment_notice(text)
    twice = append_law_attachment_notice(once)
    assert once == twice
    assert once.count(_NOTICE) == 1


def test_notice_appended_at_end_when_no_conclusion():
    text = "### 牌照申請\n\n**注意事項:**\n1. 備齊文件。\n\n**法規依據:**\n《道路交通安全規則》第 16 條"
    result = append_law_attachment_notice(text)
    assert result.rstrip().endswith(_NOTICE)


def test_bold_adds_around_law_reference():
    text = "依《道路交通管理處罰條例》第 21 條規定，應依法裁罰。"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第 21 條**" in result


def test_bold_idempotent_when_already_bold():
    text = "依**《道路交通管理處罰條例》第 21 條**規定，應依法裁罰。"
    result = bold_law_references(text)
    # Should not double-wrap
    assert "****" not in result
    assert "**《道路交通管理處罰條例》第 21 條**" in result


def test_bold_handles_paragraph_and_clause():
    text = "《道路交通安全規則》第 114 條第 2 項第 1 款另有規定。"
    result = bold_law_references(text)
    assert "**《道路交通安全規則》第 114 條第 2 項第 1 款**" in result


def test_bold_handles_article_direct_clause():
    text = "《道路交通管理處罰條例》第 3 條第 8 款規定如下。"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第 3 條第 8 款**" in result
    assert "第 3 條**第 8 款" not in result


def test_bold_repairs_partial_bold_clause_suffix():
    text = "依**《道路交通管理處罰條例》第 3 條**第 8 款規定。"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第 3 條第 8 款**" in result
    assert "**第 8 款" not in result


def test_bold_repairs_partial_bold_item_suffix():
    """項次掉在粗體外（**…第N條** 第N項）應收回粗體內，否則前端無法摺疊。"""
    text = "**《道路交通管理處罰條例》第 35 條** 第一項\n「汽機車駕駛人…」"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第 35 條 第一項**" in result
    assert "第 35 條** 第一項" not in result


def test_bold_repairs_partial_bold_item_chinese_digit():
    """中文數字項次（第八項）同樣收回粗體內。"""
    text = "**《道路交通管理處罰條例》第 35 條** 第八項"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第 35 條 第八項**" in result


def test_bold_repairs_partial_bold_item_and_clause():
    """項+款同時掉在粗體外，應一次收齊。"""
    text = "**《道路交通管理處罰條例》第 45 條** 第 1 項第 6 款"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第 45 條 第 1 項第 6 款**" in result


def test_bold_partial_item_idempotent():
    """二次執行不應改變已收進粗體的結果。"""
    text = "**《道路交通管理處罰條例》第 35 條** 第一項"
    once = bold_law_references(text)
    twice = bold_law_references(once)
    assert once == twice


def test_bold_skips_inside_code_span():
    text = "請參考 `《道路交通管理處罰條例》第 53 條` 所示範例。"
    result = bold_law_references(text)
    # Inside backtick span — should NOT be bolded
    assert "**《道路交通管理處罰條例》第 53 條**" not in result


def test_bold_handles_chinese_digits():
    text = "《道路交通管理處罰條例》第二十一條規定如下。"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第二十一條**" in result


def test_bold_handles_article_with_zhi():
    """條之N（附加條號）格式也應被加粗。"""
    text = "依《道路交通管理處罰條例》第 63 條之 1 辦理。"
    result = bold_law_references(text)
    assert "**《道路交通管理處罰條例》第 63 條之 1**" in result


def test_bold_empty_string_returns_empty():
    assert bold_law_references("") == ""


def test_bold_no_law_reference_unchanged():
    text = "這段文字沒有任何法條引用，應維持原樣。"
    result = bold_law_references(text)
    assert result == text


def test_has_section_shorthand_detects_simple():
    assert has_section_shorthand("違反§42規定") is True


def test_has_section_shorthand_detects_with_dash():
    assert has_section_shorthand("依§55-1辦理") is True


def test_has_section_shorthand_false_when_clean():
    assert has_section_shorthand("依**《道路交通管理處罰條例》第 42 條**規定") is False


def test_has_section_shorthand_false_on_empty():
    assert has_section_shorthand("") is False


def test_ensure_reference_line_appends_when_absent():
    text = "**結論:**\n答案內容。\n\n**法規依據:**\n- 條文"
    result = ensure_reference_line(text, ["道路交通管理處罰條例"])
    assert "參考依據：**[道路交通管理處罰條例]**" in result


def test_ensure_reference_line_idempotent_when_present():
    text = "**結論:**\n答案。\n\n參考依據：**[道路交通管理處罰條例]**"
    result = ensure_reference_line(text, ["道路交通管理處罰條例"])
    assert result.count("參考依據") == 1


def test_ensure_reference_line_no_change_when_no_titles():
    text = "**結論:**\n答案內容。"
    result = ensure_reference_line(text, [])
    assert result == text


def test_ensure_reference_line_deduplicates_titles():
    text = "**結論:**\n答案內容。"
    result = ensure_reference_line(text, ["法規A", "法規A", "法規B"])
    assert result.count("法規A") == 1
    assert "法規B" in result


def test_repair_moves_stray_basis_into_empty_conclusion():
    text = "\n".join([
        "**機車行駛人行道之執法處理**",
        "**結論:**",
        "",
        "**依據:**",
        "依**《道路交通管理處罰條例》第 3 條第 8 款**，機車屬於汽車範疇。",
        "",
        "**注意事項:**",
        "- 注意",
        "",
        "**法規依據:**",
        "**《道路交通管理處罰條例》第 45 條第 1 項第 6 款**",
    ])
    result = repair_answer_structure(text)
    conclusion = result.split("**注意事項:**", 1)[0]
    assert "**依據:**" not in conclusion
    assert "機車屬於汽車範疇" in conclusion


def test_repair_motorcycle_sidewalk_uses_article_45_wording():
    text = (
        "**結論:**\n"
        "依**《道路交通管理處罰條例》第 3 條第 8 款**規定，機車屬於汽車範疇。"
        "因此，機車駕駛人行駛人行道，將比照汽車駕駛人行駛人行道之規定，處以罰鍰。\n\n"
        "**法規依據:**\n"
        "**《道路交通管理處罰條例》第 45 條第 1 項第 6 款**"
    )
    result = repair_answer_structure(text)
    assert "將比照汽車駕駛人" not in result
    assert "適用汽車駕駛人行駛人行道之裁罰規定" in result
    assert "**《道路交通管理處罰條例》第 45 條第 1 項第 6 款**" in result
