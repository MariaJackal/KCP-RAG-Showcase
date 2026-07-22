from services.session_manager import (
    MAX_CONVERSATIONS,
    add_message,
    create_conversation,
    delete_conversation,
    get_active_conversation,
    get_sorted_conversations,
    init_session_state,
    migrate_legacy_messages,
    switch_conversation,
)


def _state():
    """Return a plain dict that mimics st.session_state."""
    return {}


def test_init_session_state():
    state = _state()
    init_session_state(state)
    assert "conversations" in state
    assert "active_conversation_id" in state
    assert state["active_conversation_id"] is None


def test_create_conversation():
    state = _state()
    conv = create_conversation(state)
    assert conv.id in state["conversations"]
    assert state["active_conversation_id"] == conv.id
    assert conv.title == "新對話"


def test_create_conversation_with_persona():
    state = _state()
    conv = create_conversation(state, persona_id="traffic")
    assert conv.persona_id == "traffic"


def test_switch_conversation():
    state = _state()
    conv1 = create_conversation(state)
    conv2 = create_conversation(state)
    assert state["active_conversation_id"] == conv2.id

    result = switch_conversation(state, conv1.id)
    assert result is conv1
    assert state["active_conversation_id"] == conv1.id


def test_switch_nonexistent_returns_none():
    state = _state()
    create_conversation(state)
    result = switch_conversation(state, "nonexistent")
    assert result is None


def test_delete_conversation_auto_switch():
    state = _state()
    conv1 = create_conversation(state)
    conv2 = create_conversation(state)
    assert state["active_conversation_id"] == conv2.id

    delete_conversation(state, conv2.id)
    assert conv2.id not in state["conversations"]
    assert state["active_conversation_id"] == conv1.id


def test_delete_last_conversation():
    state = _state()
    conv = create_conversation(state)
    delete_conversation(state, conv.id)
    assert state["active_conversation_id"] is None
    assert len(state["conversations"]) == 0


def test_delete_nonexistent_is_noop():
    state = _state()
    create_conversation(state)
    delete_conversation(state, "nonexistent")
    assert len(state["conversations"]) == 1


def test_add_message_auto_title():
    state = _state()
    create_conversation(state)
    add_message(state, "user", "關於酒駕取締的問題想請教")
    conv = get_active_conversation(state)
    assert conv.title == "關於酒駕取締的問題想請教"
    assert len(conv.messages) == 1
    assert conv.messages[0].role == "user"


def test_add_message_auto_title_truncates():
    state = _state()
    create_conversation(state)
    long_msg = "這是一個非常長的問題，關於警察在執行臨檢勤務時的法律依據與相關作業程序"
    add_message(state, "user", long_msg)
    conv = get_active_conversation(state)
    assert len(conv.title) <= 20


def test_add_message_no_active_conversation():
    state = _state()
    init_session_state(state)
    add_message(state, "user", "test")  # Should not raise


def test_get_sorted_conversations_newest_first():
    state = _state()
    conv1 = create_conversation(state)
    conv1.created_at = "2024-01-01T00:00:00"
    conv2 = create_conversation(state)
    conv2.created_at = "2024-01-02T00:00:00"
    conv3 = create_conversation(state)
    conv3.created_at = "2024-01-03T00:00:00"
    sorted_convs = get_sorted_conversations(state)
    assert sorted_convs[0].id == conv3.id
    assert sorted_convs[-1].id == conv1.id


def test_migrate_legacy_messages():
    state = {
        "messages": [
            {"role": "user", "content": "酒駕相關規定"},
            {"role": "assistant", "content": "根據道交條例..."},
        ]
    }
    migrate_legacy_messages(state)
    assert "messages" not in state
    conv = get_active_conversation(state)
    assert conv is not None
    assert len(conv.messages) == 2
    assert conv.title == "酒駕相關規定"


def test_migrate_empty_messages():
    state = {"messages": []}
    migrate_legacy_messages(state)
    assert "messages" not in state
    # No conversation should be created
    init_session_state(state)
    assert get_active_conversation(state) is None


def test_migrate_no_messages_key():
    state = {}
    migrate_legacy_messages(state)
    assert "conversations" not in state


def test_max_conversations_enforced():
    state = _state()
    for _ in range(MAX_CONVERSATIONS + 5):
        create_conversation(state)
    assert len(state["conversations"]) <= MAX_CONVERSATIONS
