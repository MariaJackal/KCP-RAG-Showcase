from presets import PRESETS, match_preset_question


def test_match_exact_question():
    """逐字相同的問題命中對應 preset。"""
    for preset in PRESETS:
        matched = match_preset_question(preset["question"])
        assert matched is not None
        assert matched["id"] == preset["id"]


def test_match_tolerates_missing_question_mark():
    """尾端沒打問號也命中。"""
    matched = match_preset_question("毒駕標準作業程序為何")
    assert matched is not None
    assert matched["id"] == "drug_driving_sop"


def test_match_tolerates_halfwidth_question_mark_and_whitespace():
    matched = match_preset_question("  道路交通事故定義為何?  ")
    assert matched is not None
    assert matched["id"] == "traffic_accident_def"


def test_different_wording_does_not_match():
    """不同問法不命中，走正常 RAG。"""
    assert match_preset_question("毒駕標準作業程序") is None
    assert match_preset_question("毒駕怎麼處理") is None
    assert match_preset_question("酒駕罰則？") is None


def test_empty_and_digit_do_not_match():
    assert match_preset_question("") is None
    assert match_preset_question("  ") is None
    assert match_preset_question("1") is None


def test_drug_preset_answer_has_no_followup_menu():
    """毒駕 preset 不附追問選單；其他三個都有。"""
    for preset in PRESETS:
        has_menu = "直接輸入數字即可" in preset["answer"]
        if preset["id"] == "drug_driving_sop":
            assert not has_menu
        else:
            assert has_menu
