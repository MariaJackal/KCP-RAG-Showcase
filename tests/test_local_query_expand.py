import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.local_query_expand import local_query_expand


def test_no_match_returns_original():
    q = "這個問題沒有關鍵字"
    assert local_query_expand(q) == q


def test_alcohol_keywords():
    result = local_query_expand("酒駕怎麼處理")
    assert "酒精濃度檢測" in result
    assert "第35條" in result


def test_alcohol_test_keyword():
    result = local_query_expand("路邊酒測被攔下")
    assert "酒精濃度檢測" in result


def test_hit_and_run():
    result = local_query_expand("肇逃的罰則")
    assert "肇事逃逸" in result
    assert "第62條" in result


def test_illegal_parking():
    result = local_query_expand("違停怎麼開罰")
    assert "第55條" in result
    assert "第56條" in result


def test_illegal_parking_full_term():
    result = local_query_expand("違規停車紅線")
    assert "第55條" in result


def test_temporary_parking_keyword():
    result = local_query_expand("臨時停車使用錯誤燈號")
    assert "汽車駕駛人臨時停車" in result
    assert "第55條" in result
    assert "不依規定使用燈光" in result
    assert "第42條" in result


def test_light_keywords():
    result = local_query_expand("方向燈沒打被罰")
    assert "不依規定使用燈光" in result
    assert "第42條" in result


def test_lane_change_direction_indicator_keywords():
    result = local_query_expand("變換車道沒打方向燈")
    assert "汽車駕駛人轉彎或變換車道" in result
    assert "第48條" in result
    assert "第42條" in result


def test_slow_vehicle_light_keywords():
    result = local_query_expand("自行車夜間未開大燈")
    assert "慢車 夜間行車未開啟燈光" in result
    assert "第73條" in result


def test_red_ticket():
    result = local_query_expand("收到紅單要怎麼辦")
    assert "舉發違反道路交通管理事件通知單" in result


def test_large_heavy_motorcycle_specific():
    """大型重機 must match its own rule, not fall through to 重機 rule."""
    result = local_query_expand("大型重機可以走快速道路嗎")
    assert "第92條" in result
    assert "比照小型汽車" in result


def test_plate_defacement_expands_penalty_articles():
    """牌照污損題的罰則在處罰條例 §13/§14；rewrite 逾時走 dict fallback 時
    必須展開出罰則條文，否則用原句搜尋會造成 §13/§14 召回不穩。"""
    result = local_query_expand("自用大貨車牌照汙損無法辨識之執法處理")
    assert "第13條" in result
    assert "第14條" in result
    assert "使不能辨認其牌號" in result


def test_plate_obscured_expands_penalty_articles():
    result = local_query_expand("號牌遮蔽如何處理")
    assert "第13條" in result
    assert "第14條" in result


def test_plate_without_defacement_not_triggered():
    """牌照相關但非污損/遮蔽（如申請）不應誤觸罰則展開。"""
    q = "汽車牌照怎麼申請"
    assert local_query_expand(q) == q


def test_multiple_keywords_combined():
    result = local_query_expand("酒駕肇逃")
    assert "酒精濃度檢測" in result
    assert "肇事逃逸" in result


def test_original_question_preserved_in_result():
    q = "違停怎麼辦"
    result = local_query_expand(q)
    assert result.startswith(q)
