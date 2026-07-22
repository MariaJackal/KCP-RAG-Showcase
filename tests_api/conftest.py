import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.auth import create_google_token
from api.main import app
from config import Settings
from models import Conversation
from services.cache_store import TTLCache


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_PASSWORD", "testpw")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")


class InMemorySessionStore:
    def __init__(self):
        self._db = {}

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


@pytest.fixture()
def mock_settings():
    return Settings(
        project_id="test-project",
        data_store_id="test-ds",
        location="global",
        vertex_init_location="us-central1",
        app_password="testpw",
        admin_password="adminpw",
        gcs_staging_bucket="test-bucket",
    )


@pytest.fixture()
def client(mock_settings):
    def _mock_init_runtime(app_state):
        app_state.settings = mock_settings
        app_state.rewriter_model = MagicMock()
        app_state.answer_model = MagicMock()
        app_state.search_client = MagicMock()
        app_state.document_client = MagicMock()
        app_state.search_cache = TTLCache(ttl_seconds=300, max_entries=256)
        app_state.answer_cache = TTLCache(ttl_seconds=300, max_entries=256)
        app_state.session_store = InMemorySessionStore()

    with patch("api.deps.init_runtime", _mock_init_runtime):
        with TestClient(app) as test_client:
            yield test_client


@pytest.fixture()
def user_token():
    return create_google_token(sub="test-user-sub", email="testuser@example.com", role="user")


@pytest.fixture()
def admin_token():
    return create_google_token(sub="test-admin-sub", email="kawas4ki.z2@gmail.com", role="admin")


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
