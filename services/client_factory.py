from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as discoveryengine

from config import Settings

# ────────────────────────────────────────────────────────────────────────────
# 【公開版打樁 / PUBLIC STUB】
# 這是本專案對 Google Cloud Discovery Engine 建立實際連線的唯一入口。
# 為避免公開 repo 被直接打包執行，實際建立 client 的路徑已改為拋出例外。
# 正式（私有）版本在此建立真正的 SearchServiceClient / DocumentServiceClient。
# 單元測試不受影響：測試皆以 mock client 注入，不經過本工廠。
# ────────────────────────────────────────────────────────────────────────────

_STUB_MESSAGE = (
    "client_factory 為公開版打樁：實際的 Discovery Engine client 建立邏輯未包含在此 "
    "公開 repo 中。請參考架構說明自行實作，或於正式（私有）版本執行。"
)


def _client_options(settings: Settings):
    if settings.location == "global":
        return None
    return ClientOptions(api_endpoint=f"{settings.location}-discoveryengine.googleapis.com")


def build_document_client(settings: Settings):
    raise NotImplementedError(_STUB_MESSAGE)


def build_search_client(settings: Settings):
    raise NotImplementedError(_STUB_MESSAGE)
