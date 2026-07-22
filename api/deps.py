"""Shared dependencies for FastAPI routes.

Initializes model clients, search client, and caches once at startup.
Stored on ``app.state`` and accessed via FastAPI dependency injection.
"""

from config import Settings, load_settings
from services.cache_store import TTLCache
from services.client_factory import build_document_client, build_search_client
from services.model_factory import (
    build_answer_client,
    build_fallback_client,
    build_rewriter_client,
    build_router_client,
)


def build_session_store(settings: Settings):
    """Select the session store backend from settings (memory | sqlite | firestore)."""
    backend = settings.session_store_backend
    if backend == "firestore":
        from api.firestore_session_store import FirestoreSessionStore
        return FirestoreSessionStore(
            project=settings.project_id, collection=settings.firestore_collection
        )
    if backend == "sqlite":
        from api.session_store import SessionStore
        return SessionStore()
    from api.memory_session_store import MemorySessionStore
    return MemorySessionStore()


def init_runtime(app_state):
    """Populate *app_state* with all runtime resources (called once at startup)."""
    settings: Settings = load_settings()

    from api.auth import assert_jwt_secret_safe
    assert_jwt_secret_safe(settings.app_password)

    if settings.model_provider == "vertexai_legacy":
        import vertexai
        vertexai.init(project=settings.project_id, location=settings.vertex_init_location)

    app_state.settings = settings
    # rewriter_model and answer_model names kept for compatibility with chat_routes.py
    app_state.rewriter_model = build_rewriter_client(settings)
    app_state.answer_model = build_answer_client(settings)
    app_state.fallback_model = build_fallback_client(settings)
    app_state.router_model = build_router_client(settings)
    app_state.search_client = build_search_client(settings)
    app_state.document_client = build_document_client(settings)
    app_state.search_cache = TTLCache(ttl_seconds=300, max_entries=256)
    app_state.answer_cache = TTLCache(ttl_seconds=300, max_entries=256)
    app_state.session_store = build_session_store(settings)
