import pytest

from services.followup_menu import (
    CLOSING_MARKER,
    MENU_MARKER,
    already_has_menu,
    append_menu_if_missing,
    find_matching_rule,
    strip_inconsistent_vehicle_menu,
)


class TestAlreadyHasMenu:
    def test_has_marker(self):
        assert already_has_menu(f"答案...\n{MENU_MARKER}")

    def test_no_marker(self):
        assert not already_has_menu("這是一個沒有選單的答案")

    def test_marker_embedded(self):
        assert already_has_menu(f"前文\n(1) X\n(2) Y\n\n{MENU_MARKER}")


class TestFindMatchingRule:
    @pytest.mark.parametrize("question,expected_rule", [
        # alcohol_level
        ("酒駕該怎麼處理", "alcohol_level"),
        ("酒後駕車如何舉發", "alcohol_level"),
        # alcohol already specified → no rule
        ("酒測 0.25 以上怎麼處罰", None),
        ("拒測該怎麼辦", None),
        # 具體酒測數值（任意小數）即已指明濃度，不應追問區間
        ("攔查汽車駕駛酒測0.18mg", None),
        ("酒測值0.55怎麼罰", None),
        ("駕駛人酒精濃度 0.3 如何舉發", None),
        ("酒測每公升零點一八毫克", None),
        # incident_level
        ("車禍肇事該怎麼處理", "incident_level"),
        ("發生事故現場流程", "incident_level"),
        # incident already specified → no rule
        ("A1 事故現場流程", None),
        ("發生死亡事故怎麼處理", None),
        # road_type
        ("違規停車在哪種場合罰最重", "road_type"),
        ("超速如何舉發", "road_type"),
        # road already specified → no rule
        ("高速公路超速如何舉發", None),
        # vehicle_type
        # 「臨時停車違規使用燈號」→ road_type 先命中（臨時停車 keyword，無道路類型指定）
        ("臨時停車違規使用燈號舉發哪一條", "road_type"),
        ("闖紅燈怎麼處罰", "vehicle_type"),
        ("未戴安全帽如何處理", "vehicle_type"),
        # vehicle already specified → no rule
        ("汽車闖紅燈怎麼處罰", None),
        ("機車未戴安全帽", None),
        # no keyword → no rule
        ("道路定義是什麼", None),
        ("A3 移車原則", None),
        # 慢車道／快車道是「車道」子概念，僅存在於一般道路，不應觸發 road_type 追問
        ("汽車行駛慢車道", None),
        ("行駛慢車道", None),
        ("大型重機行駛快車道", None),
    ])
    def test_routing(self, question, expected_rule):
        rule = find_matching_rule(question)
        assert (rule.name if rule else None) == expected_rule


class TestAppendMenuIfMissing:
    def test_skips_when_menu_present(self):
        ans = f"**結論:** 答案\n---\n(1) X\n\n{MENU_MARKER}"
        out, name = append_menu_if_missing(ans, "酒駕怎處理")
        assert out == ans
        assert name is None

    def test_appends_alcohol_menu(self):
        ans = "**結論:** 酒駕依法裁罰。"
        out, name = append_menu_if_missing(ans, "酒駕該怎麼處理")
        assert name == "alcohol_level"
        assert MENU_MARKER in out
        assert "(1) 吐氣酒精濃度 0.15" in out
        assert "(4) 以上皆想了解" in out

    def test_appends_incident_menu(self):
        ans = "**結論:** 依事故等級分流處理。"
        out, name = append_menu_if_missing(ans, "發生事故現場流程")
        assert name == "incident_level"
        assert "(1) A1" in out

    def test_appends_road_menu_for_parking_lamp(self):
        # 臨時停車違規 → road_type 先命中（比 vehicle_type 更特定）
        ans = "**結論:** 違規停車使用燈號依第 42 條裁罰。"
        out, name = append_menu_if_missing(ans, "臨時停車違規使用燈號舉發哪一條")
        assert name == "road_type"
        assert "(1) 一般道路" in out
        assert MENU_MARKER in out

    def test_appends_vehicle_menu_for_lamp(self):
        ans = "**結論:** 違規使用燈號依法裁罰。"
        out, name = append_menu_if_missing(ans, "路邊停車違規使用燈號舉發哪一條")
        assert name in ("road_type", "vehicle_type")
        assert MENU_MARKER in out

    def test_skips_vehicle_menu_when_answer_fixed_auto_parking_context(self):
        ans = "**結論:** 汽車駕駛人臨時停車不依規定使用燈光，依第55條與第42條裁罰。"
        out, name = append_menu_if_missing(ans, "路邊停車使用錯誤燈號")
        assert name is None
        assert MENU_MARKER not in out

    def test_strips_llm_vehicle_menu_when_answer_fixed_auto_parking_context(self):
        ans = "\n".join([
            "**結論:** 汽車駕駛人臨時停車不依規定使用燈光，依第55條與第42條裁罰。",
            "---",
            "想提供更精確的答案，請問你的情況是：",
            "(1) 汽車",
            "(2) 機車（大型重機／普通重機／輕型機車）",
            "(3) 慢車（自行車／電動自行車）",
            "(4) 以上皆想了解",
            "",
            MENU_MARKER,
        ])
        out = strip_inconsistent_vehicle_menu(ans, "臨時停車使用錯誤燈號")
        assert MENU_MARKER not in out
        assert "(1) 汽車" not in out
        assert "第55條" in out

    def test_preserves_incident_menu_for_accident_question(self):
        ans = "\n".join([
            "**結論:** 後方車追撞涉及事故處理。",
            "---",
            "想提供更精確的答案，請問本案事故等級是：",
            "(1) A1（造成人員死亡）",
            "(2) A2（造成人員受傷）",
            "(3) A3（僅車損或財損）",
            "(4) 以上皆想了解",
            "",
            MENU_MARKER,
        ])
        out = strip_inconsistent_vehicle_menu(ans, "臨時停車使用錯誤燈號後方車追撞")
        assert MENU_MARKER in out
        assert "(1) A1" in out

    def test_no_append_on_no_data_phrase(self):
        # "假沒有" answer must not get a clarification menu slapped on top —
        # the menu implies we can answer once a dimension is picked, which
        # contradicts the no-data claim. (question has 車道 → road_type would match)
        ans = "目前資料庫中沒有直接對應此問題的法規資料，建議查詢主管機關發布的最新規定，或洽詢法制單位。"
        out, name = append_menu_if_missing(ans, "大型重機行駛慢車道")
        assert name is None
        assert MENU_MARKER not in out

    def test_no_append_when_no_rule(self):
        # 無追問規則命中時，不加選單但補一句中性收尾
        ans = "**結論:** 道路定義如下。"
        out, name = append_menu_if_missing(ans, "道路定義是什麼")
        assert name is None
        assert MENU_MARKER not in out
        assert CLOSING_MARKER in out

    def test_no_append_for_slow_lane_question(self):
        # 「汽車行駛慢車道」已隱含一般道路且已指明車種，無實質模糊維度 → 不追問
        ans = "**結論:** 汽車行駛慢車道依第45條第1項第4款裁罰。"
        out, name = append_menu_if_missing(ans, "汽車行駛慢車道")
        assert name is None
        assert MENU_MARKER not in out
        # 收尾提示補上，且不含會誤觸追問偵測的選單標記
        assert CLOSING_MARKER in out
        assert MENU_MARKER not in out

    def test_closing_is_idempotent(self):
        ans = "**結論:** 汽車行駛慢車道依第45條裁罰。"
        once, _ = append_menu_if_missing(ans, "汽車行駛慢車道")
        twice, _ = append_menu_if_missing(once, "汽車行駛慢車道")
        assert once == twice
        assert twice.count(CLOSING_MARKER) == 1

    def test_no_closing_on_no_data_phrase(self):
        # 「資料庫沒有對應」固定話術不應加收尾（語意矛盾）
        ans = "目前資料庫中沒有直接對應此問題的法規資料，建議查詢主管機關發布的最新規定，或洽詢法制單位。"
        out, name = append_menu_if_missing(ans, "汽車行駛慢車道")
        assert name is None
        assert CLOSING_MARKER not in out

    def test_no_closing_when_menu_present(self):
        # 有追問選單時，選單本身即收尾，不再加補充提示
        ans = "**結論:** 酒駕依法裁罰。"
        out, name = append_menu_if_missing(ans, "酒駕該怎麼處理")
        assert name == "alcohol_level"
        assert MENU_MARKER in out
        assert CLOSING_MARKER not in out

    def test_specific_alcohol_value_no_menu_but_closing(self):
        # 已給具體酒測值 → 不追問區間，改補收尾
        ans = "**結論:** 酒測 0.18 達標準，依第35條裁罰。"
        out, name = append_menu_if_missing(ans, "攔查汽車駕駛酒測0.18mg")
        assert name is None
        assert MENU_MARKER not in out
        assert CLOSING_MARKER in out

    def test_no_append_for_motorcycle_sidewalk_specific_question(self):
        ans = "**結論:** 機車行駛人行道依第45條裁罰。"
        out, name = append_menu_if_missing(ans, "機車行駛人行道")
        assert name is None
        assert MENU_MARKER not in out

    def test_menu_appended_after_strip(self):
        ans = "**結論:** 答案。   "
        out, name = append_menu_if_missing(ans, "酒駕怎麼處理")
        assert name == "alcohol_level"
        assert not out.startswith("\n")

    def test_fourth_option_is_all(self):
        ans = "**結論:** 車禍處理。"
        out, _ = append_menu_if_missing(ans, "車禍肇事該怎麼處理")
        assert "(4) 以上皆想了解" in out
