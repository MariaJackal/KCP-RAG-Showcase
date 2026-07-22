"""Feedback admin routes: 回饋審核後台（3-2 回饋閉環）。

管理者檢視回饋記錄（含 👍👎 評分與側欄表單）、標註正確法條與分類，
標註結果寫回 GCS reviews/ 前綴，供 scripts/export_feedback_to_golden.py
匯出成 golden set 候選。
"""

import asyncio
import functools
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from api.auth import require_admin
from services.feedback_log import list_feedback_records, save_feedback_review
from services.telemetry import log_event

router = APIRouter(prefix="/feedback/admin", tags=["feedback-admin"])


class ReviewRequest(BaseModel):
    record_id: str
    correct_laws: str = ""  # 一行一條：「法規名稱 第N條」，匯出腳本解析
    category: str = ""
    note: str = ""


@router.get("/records")
async def list_records(
    request: Request,
    type: str = "",
    unreviewed: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_admin),
):
    settings = request.app.state.settings
    if not settings.feedback_log_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未設定意見回饋 bucket（FEEDBACK_LOG_BUCKET）",
        )

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None,
            functools.partial(
                list_feedback_records,
                settings,
                feedback_type=type,
                only_unreviewed=unreviewed,
                page=page,
                page_size=page_size,
            ),
        )
    except Exception as e:
        trace_id = uuid.uuid4().hex[:8]
        log_event(
            "feedback_admin_list_failed", severity="ERROR", trace_id=trace_id, error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"讀取回饋記錄失敗，請洽系統管理員（trace: {trace_id}）",
        )


@router.post("/review")
async def save_review(
    body: ReviewRequest,
    request: Request,
    user: dict = Depends(require_admin),
):
    settings = request.app.state.settings
    if not settings.feedback_log_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未設定意見回饋 bucket（FEEDBACK_LOG_BUCKET）",
        )

    review = {
        "correct_laws": body.correct_laws.strip(),
        "category": body.category.strip(),
        "note": body.note.strip(),
        "reviewer_sub": user.get("sub", "unknown"),
    }

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            functools.partial(save_feedback_review, settings, body.record_id, review),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        trace_id = uuid.uuid4().hex[:8]
        log_event(
            "feedback_admin_review_failed",
            severity="ERROR",
            trace_id=trace_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"儲存標註失敗，請洽系統管理員（trace: {trace_id}）",
        )
    return {"ok": True}
