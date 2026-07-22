"""Vertex AI Ranking API 重排序層（1-1）。

在 search 與 answer 之間對檢索結果做語意重排。任何失敗（SDK 不可用、
API 錯誤、回應格式異常）都回傳原始順序，絕不阻斷主流程。
呼叫形式與參數依 2026-07-19 前置探測結果（見升級計畫文件）。
"""

from rag_logic import extract_result_content, extract_result_data, extract_result_title
from services.telemetry import log_event

RANKING_MODEL = "semantic-ranker-default@latest"
_MAX_RECORD_CONTENT_CHARS = 2000

_default_client = None


def _load_discoveryengine():
    try:
        from google.cloud import discoveryengine_v1 as discoveryengine
    except Exception:
        return None
    return discoveryengine


def _get_default_client():
    global _default_client
    if _default_client is None:
        discoveryengine = _load_discoveryengine()
        if discoveryengine is None:
            return None
        # Ranking API 走 global endpoint，不需要 regional client_options
        _default_client = discoveryengine.RankServiceClient()
    return _default_client


def _record_fields(result):
    raw = getattr(result, "result", result)  # 解開 _SubQueryResult 包裝
    data = extract_result_data(raw)
    title = extract_result_title(data)
    content = extract_result_content(data) or ""
    return title, content[:_MAX_RECORD_CONTENT_CHARS]


def rerank_results(query, results, settings, rank_client=None, request_id=None):
    """以 Ranking API 重排 results，回傳前 settings.rerank_top_n 筆（原物件重新排序）。

    失敗時回傳原始 list（不截斷），行為與未啟用 rerank 一致。
    """
    results = list(results)
    if len(results) < 2 or not query or not query.strip():
        return results

    client = rank_client if rank_client is not None else _get_default_client()
    if client is None:
        log_event("rerank_skipped", request_id=request_id, reason="sdk_unavailable")
        return results

    top_n = max(1, int(getattr(settings, "rerank_top_n", 15)))
    request = {
        "ranking_config": (
            f"projects/{settings.project_id}"
            f"/locations/global/rankingConfigs/default_ranking_config"
        ),
        "model": RANKING_MODEL,
        "query": query.strip(),
        "records": [
            {"id": str(idx), "title": title, "content": content}
            for idx, (title, content) in (
                (i, _record_fields(r)) for i, r in enumerate(results)
            )
        ],
        "top_n": min(top_n, len(results)),
    }

    try:
        response = client.rank(request=request)
        reordered = []
        seen = set()
        for record in response.records:
            try:
                idx = int(record.id)
            except (TypeError, ValueError):
                continue
            if idx in seen or not (0 <= idx < len(results)):
                continue
            seen.add(idx)
            reordered.append(results[idx])
        if not reordered:
            log_event("rerank_failed", request_id=request_id, reason="empty_response")
            return results
        log_event(
            "rerank_completed",
            request_id=request_id,
            input_count=len(results),
            output_count=len(reordered),
            top_score=round(float(getattr(response.records[0], "score", 0.0)), 4),
        )
        return reordered
    except Exception as e:
        log_event("rerank_failed", request_id=request_id, reason=f"{type(e).__name__}: {e}")
        return results
