from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List
from uuid import uuid4


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Message:
    role: str
    content: str
    timestamp: str = field(default_factory=_now_iso)
    # 引用來源 [{"index", "title", "content"}]；僅 assistant 訊息使用，
    # 舊訊息無此欄位（from_dict 預設空 list）
    citations: List[dict] = field(default_factory=list)
    # 使用者評分 ""/"up"/"down"；僅 assistant 訊息使用，
    # 舊訊息無此欄位（from_dict 預設空字串）
    rating: str = ""


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    title: str = "新對話"
    messages: List[Message] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    persona_id: str = "traffic"

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    **({"citations": m.citations} if m.citations else {}),
                    **({"rating": m.rating} if m.rating else {}),
                }
                for m in self.messages
            ],
            "created_at": self.created_at,
            "persona_id": self.persona_id,
        }

    @classmethod
    def from_dict(cls, d):
        messages = []
        for m in d.get("messages", []):
            try:
                citations = m.get("citations", [])
                if not isinstance(citations, list):
                    citations = []
                rating = m.get("rating", "")
                if rating not in ("up", "down"):
                    rating = ""
                messages.append(
                    Message(
                        role=m["role"],
                        content=m["content"],
                        timestamp=m["timestamp"],
                        citations=citations,
                        rating=rating,
                    )
                )
            except (KeyError, TypeError, AttributeError):
                # 跳過單筆損壞訊息，不讓整個對話（乃至該使用者所有請求）崩潰。
                continue
        return cls(
            id=d["id"],
            title=d.get("title", "新對話"),
            messages=messages,
            created_at=d.get("created_at", _now_iso()),
            persona_id=d.get("persona_id", "traffic"),
        )
