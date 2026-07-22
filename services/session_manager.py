"""Pure-function session manager for multi-conversation state."""

from models import Conversation, Message
from personas import DEFAULT_PERSONA_ID

MAX_CONVERSATIONS = 50


def init_session_state(state):
    """Initialize conversation-related keys if absent."""
    if "conversations" not in state:
        state["conversations"] = {}
    if "active_conversation_id" not in state:
        state["active_conversation_id"] = None


def create_conversation(state, persona_id=DEFAULT_PERSONA_ID):
    """Create a new conversation and set it active. Returns the Conversation."""
    init_session_state(state)
    # Enforce soft limit
    if len(state["conversations"]) >= MAX_CONVERSATIONS:
        # Remove oldest conversation
        oldest_id = min(
            state["conversations"],
            key=lambda cid: state["conversations"][cid].created_at,
        )
        del state["conversations"][oldest_id]
    conv = Conversation(persona_id=persona_id)
    state["conversations"][conv.id] = conv
    state["active_conversation_id"] = conv.id
    return conv


def switch_conversation(state, conv_id):
    """Switch to an existing conversation. Returns the Conversation or None."""
    init_session_state(state)
    conv = state["conversations"].get(conv_id)
    if conv is not None:
        state["active_conversation_id"] = conv_id
    return conv


def delete_conversation(state, conv_id):
    """Delete a conversation and auto-switch to another."""
    init_session_state(state)
    if conv_id not in state["conversations"]:
        return
    del state["conversations"][conv_id]
    if state["active_conversation_id"] == conv_id:
        remaining = get_sorted_conversations(state)
        state["active_conversation_id"] = remaining[0].id if remaining else None


def add_message(state, role, content):
    """Append a message to the active conversation. Auto-titles on first user message."""
    conv = get_active_conversation(state)
    if conv is None:
        return
    conv.messages.append(Message(role=role, content=content))
    # Auto-set title from first user message
    if role == "user" and conv.title == "新對話":
        conv.title = content[:20].strip() or "新對話"


def get_active_conversation(state):
    """Return the active Conversation or None."""
    init_session_state(state)
    cid = state.get("active_conversation_id")
    if cid is None:
        return None
    return state["conversations"].get(cid)


def get_sorted_conversations(state):
    """Return conversations sorted by created_at descending (newest first)."""
    init_session_state(state)
    convs = list(state["conversations"].values())
    convs.sort(key=lambda c: c.created_at, reverse=True)
    return convs


def migrate_legacy_messages(state, persona_id=DEFAULT_PERSONA_ID):
    """Migrate old flat st.session_state.messages to a Conversation."""
    if "messages" not in state:
        return
    old_messages = state.pop("messages")
    if not old_messages:
        return
    init_session_state(state)
    conv = Conversation(persona_id=persona_id)
    for msg in old_messages:
        conv.messages.append(
            Message(role=msg["role"], content=msg["content"])
        )
    # Set title from first user message
    for msg in conv.messages:
        if msg.role == "user":
            conv.title = msg.content[:20].strip() or "新對話"
            break
    state["conversations"][conv.id] = conv
    state["active_conversation_id"] = conv.id
