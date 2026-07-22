"""Phase 2: session serialization + Firestore store + backend selection."""

from models import Conversation, Message


def _sample_state():
    conv = Conversation(
        id="c1",
        title="酒駕問答",
        messages=[
            Message(role="user", content="酒駕怎麼處理", timestamp="2026-06-24T00:00:00+00:00"),
            Message(role="assistant", content="依第35條裁罰。", timestamp="2026-06-24T00:00:01+00:00"),
        ],
        created_at="2026-06-24T00:00:00+00:00",
        persona_id="traffic",
    )
    return {"conversations": {"c1": conv}, "active_conversation_id": "c1"}


# --- session_serde round-trip ---

class TestSerde:
    def test_serialize_deserialize_round_trip(self):
        from api.session_serde import deserialize, serialize

        state = _sample_state()
        restored = deserialize(serialize(state))
        conv = restored["conversations"]["c1"]
        assert isinstance(conv, Conversation)
        assert conv.title == "酒駕問答"
        assert conv.persona_id == "traffic"
        assert len(conv.messages) == 2
        assert conv.messages[0].content == "酒駕怎麼處理"
        assert restored["active_conversation_id"] == "c1"

    def test_payload_round_trip_preserves_conversation_objects(self):
        from api.session_serde import payload_to_state, state_to_payload

        payload = state_to_payload(_sample_state())
        # payload must be plain JSON-able dicts, not Conversation objects
        assert isinstance(payload["conversations"]["c1"], dict)
        restored = payload_to_state(payload)
        assert isinstance(restored["conversations"]["c1"], Conversation)

    def test_empty_state(self):
        from api.session_serde import empty_state

        s = empty_state()
        assert s == {"conversations": {}, "active_conversation_id": None}

    def test_corrupt_conversation_is_skipped_not_fatal(self, monkeypatch):
        """單筆損壞對話（缺 id）應被跳過，其餘正常對話仍還原成功。"""
        import api.session_serde as mod

        monkeypatch.setattr(mod, "log_event", lambda event, **kw: None)

        payload = {
            "conversations": {
                "good": {
                    "id": "good",
                    "title": "正常",
                    "messages": [],
                    "created_at": "2026-06-24T00:00:00+00:00",
                    "persona_id": "traffic",
                },
                "bad": {"title": "缺id的壞資料"},  # 缺 "id" → 應被跳過
            },
            "active_conversation_id": "good",
        }
        restored = mod.payload_to_state(payload)
        assert "good" in restored["conversations"]
        assert "bad" not in restored["conversations"]

    def test_conversation_with_corrupt_message_is_kept(self):
        """對話內單筆訊息損壞應被跳過，對話本身仍還原。"""
        from api.session_serde import payload_to_state

        payload = {
            "conversations": {
                "c1": {
                    "id": "c1",
                    "title": "含壞訊息",
                    "messages": [
                        {"role": "user", "content": "ok", "timestamp": "2026-06-24T00:00:00+00:00"},
                        {"role": "user"},  # 缺 content/timestamp → 跳過此筆
                    ],
                    "created_at": "2026-06-24T00:00:00+00:00",
                    "persona_id": "traffic",
                },
            },
            "active_conversation_id": "c1",
        }
        restored = payload_to_state(payload)
        assert len(restored["conversations"]["c1"].messages) == 1


# --- FirestoreSessionStore with a mock client ---

class _FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _FakeDocRef:
    def __init__(self, store_dict, doc_id):
        self._store = store_dict
        self._id = doc_id

    def get(self):
        return _FakeSnapshot(self._store.get(self._id))

    def set(self, payload):
        self._store[self._id] = payload

    def delete(self):
        self._store.pop(self._id, None)


class _FakeCollection:
    def __init__(self):
        self._docs = {}

    def document(self, doc_id):
        return _FakeDocRef(self._docs, doc_id)


class _FakeClient:
    def __init__(self):
        self._collection = _FakeCollection()

    def collection(self, name):
        return self._collection


class TestFirestoreStore:
    def _store(self):
        from api.firestore_session_store import FirestoreSessionStore

        return FirestoreSessionStore(project="proj", collection="sessions", client=_FakeClient())

    def test_get_missing_returns_empty_state(self):
        store = self._store()
        assert store.get("nobody") == {"conversations": {}, "active_conversation_id": None}

    def test_save_then_get_round_trip(self):
        store = self._store()
        store.save("u1", _sample_state())
        restored = store.get("u1")
        conv = restored["conversations"]["c1"]
        assert isinstance(conv, Conversation)
        assert conv.messages[1].content == "依第35條裁罰。"
        assert restored["active_conversation_id"] == "c1"

    def test_delete(self):
        store = self._store()
        store.save("u1", _sample_state())
        store.delete("u1")
        assert store.get("u1")["conversations"] == {}

    def test_oversize_trims_oldest_conversation(self, monkeypatch):
        import api.firestore_session_store as mod

        events = []
        monkeypatch.setattr(mod, "log_event", lambda event, **kw: events.append((event, kw)))
        monkeypatch.setattr(mod, "_SIZE_TRIM_BYTES", 10)  # force the threshold

        # 兩個對話，c_old 較舊；門檻壓到 10 bytes → 應裁到僅剩 active 的 c_new。
        old = Conversation(id="c_old", title="舊", messages=[], created_at="2026-06-24T00:00:00+00:00")
        new = Conversation(id="c_new", title="新", messages=[], created_at="2026-06-25T00:00:00+00:00")
        state = {"conversations": {"c_old": old, "c_new": new}, "active_conversation_id": "c_new"}

        store = self._store()
        store.save("u1", state)

        assert any(e[0] == "firestore_session_trimmed" for e in events)
        restored = store.get("u1")
        # 使用中的對話必須保留，最舊的被裁掉。
        assert "c_new" in restored["conversations"]
        assert "c_old" not in restored["conversations"]

    def test_oversize_keeps_at_least_one_conversation(self, monkeypatch):
        import api.firestore_session_store as mod

        monkeypatch.setattr(mod, "log_event", lambda event, **kw: None)
        monkeypatch.setattr(mod, "_SIZE_TRIM_BYTES", 10)  # force below any real payload

        store = self._store()
        store.save("u1", _sample_state())  # single conversation

        # 即使超限，唯一的對話也不能被裁光，寫入仍須成立。
        assert store.get("u1")["conversations"]["c1"].title == "酒駕問答"

    def test_single_oversize_conversation_trims_oldest_messages(self, monkeypatch):
        """單一對話自己就超限時（多對話裁不掉），改裁該對話最舊訊息，
        寫入仍須成立、且至少保留最新一筆——這是 #19 真正要修掉的失敗。"""
        import api.firestore_session_store as mod

        events = []
        monkeypatch.setattr(mod, "log_event", lambda event, **kw: events.append((event, kw)))

        big = "填充" * 400  # 每筆訊息夠大，讓單一對話就衝破門檻
        messages = [
            Message(role="user", content=f"{i}-{big}", timestamp=f"2026-06-24T00:00:{i:02d}+00:00")
            for i in range(30)
        ]
        conv = Conversation(
            id="c1", title="超長對話", messages=messages,
            created_at="2026-06-24T00:00:00+00:00",
        )
        state = {"conversations": {"c1": conv}, "active_conversation_id": "c1"}

        # 門檻壓在「單筆訊息裝不下多筆」的區間，強制進入階段二裁訊息。
        monkeypatch.setattr(mod, "_SIZE_TRIM_BYTES", 4000)

        store = self._store()
        store.save("u1", state)

        restored = store.get("u1")["conversations"]["c1"]
        assert restored is not None  # 對話仍在（未被刪光）
        assert 0 < len(restored.messages) < 30  # 有裁到訊息、但至少留一筆
        # 保留的是最新的訊息（最舊的被裁掉）。
        assert restored.messages[-1].content.startswith("29-")
        assert any(
            e[0] == "firestore_session_trimmed" and e[1].get("removed_messages", 0) > 0
            for e in events
        )

    def test_under_threshold_no_warning(self, monkeypatch):
        import api.firestore_session_store as mod

        events = []
        monkeypatch.setattr(mod, "log_event", lambda event, **kw: events.append((event, kw)))

        store = self._store()
        store.save("u1", _sample_state())
        assert not events


# --- deps.build_session_store backend selection ---

class TestBackendSelection:
    def _settings(self, backend, monkeypatch):
        from config import load_settings

        # 這些測試透過 secrets= 指定後端，但 config._read_setting 讓「環境變數
        # 優先於 secrets」。本機若存在 .env，load_dotenv 會把 SESSION_STORE_
        # BACKEND 等值灌進 os.environ 蓋掉 secrets，導致測試拿到 .env 的後端
        # 而非本測試指定的。清掉相關環境變數，讓 secrets 如測試意圖生效
        # （現形化原本「機器無 .env」的隱含前提）。
        for key in ("VERTEX_PROJECT_ID", "VERTEX_DATA_STORE_ID", "APP_PASSWORD",
                    "SESSION_STORE_BACKEND"):
            monkeypatch.delenv(key, raising=False)

        return load_settings(secrets={
            "VERTEX_PROJECT_ID": "proj",
            "VERTEX_DATA_STORE_ID": "ds",
            "APP_PASSWORD": "pw",
            "SESSION_STORE_BACKEND": backend,
        })

    def test_memory_default(self, monkeypatch):
        from api.deps import build_session_store
        from api.memory_session_store import MemorySessionStore

        assert isinstance(build_session_store(self._settings("memory", monkeypatch)), MemorySessionStore)

    def test_sqlite(self, tmp_path, monkeypatch):
        from api.deps import build_session_store
        from api.session_store import SessionStore

        monkeypatch.chdir(tmp_path)  # SessionStore writes sessions.db in cwd
        assert isinstance(build_session_store(self._settings("sqlite", monkeypatch)), SessionStore)

    def test_firestore(self, monkeypatch):
        import api.firestore_session_store as mod
        from api.deps import build_session_store

        # avoid real GCP connection
        monkeypatch.setattr(mod.FirestoreSessionStore, "__init__", lambda self, **kw: None)
        store = build_session_store(self._settings("firestore", monkeypatch))
        assert isinstance(store, mod.FirestoreSessionStore)
