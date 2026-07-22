"""Conversation CRUD routes."""

import asyncio
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import Response

from api.auth import get_current_user, require_admin
from api.schemas import (
    ConversationSummary,
    CreateConversationRequest,
    MessageOut,
    PatchPersonaRequest,
)
from services.conversation_export import export_conversations_csv
from services.session_manager import (
    add_message,
    create_conversation,
    delete_conversation,
    get_active_conversation,
    get_sorted_conversations,
    init_session_state,
    switch_conversation,
)
from services.telemetry import log_event

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/export")
async def export_conversations(
    request: Request,
    _user: dict = Depends(require_admin),
):
    settings = request.app.state.settings

    loop = asyncio.get_running_loop()
    try:
        csv_bytes = await loop.run_in_executor(None, export_conversations_csv, settings)
    except Exception as e:
        trace_id = uuid.uuid4().hex[:8]
        log_event("conversation_export_failed", severity="ERROR", trace_id=trace_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"匯出失敗，請洽系統管理員（trace: {trace_id}）",
        )

    filename = f"conversations_export_{date.today()}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _user_id(user: dict) -> str:
    return user.get("sub", "user")


def _get_store(request: Request):
    return request.app.state.session_store


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    request: Request, user: dict = Depends(get_current_user)
):
    store = _get_store(request)
    state = store.get(_user_id(user))
    convs = get_sorted_conversations(state)
    return [
        ConversationSummary(
            id=c.id, title=c.title, persona_id=c.persona_id, created_at=c.created_at
        )
        for c in convs
    ]


@router.post("", response_model=ConversationSummary, status_code=201)
async def new_conversation(
    body: CreateConversationRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    store = _get_store(request)
    uid = _user_id(user)
    state = store.get(uid)
    conv = create_conversation(state, persona_id=body.persona_id)
    store.save(uid, state)
    return ConversationSummary(
        id=conv.id, title=conv.title, persona_id=conv.persona_id, created_at=conv.created_at
    )


@router.delete("/{conv_id}", status_code=204)
async def remove_conversation(
    conv_id: str, request: Request, user: dict = Depends(get_current_user)
):
    store = _get_store(request)
    uid = _user_id(user)
    state = store.get(uid)
    if conv_id not in state.get("conversations", {}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="對話不存在")
    delete_conversation(state, conv_id)
    store.save(uid, state)


@router.get("/{conv_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conv_id: str, request: Request, user: dict = Depends(get_current_user)
):
    store = _get_store(request)
    state = store.get(_user_id(user))
    conv = state.get("conversations", {}).get(conv_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="對話不存在")
    return [
        MessageOut(
            role=m.role,
            content=m.content,
            timestamp=m.timestamp,
            citations=getattr(m, "citations", []) or [],
            rating=getattr(m, "rating", "") or "",
        )
        for m in conv.messages
    ]


@router.patch("/{conv_id}/persona", status_code=204)
async def update_persona(
    conv_id: str,
    body: PatchPersonaRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    store = _get_store(request)
    uid = _user_id(user)
    state = store.get(uid)
    conv = state.get("conversations", {}).get(conv_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="對話不存在")
    conv.persona_id = body.persona_id
    store.save(uid, state)
