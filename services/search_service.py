from google.api_core.client_options import ClientOptions

from config import Settings
from services.telemetry import log_event


class _EmptySearchResponse:
    """Lightweight empty response used when query is blank."""

    results = []


class _ProtoFieldShim:
    def __init__(self, payload):
        self._payload = payload

    def HasField(self, name):
        return getattr(self._payload, name, None) is not None


class _SearchRequestShim:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self._pb = _ProtoFieldShim(self)


def _load_discoveryengine():
    try:
        from google.cloud import discoveryengine_v1 as discoveryengine
    except Exception:
        return None
    return discoveryengine


def search_vertex(query_text, settings: Settings, search_client=None):
    """呼叫 Vertex AI Search。"""
    if not query_text or not query_text.strip():
        log_event("search_skipped", reason="empty_query")
        return _EmptySearchResponse()

    discoveryengine = _load_discoveryengine()
    client = search_client
    if client is None:
        # 【公開版打樁 / PUBLIC STUB】
        # 未注入 search_client 時，正式版會於此自建 SearchServiceClient 並實際呼叫
        # Vertex AI Search。公開 repo 移除此路徑以避免直接打包執行；單元測試以
        # mock client 注入（search_client 非 None），不受影響。
        raise NotImplementedError(
            "search_service 為公開版打樁：實際的 Vertex AI Search 呼叫未包含在此公開 "
            "repo 中。請注入 search_client（測試用），或於正式（私有）版本執行。"
        )
        if discoveryengine is None:
            raise RuntimeError("google-cloud-discoveryengine 套件不可用，無法建立 SearchServiceClient")
        client_options = (
            ClientOptions(api_endpoint=f"{settings.location}-discoveryengine.googleapis.com")
            if settings.location != "global"
            else None
        )
        client = discoveryengine.SearchServiceClient(client_options=client_options)

    serving_config = (
        f"projects/{settings.project_id}"
        f"/locations/{settings.location}"
        f"/collections/default_collection"
        f"/engines/{settings.engine_id}"
        f"/servingConfigs/default_search"
    )

    # rerank 啟用時擴大召回池供重排；未啟用維持 15 不變（零回歸）
    page_size = 50 if getattr(settings, "rerank_enabled", False) else 15

    if discoveryengine is None:
        request = _SearchRequestShim(
            serving_config=serving_config,
            query=query_text,
            page_size=page_size,
        )
    else:
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query_text,
            page_size=page_size,
            query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
                condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO,
            ),
            spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
                mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO,
            ),
        )
    return client.search(request)
