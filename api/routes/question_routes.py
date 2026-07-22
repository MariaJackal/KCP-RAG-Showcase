"""Question export route (admin only)."""

import asyncio
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import Response

from api.auth import require_admin
from services.telemetry import log_event
from services.question_log import export_questions_csv

router = APIRouter(prefix="/questions", tags=["questions"])


@router.get("/export")
async def export_questions(
    request: Request,
    _user: dict = Depends(require_admin),
):
    settings = request.app.state.settings

    if not settings.question_log_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未設定提問記錄 bucket（QUESTION_LOG_BUCKET）",
        )

    loop = asyncio.get_running_loop()
    try:
        csv_bytes = await loop.run_in_executor(
            None, export_questions_csv, settings
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        trace_id = uuid.uuid4().hex[:8]
        log_event("question_export_failed", severity="ERROR", trace_id=trace_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"匯出失敗，請洽系統管理員（trace: {trace_id}）",
        )

    filename = f"questions_export_{date.today()}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
