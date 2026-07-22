"""In-memory session store — drop-in replacement for SessionStore (SQLite).

對話狀態只存在 process 記憶體中，重啟後清空（符合「登入後不保留舊對話」需求）。
介面與 api/session_store.py 完全相同（get / save / delete），呼叫端不需改動。

部署注意：Cloud Run 須設 --max-instances 1，確保同一使用者的請求
不會被路由到不同 instance 而看不到記憶體中的對話歷史。
"""

from models import Conversation


class MemorySessionStore:
    def __init__(self):
        self._db: dict = {}

    def get(self, user_id: str) -> dict:
        state = self._db.get(user_id)
        if state is None:
            return {"conversations": {}, "active_conversation_id": None}
        convs = {}
        for cid, conv in state.get("conversations", {}).items():
            convs[cid] = Conversation.from_dict(conv.to_dict())
        return {
            "conversations": convs,
            "active_conversation_id": state.get("active_conversation_id"),
        }

    def save(self, user_id: str, state: dict):
        convs = {}
        for cid, conv in state.get("conversations", {}).items():
            convs[cid] = Conversation.from_dict(conv.to_dict())
        self._db[user_id] = {
            "conversations": convs,
            "active_conversation_id": state.get("active_conversation_id"),
        }

    def delete(self, user_id: str):
        self._db.pop(user_id, None)
