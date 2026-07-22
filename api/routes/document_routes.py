"""Document management routes (admin only)."""

import asyncio
import re
import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from google.api_core.exceptions import PermissionDenied
from google.auth.exceptions import DefaultCredentialsError

from api.auth import require_admin
from services.telemetry import log_event
from services.document_service import (
    _branch_path,
    delete_document,
    get_document_list_detailed,
    import_document_from_gcs,
)
from services.storage_service import upload_to_gcs

router = APIRouter(prefix="/documents", tags=["documents"])

# 允許的檔名字元：英數、底線、連字號、點、中文；其餘一律換成底線。
_SAFE_NAME_RE = re.compile(r"[^\w.\-一-鿿]")


def _safe_blob_name(filename: str) -> str:
    """去除路徑成分並過濾危險字元，加 UUID 前綴避免覆寫既有物件。"""
    base = PurePosixPath(filename).name  # 去掉 / 與 .. 等路徑成分
    safe = _SAFE_NAME_RE.sub("_", base).lstrip(".") or "document.pdf"
    return f"{uuid.uuid4().hex[:8]}_{safe}"


def _raise_upload_error(exc: Exception):
    if isinstance(exc, DefaultCredentialsError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GCP 認證失敗，請先設定 Application Default Credentials (ADC)",
        )
    if isinstance(exc, PermissionDenied):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GCP 權限不足，請確認 GCS 與 Discovery Engine 的 IAM 權限",
        )
    trace_id = uuid.uuid4().hex[:8]
    log_event("document_upload_failed", severity="ERROR", trace_id=trace_id, error=str(exc))
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"文件上傳失敗，請洽系統管理員（trace: {trace_id}）",
    )


@router.get("")
async def list_documents(request: Request, _user: dict = Depends(require_admin)):
    settings = request.app.state.settings
    client = request.app.state.document_client

    loop = asyncio.get_running_loop()
    docs = await loop.run_in_executor(
        None, get_document_list_detailed, settings, client
    )
    return docs


@router.post("/upload", status_code=201)
async def upload_document(
    file: UploadFile,
    request: Request,
    _user: dict = Depends(require_admin),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="僅支援 PDF 檔案",
        )

    settings = request.app.state.settings
    if not settings.gcs_staging_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GCS staging bucket 未設定",
        )

    file_bytes = await file.read()
    if not file_bytes.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="檔案內容不是有效的 PDF",
        )
    document_client = request.app.state.document_client
    loop = asyncio.get_running_loop()

    safe_name = _safe_blob_name(file.filename)
    try:
        # Upload to GCS with a sanitized blob name (path-stripped + UUID prefix)
        gcs_uri = await loop.run_in_executor(
            None, upload_to_gcs, file_bytes, safe_name, settings
        )
    except Exception as e:
        _raise_upload_error(e)

    try:
        # Trigger Discovery Engine import (async LRO)
        op_name = await loop.run_in_executor(
            None, import_document_from_gcs, gcs_uri, settings, document_client
        )
    except Exception as e:
        _raise_upload_error(e)

    return {"gcs_uri": gcs_uri, "operation": op_name}


@router.delete("/{doc_id:path}", status_code=204)
async def remove_document(
    doc_id: str,
    request: Request,
    _user: dict = Depends(require_admin),
):
    settings = request.app.state.settings
    client = request.app.state.document_client
    loop = asyncio.get_running_loop()

    # 僅允許刪除本專案 data store branch 底下的文件（防 confused deputy 越權刪除）。
    if not doc_id.startswith(_branch_path(settings) + "/documents/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件資源名稱不在允許範圍內",
        )

    try:
        await loop.run_in_executor(None, delete_document, doc_id, settings, client)
    except Exception as e:
        trace_id = uuid.uuid4().hex[:8]
        log_event("document_delete_failed", severity="ERROR",
                  trace_id=trace_id, doc_id=doc_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"刪除失敗，請洽系統管理員（trace: {trace_id}）",
        )
