"""Vertex AI Check Grounding API 答案忠實度檢核（1-3）。

答案生成後，對照進 prompt 的引用來源逐句驗證，回傳 support_score
（0~1，越高越忠實）。任何失敗回傳 None，絕不阻斷主流程——檢核是
加值層，答案照常回覆。

呼叫介面（google-cloud-discoveryengine 0.13.x 確認）：
GroundedGenerationServiceClient.check_grounding，global endpoint，
grounding_config = default_grounding_config。
"""

from services.telemetry import log_event

# 低於門檻時附加在答案尾端的警示（呼應「寧可少答，不要答錯」）
GROUNDING_WARNING_BLOCK = (
    "\n\n---\n"
    "⚠ **本回答部分內容信心不足，請務必核對原始法規條文後再引用。**"
)

_MAX_FACT_CHARS = 2000

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
        # Check Grounding API 走 global endpoint
        _default_client = discoveryengine.GroundedGenerationServiceClient()
    return _default_client


def check_grounding(answer, sources, settings, grounding_client=None, request_id=None):
    """檢核 answer 是否被 sources 支持，回傳 support_score（float）或 None。

    sources 為 pipeline 的引用來源（[{"index", "title", "content"}]），
    與進 answer prompt 的內容一致——檢核對照的就是模型實際看到的資料。
    失敗（SDK 不可用、API 錯誤、空輸入）一律回 None，不拋例外。
    """
    if not answer or not answer.strip() or not sources:
        return None

    client = grounding_client if grounding_client is not None else _get_default_client()
    if client is None:
        log_event("grounding_skipped", request_id=request_id, reason="sdk_unavailable")
        return None

    request = {
        "grounding_config": (
            f"projects/{settings.project_id}"
            f"/locations/global/groundingConfigs/default_grounding_config"
        ),
        "answer_candidate": answer,
        "facts": [
            {
                "fact_text": (source.get("content") or "")[:_MAX_FACT_CHARS],
                "attributes": {"title": source.get("title") or ""},
            }
            for source in sources
            if (source.get("content") or "").strip()
        ],
        "grounding_spec": {
            "citation_threshold": float(getattr(settings, "grounding_threshold", 0.6)),
        },
    }
    if not request["facts"]:
        return None

    try:
        response = client.check_grounding(request=request)
        score = float(response.support_score)
        log_event(
            "grounding_checked",
            request_id=request_id,
            score=round(score, 4),
            fact_count=len(request["facts"]),
            claim_count=len(getattr(response, "claims", []) or []),
        )
        return score
    except Exception as e:
        log_event(
            "grounding_failed", request_id=request_id, reason=f"{type(e).__name__}: {e}"
        )
        return None
