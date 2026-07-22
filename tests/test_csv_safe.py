"""CSV/公式注入防護（Finding 1 修復）的回歸測試。"""

import csv
import io
import json
from unittest.mock import MagicMock

from config import Settings
from services.csv_safe import csv_safe
from services.conversation_export import export_conversations_csv
from services.feedback_log import export_feedback_csv
from services.question_log import export_questions_csv


def _settings(**kw):
    base = dict(
        project_id="p", data_store_id="d", location="global",
        vertex_init_location="us-central1", app_password="pw",
        firestore_collection="sessions",
    )
    base.update(kw)
    return Settings(**base)


# --- csv_safe helper ---

def test_csv_safe_neutralizes_formula_leading_chars():
    for trigger in ("=", "+", "-", "@", "\t", "\r"):
        assert csv_safe(f"{trigger}cmd").startswith("'" + trigger)


def test_csv_safe_leaves_normal_text_untouched():
    assert csv_safe("酒駕怎麼處理") == "酒駕怎麼處理"
    assert csv_safe("2026-06-24T00:00:00+00:00") == "2026-06-24T00:00:00+00:00"
    assert csv_safe("") == ""
    assert csv_safe(None) == ""


# --- exporters must not emit a live formula cell ---

def _cells(text):
    """以 csv parser 還原所有儲存格（處理引號跳脫）。"""
    return [c for row in csv.reader(io.StringIO(text)) for c in row]


def _assert_no_live_formula(text, payload):
    cells = _cells(text)
    # 沒有任何儲存格以公式觸發字元開頭
    for c in cells:
        assert not (c and c[0] in ("=", "+", "-", "@", "\t", "\r")), f"未中和的儲存格: {c!r}"
    # 原始內容仍保留（被前綴中和後仍含原字串）
    assert any(payload in c for c in cells)


class _Doc:
    def __init__(self, doc_id, data):
        self.id, self._data = doc_id, data

    def to_dict(self):
        return self._data


class _Client:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        c = MagicMock()
        c.stream.return_value = iter(self._docs)
        return c


def test_conversation_export_neutralizes_formula_in_question():
    payload = "=HYPERLINK(\"http://evil\",\"x\")"
    docs = [_Doc("subA", {"conversations": {
        "c1": {"title": "標題", "created_at": "T0", "messages": [
            {"role": "user", "content": payload},
            {"role": "assistant", "content": "正常答案", "timestamp": "t1"},
        ]},
    }})]
    text = export_conversations_csv(_settings(), firestore_client=_Client(docs)).decode("utf-8-sig")
    _assert_no_live_formula(text, payload)


def _blob(obj):
    b = MagicMock()
    b.download_as_text.return_value = json.dumps(obj)
    return b


def _storage(blobs):
    bucket = MagicMock()
    bucket.list_blobs.return_value = iter(blobs)
    client = MagicMock()
    client.bucket.return_value = bucket
    return client


def test_feedback_export_neutralizes_formula_in_content():
    payload = "=cmd|'/c calc'!A1"
    blobs = [_blob({"ts": "T0", "type": "答案錯誤", "content": payload, "user_sub": "u"})]
    text = export_feedback_csv(
        _settings(feedback_log_bucket="b"), storage_client=_storage(blobs)
    ).decode("utf-8-sig")
    _assert_no_live_formula(text, payload)


def test_question_export_neutralizes_formula_in_question():
    payload = "@SUM(1+1)"
    blobs = [_blob({"ts": "T0", "question": payload, "user_sub": "u"})]
    text = export_questions_csv(
        _settings(question_log_bucket="b"), storage_client=_storage(blobs)
    ).decode("utf-8-sig")
    _assert_no_live_formula(text, payload)
