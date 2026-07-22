"""Build ModelClient instances based on Settings.model_provider.

【公開版打樁 / PUBLIC STUB】
這是本專案建立實際 Gemini 模型 client 的唯一入口。為避免公開 repo 被直接
打包執行，實際建立 client 的路徑已改為拋出例外。正式（私有）版本在此依
settings.model_provider 建立真正的 GoogleGenAIModelClient / VertexLegacyModelClient。
單元測試不受影響：測試皆以 mock model client 注入，不經過本工廠。
"""

from config import Settings
from services.model_client import GoogleGenAIModelClient, ModelClient, VertexLegacyModelClient

_STUB_MESSAGE = (
    "model_factory 為公開版打樁：實際的 Gemini model client 建立邏輯未包含在此 "
    "公開 repo 中。請參考架構說明自行實作，或於正式（私有）版本執行。"
)


def _legacy(model_name: str) -> ModelClient:
    return VertexLegacyModelClient(model_name)


def _genai(settings: Settings, model_name: str) -> ModelClient:
    return GoogleGenAIModelClient(
        project_id=settings.project_id,
        location=settings.genai_location,
        model_name=model_name,
    )


def _build(settings: Settings, model_name: str) -> ModelClient:
    raise NotImplementedError(_STUB_MESSAGE)


def build_router_client(settings: Settings) -> ModelClient:
    return _build(settings, settings.router_model_name)


def build_rewriter_client(settings: Settings) -> ModelClient:
    return _build(settings, settings.rewriter_model_name)


def build_answer_client(settings: Settings) -> ModelClient:
    return _build(settings, settings.answer_model_name)


def build_fallback_client(settings: Settings) -> ModelClient:
    return _build(settings, settings.fallback_model_name)
