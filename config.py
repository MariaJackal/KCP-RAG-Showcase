from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local convenience dependency
    load_dotenv = None


if load_dotenv is not None:
    # Let local `uvicorn` runs behave like `make run` by loading repo-root .env.
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


@dataclass(frozen=True)
class Settings:
    # --- existing fields (must not be renamed) ---
    project_id: str
    data_store_id: str
    location: str
    vertex_init_location: str
    app_password: str
    engine_id: str = "your-search-engine-id"
    admin_password: str = ""
    gcs_staging_bucket: str = ""
    question_log_bucket: str = ""
    feedback_log_bucket: str = ""
    google_client_id: str = ""
    admin_emails: str = ""  # comma-separated; empty = no Google admin

    # --- model provider settings (Phase 1) ---
    model_provider: str = "vertexai_legacy"   # vertexai_legacy | google_genai
    genai_location: str = "global"
    router_model_name: str = "gemini-2.5-flash"
    rewriter_model_name: str = "gemini-2.5-flash"
    answer_model_name: str = "gemini-2.5-pro"
    fallback_model_name: str = "gemini-2.5-flash"
    answer_max_output_tokens: int = 32768
    answer_thinking_budget: int = 0    # 0 = do not send (use model default)
    answer_thinking_level: str = ""    # "" = do not send
    answer_max_context_chars: int = 12000
    answer_max_context_sources: int = 10

    # --- session persistence (Phase 2) ---
    session_store_backend: str = "memory"   # memory | sqlite | firestore
    firestore_collection: str = "sessions"

    # --- reranker (1-1) ---
    rerank_enabled: bool = False
    rerank_top_n: int = 15

    # --- grounding check (1-3) ---
    grounding_enabled: bool = False
    grounding_threshold: float = 0.6


def _read_setting(key: str, default: Optional[str] = None, required: bool = False, secrets: Optional[Mapping[str, Any]] = None) -> str:
    value = os.getenv(key)

    if not value and secrets is not None:
        try:
            value = secrets.get(key)
        except Exception:
            value = None

    if isinstance(value, str):
        value = value.strip().strip('"').strip("'")

    if value in (None, ""):
        if required:
            raise RuntimeError(f"缺少必要設定: {key}")
        return default if default is not None else ""

    return str(value)


def _read_int_setting(key: str, default: int, secrets: Optional[Mapping[str, Any]] = None) -> int:
    raw = _read_setting(key, secrets=secrets)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_bool_setting(key: str, default: bool, secrets: Optional[Mapping[str, Any]] = None) -> bool:
    raw = _read_setting(key, secrets=secrets)
    if not raw:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _read_float_setting(key: str, default: float, secrets: Optional[Mapping[str, Any]] = None) -> float:
    raw = _read_setting(key, secrets=secrets)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_settings(secrets: Optional[Mapping[str, Any]] = None) -> Settings:
    return Settings(
        project_id=_read_setting("VERTEX_PROJECT_ID", required=True, secrets=secrets),
        data_store_id=_read_setting("VERTEX_DATA_STORE_ID", required=True, secrets=secrets),
        location=_read_setting("VERTEX_LOCATION", default="global", secrets=secrets),
        vertex_init_location=_read_setting("VERTEX_INIT_LOCATION", default="us-central1", secrets=secrets),
        app_password=_read_setting("APP_PASSWORD", required=True, secrets=secrets),
        engine_id=_read_setting("VERTEX_ENGINE_ID", default="your-search-engine-id", secrets=secrets),
        admin_password=_read_setting("ADMIN_PASSWORD", secrets=secrets),
        gcs_staging_bucket=_read_setting("GCS_STAGING_BUCKET", secrets=secrets),
        question_log_bucket=_read_setting("QUESTION_LOG_BUCKET", secrets=secrets),
        feedback_log_bucket=_read_setting("FEEDBACK_LOG_BUCKET", secrets=secrets),
        google_client_id=_read_setting("GOOGLE_CLIENT_ID", secrets=secrets),
        admin_emails=_read_setting("ADMIN_EMAILS", secrets=secrets),
        # model provider
        model_provider=_read_setting("MODEL_PROVIDER", default="vertexai_legacy", secrets=secrets),
        genai_location=_read_setting("GENAI_LOCATION", default="global", secrets=secrets),
        router_model_name=_read_setting("ROUTER_MODEL_NAME", default="gemini-2.5-flash", secrets=secrets),
        rewriter_model_name=_read_setting("REWRITER_MODEL_NAME", default="gemini-2.5-flash", secrets=secrets),
        answer_model_name=_read_setting("ANSWER_MODEL_NAME", default="gemini-2.5-pro", secrets=secrets),
        fallback_model_name=_read_setting("FALLBACK_MODEL_NAME", default="gemini-2.5-flash", secrets=secrets),
        answer_max_output_tokens=_read_int_setting("ANSWER_MAX_OUTPUT_TOKENS", default=32768, secrets=secrets),
        answer_thinking_budget=_read_int_setting("ANSWER_THINKING_BUDGET", default=0, secrets=secrets),
        answer_thinking_level=_read_setting("ANSWER_THINKING_LEVEL", default="", secrets=secrets),
        answer_max_context_chars=_read_int_setting("ANSWER_MAX_CONTEXT_CHARS", default=12000, secrets=secrets),
        answer_max_context_sources=_read_int_setting("ANSWER_MAX_CONTEXT_SOURCES", default=10, secrets=secrets),
        # session persistence
        session_store_backend=_read_setting("SESSION_STORE_BACKEND", default="memory", secrets=secrets),
        firestore_collection=_read_setting("FIRESTORE_COLLECTION", default="sessions", secrets=secrets),
        # reranker
        rerank_enabled=_read_bool_setting("RERANK_ENABLED", default=False, secrets=secrets),
        rerank_top_n=_read_int_setting("RERANK_TOP_N", default=15, secrets=secrets),
        grounding_enabled=_read_bool_setting("GROUNDING_ENABLED", default=False, secrets=secrets),
        grounding_threshold=_read_float_setting("GROUNDING_THRESHOLD", default=0.6, secrets=secrets),
    )
