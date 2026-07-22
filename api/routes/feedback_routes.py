"""Feedback routes: submit (user) and export CSV (admin)."""

import asyncio
import functools
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from starlette.responses import Response

from api.auth import get_current_user, require_admin
from services.feedback_log import export_feedback_csv, log_feedback
from services.telemetry import log_event

router = APIRouter(prefix="/feedback", tags=["feedback"])

_VALID_TYPES = {"答案錯誤", "系統功能異常"}


class FeedbackRequest(BaseModel):
    type: str
    content: str
    conv_id: Optional[str] = None  # 前端目前開啟的對話；用於自動帶入最後一組問答


class RatingRequest(BaseModel):
    conv_id: str
    message_index: int  # 該對話 messages 陣列中的索引（後端在 done/preset 回應提供）
    rating: str  # "up" | "down"
    # 該訊息的 timestamp（伺服器產生，對話內唯一）。index 因 Firestore
    # 超限裁切位移時，以此尋回正確訊息；亦寫入 GCS 記錄供 3-2 去重。
    message_ts: Optional[str] = None


def _last_qa(store, user_sub: str, conv_id: str) -> tuple[str, str]:
    """取該對話最後一則 assistant 答案與其對應的 user 問題；取不到回空字串。

    帶入失敗絕不影響回饋主流程（回饋本身比附帶資訊重要）。
    """
    try:
        state = store.get(user_sub)
        conv = state.get("conversations", {}).get(conv_id)
        if conv is None or not conv.messages:
            return "", ""
        msgs = conv.messages
        ai_idx = next(
            (i for i in range(len(msgs) - 1, -1, -1) if msgs[i].role == "assistant"), None
        )
        if ai_idx is None:
            return "", ""
        answer = msgs[ai_idx].content
        question = next(
            (msgs[i].content for i in range(ai_idx - 1, -1, -1) if msgs[i].role == "user"), ""
        )
        return question, answer
    except Exception as exc:
        log_event("feedback_qa_capture_failed", error=str(exc)[:200])
        return "", ""


@router.post("")
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    if body.type not in _VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"問題類型無效，請選擇：{'、'.join(_VALID_TYPES)}",
        )
    if not body.content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="回饋內容不得為空",
        )

    settings = request.app.state.settings
    user_sub = user.get("sub", "unknown")

    question, answer = ("", "")
    if body.conv_id:
        question, answer = _last_qa(request.app.state.session_store, user_sub, body.conv_id)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        functools.partial(
            log_feedback,
            body.type,
            body.content.strip(),
            user_sub,
            settings,
            question=question,
            answer=answer,
        ),
    )
    return {"ok": True}


@router.post("/rating")
async def submit_rating(
    body: RatingRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """訊息級 👍👎 評分：更新該則訊息的 rating 並寫入回饋 log（type=讚/倒讚）。

    重複點擊可改變評分（up ↔ down）；GCS 為 append-only，以 ts 最新者為準。
    """
    if body.rating not in ("up", "down"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="rating 必須為 up 或 down",
        )

    store = request.app.state.session_store
    settings = request.app.state.settings
    user_sub = user.get("sub", "unknown")

    state = store.get(user_sub)
    conv = state.get("conversations", {}).get(body.conv_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="對話不存在")

    msgs = conv.messages
    msg = None
    idx = body.message_index
    if (
        0 <= idx < len(msgs)
        and msgs[idx].role == "assistant"
        and (body.message_ts is None or msgs[idx].timestamp == body.message_ts)
    ):
        msg = msgs[idx]
    elif body.message_ts:
        # index 失準（如 Firestore 超限裁切造成位移）：以 timestamp 尋回
        for i, m in enumerate(msgs):
            if m.role == "assistant" and m.timestamp == body.message_ts:
                msg, idx = m, i
                break
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="訊息不存在或非系統回答"
        )

    msg.rating = body.rating
    store.save(user_sub, state)

    question = next(
        (msgs[i].content for i in range(idx - 1, -1, -1) if msgs[i].role == "user"),
        "",
    )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        functools.partial(
            log_feedback,
            "讚" if body.rating == "up" else "倒讚",
            "",
            user_sub,
            settings,
            question=question,
            answer=msg.content,
            extra={"conv_id": body.conv_id, "message_ts": msg.timestamp},
        ),
    )
    return {"ok": True}


@router.get("/export")
async def export_feedback(
    request: Request,
    _user: dict = Depends(require_admin),
):
    settings = request.app.state.settings

    if not settings.feedback_log_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未設定意見回饋 bucket（FEEDBACK_LOG_BUCKET）",
        )

    loop = asyncio.get_running_loop()
    try:
        csv_bytes = await loop.run_in_executor(
            None, export_feedback_csv, settings
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        trace_id = uuid.uuid4().hex[:8]
        log_event("feedback_export_failed", severity="ERROR", trace_id=trace_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"匯出失敗，請洽系統管理員（trace: {trace_id}）",
        )

    filename = f"feedback_export_{date.today()}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
