"""Chat route: SSE-streamed RAG pipeline execution."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import StreamingResponse

from api.auth import get_current_user
from api.schemas import AskRequest, PresetRequest
from models import Message
from personas import get_persona
from services.answer_formatter import link_law_mentions
from presets import get_preset, list_presets
from services.pipeline import run_rag_pipeline
from services.question_log import log_question
from services.rate_limit import RateLimiter, client_ip
from services.session_manager import add_message
from services.telemetry import log_event

router = APIRouter(prefix="/conversations", tags=["chat"])

HEARTBEAT_INTERVAL = 5  # seconds

# /ask 每 sub + 每 IP 限流：每分鐘最多 20 題，防腳本連發放大 GCP 成本與排擠他人。
_ASK_LIMITER = RateLimiter(max_events=20, window_seconds=60)


def _user_id(user: dict) -> str:
    return user.get("sub", "user")


def _get_store(request: Request):
    return request.app.state.session_store


@router.post("/{conv_id}/ask")
async def ask(
    conv_id: str,
    body: AskRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    store = _get_store(request)
    uid = _user_id(user)

    # 每 sub + 每 IP 限流，逾限回 429（防成本放大與服務排擠）。
    if not _ASK_LIMITER.hit(f"{uid}:{client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="提問過於頻繁，請稍後再試",
        )

    state = store.get(uid)

    conv = state.get("conversations", {}).get(conv_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")

    # Point active conversation first so add_message targets the right one
    state["active_conversation_id"] = conv_id
    # Save user message immediately
    add_message(state, "user", body.question)
    store.save(uid, state)

    app_state = request.app.state
    persona = get_persona(conv.persona_id)

    async def sse_stream():
        loop = asyncio.get_running_loop()
        progress_queue: asyncio.Queue = asyncio.Queue()
        token_queue: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str):
            loop.call_soon_threadsafe(progress_queue.put_nowait, msg)

        def on_token(chunk: str):
            loop.call_soon_threadsafe(token_queue.put_nowait, chunk)

        future = loop.run_in_executor(
            None,
            _run_pipeline_sync,
            body.question,
            persona,
            conv,
            app_state,
            on_progress,
            on_token,
        )

        # Stream progress + token events
        while not future.done():
            # Drain token queue first (higher priority for responsiveness)
            while not token_queue.empty():
                chunk = token_queue.get_nowait()
                yield _sse_event("token", {"text": chunk})
            try:
                msg = progress_queue.get_nowait()
                yield _sse_event("progress", msg if isinstance(msg, dict) else {"message": msg})
            except asyncio.QueueEmpty:
                pass
            await asyncio.sleep(0.05)

        # Drain remaining tokens and progress
        while not token_queue.empty():
            chunk = token_queue.get_nowait()
            yield _sse_event("token", {"text": chunk})
        while not progress_queue.empty():
            msg = progress_queue.get_nowait()
            yield _sse_event("progress", msg if isinstance(msg, dict) else {"message": msg})

        result = future.result()

        log_event(
            "qa_trace",
            user_sub=uid,
            conv_id=conv_id,
            persona_id=conv.persona_id,
            question_len=len(body.question),
            answer_len=len(result.answer) if result.answer else 0,
            intent=result.intent,
            latency_ms={k: round(v, 1) for k, v in result.stage_latency_ms.items()},
        )

        try:
            log_question(body.question, uid, app_state.settings)
        except Exception as exc:
            log_event("question_log_error", error=str(exc))

        # 確定性交叉索引：結論/注意事項提及的法條 ↔ 法規依據條目標同號 [n]
        # （純文字指引，不可點；Paul 2026-07-19 拍板的方向 C）
        final_answer, unmatched_laws = link_law_mentions(result.answer)
        if unmatched_laws:
            # 有提及、依據段無對應：定義條款引用/資料缺口/幻覺的監測訊號
            log_event(
                "conclusion_law_without_basis",
                request_id=result.request_id,
                question=body.question[:100],
                laws=[f"{law}第{art}{unit}" for law, unit, art in unmatched_laws],
            )

        # Save assistant message — 直接寫回發問的對話，不改動 active_conversation_id，
        # 避免生成期間使用者刪除該對話時留下懸空指標
        fresh_state = store.get(uid)
        fresh_conv = fresh_state.get("conversations", {}).get(conv_id)
        message_index = None  # 對話已被刪除時無索引，前端據此不顯示評分按鈕
        message_ts = None
        if fresh_conv is not None:
            saved_msg = Message(role="assistant", content=final_answer)
            fresh_conv.messages.append(saved_msg)
            store.save(uid, fresh_state)
            message_index = len(fresh_conv.messages) - 1
            message_ts = saved_msg.timestamp

        yield _sse_event("done", {
            "answer": final_answer,
            "intent": result.intent,
            "message_index": message_index,
            "message_ts": message_ts,
            "latency_ms": {k: round(v, 1) for k, v in result.stage_latency_ms.items()},
            "grounding_score": result.grounding_score,
        })

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


def _run_pipeline_sync(question, persona, conv, app_state, progress_callback, stream_callback=None):
    return run_rag_pipeline(
        question=question,
        persona=persona,
        recent_messages=conv.messages[-12:],
        rewriter_model=app_state.rewriter_model,
        answer_model=app_state.answer_model,
        search_client=app_state.search_client,
        settings=app_state.settings,
        search_cache=app_state.search_cache,
        answer_cache=app_state.answer_cache,
        persona_id=conv.persona_id,
        progress_callback=progress_callback,
        stream_callback=stream_callback,
    )


def _sse_event(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/presets")
async def get_presets(_user: dict = Depends(get_current_user)):
    """回傳首頁按鈕清單（僅含 id 與 label）。"""
    return list_presets()


@router.post("/{conv_id}/preset")
async def ask_preset(
    conv_id: str,
    body: PresetRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """直接以預寫答案回應，不走 LLM pipeline。"""
    store = _get_store(request)
    uid = _user_id(user)
    state = store.get(uid)

    conv = state.get("conversations", {}).get(conv_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")

    preset = get_preset(body.preset_id)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="preset not found")

    state["active_conversation_id"] = conv_id
    add_message(state, "user", preset["question"])
    add_message(state, "assistant", preset["answer"])
    store.save(uid, state)

    log_event(
        "qa_trace_preset",
        user_sub=uid,
        conv_id=conv_id,
        preset_id=body.preset_id,
        question=preset["question"],
        answer=preset["answer"][:500],
    )

    return {
        "question": preset["question"],
        "answer": preset["answer"],
        "assistant_index": len(conv.messages) - 1,
        "assistant_ts": conv.messages[-1].timestamp,
    }
