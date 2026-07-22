"""scripts/golden_eval.py 指標計算單元測試（純離線）。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from golden_eval import (
    aggregate,
    article_regex,
    evaluate_answer,
    law_hit,
    load_golden_set,
    normalize_text,
)


class TestNormalizeText:
    def test_removes_spaces_in_article(self):
        assert normalize_text("第 35 條") == "第35條"

    def test_fullwidth_digits(self):
        assert normalize_text("第３５條") == "第35條"

    def test_empty(self):
        assert normalize_text("") == ""
        assert normalize_text(None) == ""


class TestArticleRegex:
    def test_plain_article_matches(self):
        assert article_regex("35").search("依第35條處罰")

    def test_plain_article_rejects_zhi_variant(self):
        # 第35條之1 是不同條文，不得誤中第35條
        assert not article_regex("35").search("依第35條之1處罰")

    def test_zhi_variant_matches(self):
        assert article_regex("35-1").search("依第35條之1處罰")
        assert article_regex("35之1").search("依第35條之1處罰")

    def test_zhi_variant_dash_style_matches(self):
        # 系統答案常寫「第 31-1 條」而非「第31條之1」（2026-07-17 基線發現）
        assert article_regex("31-1").search(normalize_text("《道路交通管理處罰條例》第 31-1 條"))

    def test_plain_article_rejects_dash_variant(self):
        assert not article_regex("35").search("依第35-1條處罰")

    def test_zhi_variant_rejects_longer_number(self):
        assert not article_regex("35-1").search("依第35條之11處罰")
        assert not article_regex("35-1").search("依第35-11條處罰")

    def test_invalid_article_raises(self):
        with pytest.raises(ValueError):
            article_regex("三十五")

    def test_chinese_numeral_matches(self):
        # 法規引文常用中文數字（2026-07-17 基線發現：處理規範引「刑法第一百八十五條之三」）
        assert article_regex("185-3").search("涉犯刑法第一百八十五條之三公共危險罪")
        assert article_regex("35").search("依第三十五條規定")
        assert article_regex("49").search("第四十九條第五款")

    def test_chinese_numeral_plain_rejects_zhi(self):
        assert not article_regex("185").search("刑法第一百八十五條之三")


class TestLawHit:
    CASE = {"law": "道路交通管理處罰條例", "article": "35", "law_aliases": ["處罰條例"]}

    def test_full_name_and_article(self):
        ans = normalize_text("依《道路交通管理處罰條例》第 35 條規定")
        assert law_hit(ans, self.CASE)

    def test_alias_name_and_article(self):
        ans = normalize_text("依處罰條例第35條規定")
        assert law_hit(ans, self.CASE)

    def test_article_without_law_name_fails(self):
        ans = normalize_text("依第35條規定")
        assert not law_hit(ans, self.CASE)

    def test_law_name_without_article_fails(self):
        ans = normalize_text("依道路交通管理處罰條例規定")
        assert not law_hit(ans, self.CASE)

    ALT_CASE = {
        "law": "道路交通管理處罰條例", "article": "65",
        "law_aliases": ["處罰條例"],
        "alt_laws": [{
            "law": "違反道路交通管理事件統一裁罰基準及處理細則",
            "article": "67", "law_aliases": ["處理細則"],
        }],
    }

    def test_alt_law_hit_when_primary_missing(self):
        # 答案引細則§67（與條例§65 等價）→ 命中
        ans = normalize_text("依《違反道路交通管理事件統一裁罰基準及處理細則》第 67 條，罰鍰不繳者移送強制執行")
        assert law_hit(ans, self.ALT_CASE)

    def test_alt_law_primary_still_hits(self):
        ans = normalize_text("依處罰條例第65條移送強制執行")
        assert law_hit(ans, self.ALT_CASE)

    def test_alt_law_neither_fails(self):
        # 細則名對但條號錯 → 不命中
        ans = normalize_text("依處理細則第44條辦理")
        assert not law_hit(ans, self.ALT_CASE)


class TestEvaluateAnswer:
    def test_metrics_computed(self):
        case = {
            "id": "t1",
            "category": "酒駕",
            "expected_laws": [
                {"law": "道路交通管理處罰條例", "article": "35"},
                {"law": "刑法", "article": "185-3"},
            ],
            "expected_keywords": ["罰鍰", "吊扣", "移送"],
        }
        answer = "依《道路交通管理處罰條例》第 35 條處罰鍰並吊扣駕照。"
        m = evaluate_answer(answer, case)
        assert m["law_hit_rate"] == 0.5  # 刑法185-3 未命中
        assert m["keyword_coverage"] == pytest.approx(2 / 3)
        assert m["context_recall"] is None

    def test_no_expected_laws_gives_none(self):
        m = evaluate_answer("任意答案", {"id": "t2", "category": "x"})
        assert m["law_hit_rate"] is None
        assert m["keyword_coverage"] is None

    def test_context_recall_computed_from_sources(self):
        case = {
            "id": "t3",
            "category": "x",
            "expected_laws": [
                {"law": "道路交通管理處罰條例", "article": "73", "law_aliases": ["處罰條例"]},
                {"law": "刑法", "article": "185-4"},
            ],
        }
        sources = [
            {"index": 1, "title": "道路交通管理處罰條例 第 73 條", "content": "慢車駕駛人..."},
            {"index": 2, "title": "道路交通安全規則 第 115-7 條", "content": "微型電動二輪車..."},
        ]
        m = evaluate_answer("答案未引用任何法條", case, sources=sources)
        assert m["context_recall"] == 0.5  # §73 在檢索內、刑法185-4 不在
        assert m["law_hit_rate"] == 0.0  # 答案層仍未引用

    def test_context_recall_none_without_sources(self):
        case = {"id": "t4", "category": "x",
                "expected_laws": [{"law": "刑法", "article": "185-4"}]}
        assert evaluate_answer("答", case)["context_recall"] is None
        assert evaluate_answer("答", case, sources=[])["context_recall"] is None


class TestAggregate:
    def test_none_excluded_from_denominator(self):
        per_q = [
            {"law_hit_rate": 1.0, "keyword_coverage": 0.5, "context_recall": None},
            {"law_hit_rate": None, "keyword_coverage": 1.0, "context_recall": None},
            {"law_hit_rate": 0.0, "keyword_coverage": None, "context_recall": None},
        ]
        s = aggregate(per_q)
        assert s["total_questions"] == 3
        assert s["avg_law_hit_rate"] == 0.5
        assert s["questions_with_expected_laws"] == 2
        assert s["questions_all_laws_hit"] == 1
        assert s["avg_keyword_coverage"] == 0.75
        assert s["avg_context_recall"] is None


class TestLoadGoldenSet:
    def test_load_valid_jsonl(self, tmp_path):
        p = tmp_path / "gs.jsonl"
        p.write_text(
            '{"id": "a", "question": "q1", "category": "c", '
            '"expected_laws": [{"law": "L", "article": "35-1"}]}\n'
            "\n"
            '{"id": "b", "question": "q2", "category": "c"}\n',
            encoding="utf-8",
        )
        cases = load_golden_set(p)
        assert [c["id"] for c in cases] == ["a", "b"]

    def test_duplicate_id_raises(self, tmp_path):
        p = tmp_path / "gs.jsonl"
        p.write_text(
            '{"id": "a", "question": "q", "category": "c"}\n'
            '{"id": "a", "question": "q", "category": "c"}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="重複"):
            load_golden_set(p)

    def test_missing_field_raises(self, tmp_path):
        p = tmp_path / "gs.jsonl"
        p.write_text('{"id": "a", "question": "q"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="category"):
            load_golden_set(p)

    def test_bad_article_raises(self, tmp_path):
        p = tmp_path / "gs.jsonl"
        p.write_text(
            '{"id": "a", "question": "q", "category": "c", '
            '"expected_laws": [{"law": "L", "article": "第三十五條"}]}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_golden_set(p)

    def test_real_golden_set_loads_if_present(self):
        gs = ROOT / "eval" / "golden_set.jsonl"
        if not gs.exists():
            pytest.skip("golden_set.jsonl 尚未建立")
        cases = load_golden_set(gs)
        assert len(cases) >= 1
