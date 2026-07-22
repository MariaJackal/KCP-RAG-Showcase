import pytest

from config import load_settings


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in (
        "VERTEX_PROJECT_ID",
        "VERTEX_DATA_STORE_ID",
        "VERTEX_LOCATION",
        "VERTEX_INIT_LOCATION",
        "VERTEX_ENGINE_ID",
        "APP_PASSWORD",
        "ADMIN_PASSWORD",
        "GCS_STAGING_BUCKET",
        "MODEL_PROVIDER",
        "GENAI_LOCATION",
        "ROUTER_MODEL_NAME",
        "REWRITER_MODEL_NAME",
        "ANSWER_MODEL_NAME",
        "FALLBACK_MODEL_NAME",
        "ANSWER_MAX_OUTPUT_TOKENS",
        "ANSWER_THINKING_BUDGET",
        "ANSWER_THINKING_LEVEL",
        "RERANK_ENABLED",
        "RERANK_TOP_N",
    ):
        monkeypatch.delenv(key, raising=False)


def test_load_settings_from_secrets_mapping():
    settings = load_settings(
        {
            "VERTEX_PROJECT_ID": "p1",
            "VERTEX_DATA_STORE_ID": "d1",
            "VERTEX_LOCATION": "global",
            "VERTEX_INIT_LOCATION": "us-central1",
            "APP_PASSWORD": "pw",
        }
    )
    assert settings.project_id == "p1"
    assert settings.data_store_id == "d1"
    assert settings.app_password == "pw"


def test_load_settings_uses_env_over_secrets(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "env-p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "env-d")
    monkeypatch.setenv("APP_PASSWORD", "env-pw")
    settings = load_settings(
        {
            "VERTEX_PROJECT_ID": "sec-p",
            "VERTEX_DATA_STORE_ID": "sec-d",
            "APP_PASSWORD": "sec-pw",
        }
    )
    assert settings.project_id == "env-p"
    assert settings.data_store_id == "env-d"
    assert settings.app_password == "env-pw"


def test_load_settings_defaults_optional_fields(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    settings = load_settings()
    assert settings.location == "global"
    assert settings.vertex_init_location == "us-central1"
    assert settings.engine_id == "your-search-engine-id"


def test_load_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("VERTEX_PROJECT_ID", raising=False)
    monkeypatch.delenv("VERTEX_DATA_STORE_ID", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        load_settings({})


def test_load_settings_admin_fields_default_empty(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    settings = load_settings()
    assert settings.admin_password == ""
    assert settings.gcs_staging_bucket == ""


def test_load_settings_admin_fields_from_env(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("VERTEX_ENGINE_ID", "search-app-1")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw")
    monkeypatch.setenv("GCS_STAGING_BUCKET", "my-bucket")
    settings = load_settings()
    assert settings.engine_id == "search-app-1"
    assert settings.admin_password == "admin-pw"
    assert settings.gcs_staging_bucket == "my-bucket"


def test_load_settings_model_provider_defaults(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    settings = load_settings()
    assert settings.model_provider == "vertexai_legacy"
    assert settings.genai_location == "global"
    assert settings.answer_model_name == "gemini-2.5-pro"
    assert settings.fallback_model_name == "gemini-2.5-flash"
    assert settings.answer_max_output_tokens == 32768
    assert settings.answer_thinking_budget == 0
    assert settings.answer_thinking_level == ""


def test_load_settings_model_provider_from_env(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("MODEL_PROVIDER", "google_genai")
    monkeypatch.setenv("ANSWER_MODEL_NAME", "gemini-3-flash-preview")
    monkeypatch.setenv("ANSWER_MAX_OUTPUT_TOKENS", "16000")
    monkeypatch.setenv("ANSWER_THINKING_BUDGET", "4096")
    monkeypatch.setenv("ANSWER_THINKING_LEVEL", "LOW")
    settings = load_settings()
    assert settings.model_provider == "google_genai"
    assert settings.answer_model_name == "gemini-3-flash-preview"
    assert settings.answer_max_output_tokens == 16000
    assert settings.answer_thinking_budget == 4096
    assert settings.answer_thinking_level == "LOW"


def test_load_settings_rerank_defaults(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    settings = load_settings()
    assert settings.rerank_enabled is False
    assert settings.rerank_top_n == 15


def test_load_settings_rerank_from_env(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("RERANK_ENABLED", "true")
    monkeypatch.setenv("RERANK_TOP_N", "10")
    settings = load_settings()
    assert settings.rerank_enabled is True
    assert settings.rerank_top_n == 10


def test_load_settings_rerank_enabled_rejects_garbage(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("RERANK_ENABLED", "banana")
    settings = load_settings()
    assert settings.rerank_enabled is False


def test_load_settings_grounding_defaults(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    settings = load_settings()
    assert settings.grounding_enabled is False
    assert settings.grounding_threshold == 0.6


def test_load_settings_grounding_from_env(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("GROUNDING_ENABLED", "true")
    monkeypatch.setenv("GROUNDING_THRESHOLD", "0.75")
    settings = load_settings()
    assert settings.grounding_enabled is True
    assert settings.grounding_threshold == 0.75


def test_load_settings_grounding_threshold_rejects_garbage(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("VERTEX_DATA_STORE_ID", "d")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("GROUNDING_ENABLED", "banana")
    monkeypatch.setenv("GROUNDING_THRESHOLD", "not-a-number")
    settings = load_settings()
    assert settings.grounding_enabled is False
    assert settings.grounding_threshold == 0.6
