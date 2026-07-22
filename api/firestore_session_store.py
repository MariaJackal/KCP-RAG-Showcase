"""Firestore-backed session persistence.

One document per user (collection ``sessions``, doc id = user_id) holding the
serialized session payload — the same model as the SQLite store. Survives Cloud
Run restarts and lets the service scale past a single instance.

Firestore has a 1MB per-document limit. Payloads approaching the limit are
auto-trimmed (oldest conversation first, mirroring MAX_CONVERSATIONS LRU) so a
heavy user never hits a hard write failure that would break every later request.
"""

import json

from api.session_serde import empty_state, payload_to_state, state_to_payload
from services.telemetry import log_event

# Firestore hard limit is 1 MiB; trim well before so writes never fail.
_SIZE_TRIM_BYTES = 900_000


class FirestoreSessionStore:
    def __init__(self, project: str, collection: str = "sessions", client=None):
        # Import lazily so local/dev (memory backend) needn't install the package.
        if client is None:
            from google.cloud import firestore
            client = firestore.Client(project=project)
        self._collection = client.collection(collection)

    def get(self, user_id: str) -> dict:
        snap = self._collection.document(user_id).get()
        if not snap.exists:
            return empty_state()
        return payload_to_state(snap.to_dict() or {})

    def save(self, user_id: str, state: dict):
        payload = state_to_payload(state)
        payload = self._trim_if_oversized(user_id, payload)
        self._collection.document(user_id).set(payload)

    def delete(self, user_id: str):
        self._collection.document(user_id).delete()

    @staticmethod
    def _payload_size(payload: dict) -> int:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    @classmethod
    def _trim_if_oversized(cls, user_id: str, payload: dict) -> dict:
        """若序列化後逼近 Firestore 1MiB 上限，先依 created_at 由舊到新刪對話
        （保留使用中的），刪到僅剩一個仍超限時，改裁該對話內最舊的訊息。
        目標是任何情況下寫入都不因超限失敗，使該使用者後續請求不再全掛。"""
        if cls._payload_size(payload) <= _SIZE_TRIM_BYTES:
            return payload

        convs = payload.get("conversations", {})
        active_id = payload.get("active_conversation_id")
        removed_convs = 0

        # 階段一：多對話時，由舊到新刪對話（優先保留使用中的），直到剩一個。
        while cls._payload_size(payload) > _SIZE_TRIM_BYTES and len(convs) > 1:
            candidates = [c for c in convs if c != active_id] or list(convs)
            oldest_id = min(candidates, key=lambda cid: convs[cid].get("created_at", ""))
            del convs[oldest_id]
            removed_convs += 1

        # 階段二：僅剩一個對話仍超限 → 裁該對話最舊訊息（保底至少留最新一筆）。
        removed_msgs = 0
        if cls._payload_size(payload) > _SIZE_TRIM_BYTES and convs:
            (only_conv,) = convs.values()
            messages = only_conv.get("messages", [])
            while cls._payload_size(payload) > _SIZE_TRIM_BYTES and len(messages) > 1:
                messages.pop(0)
                removed_msgs += 1

        log_event(
            "firestore_session_trimmed",
            severity="WARNING",
            user_id=user_id,
            removed_conversations=removed_convs,
            removed_messages=removed_msgs,
            final_size_bytes=cls._payload_size(payload),
            limit_bytes=1_048_576,
        )
        return payload
