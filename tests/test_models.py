from models import Conversation, Message


def test_conversation_has_auto_id():
    conv = Conversation()
    assert len(conv.id) == 12


def test_conversation_has_timestamp():
    conv = Conversation()
    assert conv.created_at
    assert "T" in conv.created_at  # ISO format


def test_conversation_default_title():
    conv = Conversation()
    assert conv.title == "新對話"


def test_message_has_timestamp():
    msg = Message(role="user", content="hello")
    assert msg.timestamp
    assert "T" in msg.timestamp


def test_conversation_to_dict_from_dict_roundtrip():
    conv = Conversation(persona_id="traffic")
    conv.messages.append(Message(role="user", content="test"))
    conv.messages.append(Message(role="assistant", content="reply"))

    d = conv.to_dict()
    restored = Conversation.from_dict(d)

    assert restored.id == conv.id
    assert restored.title == conv.title
    assert restored.persona_id == "traffic"
    assert restored.created_at == conv.created_at
    assert len(restored.messages) == 2
    assert restored.messages[0].role == "user"
    assert restored.messages[0].content == "test"
    assert restored.messages[1].role == "assistant"


def test_message_citations_roundtrip():
    conv = Conversation()
    cites = [{"index": 1, "title": "處罰條例 第 35 條", "content": "條文原文..."}]
    conv.messages.append(Message(role="assistant", content="答案 [1]", citations=cites))

    restored = Conversation.from_dict(conv.to_dict())
    assert restored.messages[0].citations == cites


def test_message_without_citations_omits_field_and_defaults_empty():
    conv = Conversation()
    conv.messages.append(Message(role="user", content="問題"))

    d = conv.to_dict()
    assert "citations" not in d["messages"][0]  # 無引用不佔儲存空間
    restored = Conversation.from_dict(d)
    assert restored.messages[0].citations == []


def test_old_format_message_without_citations_loads():
    # 既有 Firestore 舊資料（無 citations 欄位）必須照常載入
    d = {
        "id": "abc",
        "messages": [{"role": "assistant", "content": "舊答案", "timestamp": "2026-01-01T00:00:00"}],
    }
    conv = Conversation.from_dict(d)
    assert conv.messages[0].content == "舊答案"
    assert conv.messages[0].citations == []


def test_corrupt_citations_normalized_to_empty():
    d = {
        "id": "abc",
        "messages": [{"role": "assistant", "content": "答", "timestamp": "t", "citations": "壞資料"}],
    }
    conv = Conversation.from_dict(d)
    assert conv.messages[0].citations == []


def test_message_rating_roundtrip():
    conv = Conversation()
    conv.messages.append(Message(role="assistant", content="答案", rating="up"))

    restored = Conversation.from_dict(conv.to_dict())
    assert restored.messages[0].rating == "up"


def test_message_without_rating_omits_field_and_defaults_empty():
    conv = Conversation()
    conv.messages.append(Message(role="assistant", content="答案"))

    d = conv.to_dict()
    assert "rating" not in d["messages"][0]  # 未評分不佔儲存空間
    restored = Conversation.from_dict(d)
    assert restored.messages[0].rating == ""


def test_old_format_message_without_rating_loads():
    # 既有 Firestore 舊資料（無 rating 欄位）必須照常載入
    d = {
        "id": "abc",
        "messages": [{"role": "assistant", "content": "舊答案", "timestamp": "2026-01-01T00:00:00"}],
    }
    conv = Conversation.from_dict(d)
    assert conv.messages[0].rating == ""


def test_corrupt_rating_normalized_to_empty():
    d = {
        "id": "abc",
        "messages": [{"role": "assistant", "content": "答", "timestamp": "t", "rating": "壞資料"}],
    }
    conv = Conversation.from_dict(d)
    assert conv.messages[0].rating == ""


def test_conversation_from_dict_default_persona():
    d = {
        "id": "abc123",
        "title": "test",
        "messages": [],
        "created_at": "2024-01-01T00:00:00",
    }
    conv = Conversation.from_dict(d)
    assert conv.persona_id == "traffic"
