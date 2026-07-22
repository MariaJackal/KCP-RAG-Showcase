"""Tests for services/conversation_export."""

from config import Settings
from services.conversation_export import HEADERS, _flatten, export_conversations_csv


def _settings():
    return Settings(
        project_id="p", data_store_id="d", location="global",
        vertex_init_location="us-central1", app_password="pw",
        firestore_collection="sessions",
    )


# --- _flatten ---

def test_flatten_pairs_question_and_answer():
    convs = {
        "c1": {
            "title": "酒駕",
            "created_at": "2026-06-24T00:00:00+00:00",
            "messages": [
                {"role": "user", "content": "酒駕怎麼處理"},
                {"role": "assistant", "content": "依第35條", "timestamp": "t1"},
                {"role": "user", "content": "那拒測呢"},
                {"role": "assistant", "content": "拒測十八萬", "timestamp": "t2"},
            ],
        }
    }
    rows = list(_flatten(convs))
    assert rows == [
        ["酒駕", "2026-06-24T00:00:00+00:00", "酒駕怎麼處理", "依第35條", "t1"],
        ["酒駕", "2026-06-24T00:00:00+00:00", "那拒測呢", "拒測十八萬", "t2"],
    ]


def test_flatten_skips_empty_conversation():
    convs = {"c1": {"title": "新對話", "messages": []}}
    assert list(_flatten(convs)) == []


def test_flatten_trailing_question_without_answer():
    convs = {"c1": {"title": "t", "created_at": "", "messages": [
        {"role": "user", "content": "問題"},
    ]}}
    rows = list(_flatten(convs))
    assert rows == [["t", "", "問題", "", ""]]


def test_flatten_handles_none():
    assert list(_flatten(None)) == []


# --- export_conversations_csv (mock firestore client) ---

class _Doc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def stream(self):
        return iter(self._docs)


class _Client:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        return _Collection(self._docs)


def test_export_csv_has_header_and_rows():
    docs = [_Doc("subA", {"conversations": {
        "c1": {"title": "酒駕", "created_at": "T0", "messages": [
            {"role": "user", "content": "酒駕怎麼處理"},
            {"role": "assistant", "content": "依第35條", "timestamp": "t1"},
        ]},
    }})]
    raw = export_conversations_csv(_settings(), firestore_client=_Client(docs))
    text = raw.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == ",".join(HEADERS)
    assert "subA" in lines[1]
    assert "酒駕怎麼處理" in lines[1]
    assert "依第35條" in lines[1]


def test_export_csv_is_utf8_bom():
    raw = export_conversations_csv(_settings(), firestore_client=_Client([]))
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel-friendly BOM
