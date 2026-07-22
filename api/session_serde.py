"""Shared serialization for session state.

Session state is ``{"conversations": {cid: Conversation}, "active_conversation_id": str|None}``.
Both the SQLite and Firestore stores persist it as a single JSON-serializable
payload; these helpers are the one place that conversion lives.
"""

import json

from models import Conversation
from services.telemetry import log_event


def state_to_payload(state: dict) -> dict:
    """Convert in-memory state (Conversation objects) to a JSON-serializable dict."""
    convs_raw = {}
    for cid, conv in state.get("conversations", {}).items():
        convs_raw[cid] = conv.to_dict() if isinstance(conv, Conversation) else conv
    return {
        "conversations": convs_raw,
        "active_conversation_id": state.get("active_conversation_id"),
    }


def payload_to_state(payload: dict) -> dict:
    """Convert a stored payload back to in-memory state (Conversation objects).

    A single corrupt conversation is skipped (and logged) rather than raising,
    so one bad record can't make every request for that user return 500.
    """
    convs = {}
    for cid, conv_dict in payload.get("conversations", {}).items():
        try:
            convs[cid] = Conversation.from_dict(conv_dict)
        except (KeyError, TypeError, AttributeError) as exc:
            log_event(
                "session_conversation_skipped",
                severity="WARNING",
                conv_id=cid,
                error=str(exc),
            )
    return {
        "conversations": convs,
        "active_conversation_id": payload.get("active_conversation_id"),
    }


def empty_state() -> dict:
    return {"conversations": {}, "active_conversation_id": None}


def serialize(state: dict) -> str:
    return json.dumps(state_to_payload(state), ensure_ascii=False)


def deserialize(raw: str) -> dict:
    return payload_to_state(json.loads(raw))
