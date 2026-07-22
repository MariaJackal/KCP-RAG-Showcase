"""FastAPI application entry point."""

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import api.deps
from api.routes.auth_routes import router as auth_router
from api.routes.chat_routes import router as chat_router
from api.routes.conversation_routes import router as conversation_router
from api.routes.document_routes import router as document_router
from api.routes.persona_routes import router as persona_router
from api.routes.feedback_admin_routes import router as feedback_admin_router
from api.routes.feedback_routes import router as feedback_router
from api.routes.question_routes import router as question_router

STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "static"


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds per-path limits.

    Chat endpoints: 1 MB. Document upload: 20 MB. All others: 1 MB.
    A body-bearing request that omits Content-Length (e.g. Transfer-Encoding:
    chunked) can bypass the size check, so such requests are rejected with 411.
    """
    _MAX_CHAT = 1_048_576      # 1 MB
    _MAX_UPLOAD = 20_971_520   # 20 MB
    _BODY_METHODS = {"POST", "PUT", "PATCH"}

    async def dispatch(self, request, call_next):
        cl = request.headers.get("content-length")
        if cl is None or not cl.isdigit():
            # Body-bearing methods must declare a numeric Content-Length so the
            # size limit is enforceable; reject chunked / length-less bodies.
            if request.method in self._BODY_METHODS:
                return JSONResponse(
                    {"detail": "缺少 Content-Length，未支援分塊傳輸"},
                    status_code=411,
                )
            return await call_next(request)
        limit = self._MAX_UPLOAD if "/documents/upload" in request.url.path else self._MAX_CHAT
        if int(cl) > limit:
            return JSONResponse(
                {"detail": f"請求過大（限制 {limit // 1024} KB）"},
                status_code=413,
            )
        return await call_next(request)


_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'"
)

# 簡報頁（static/slides.html）為單一自包含 HTML，JS 全部行內：僅此路徑放行 inline script
_CSP_SLIDES = _CSP.replace("script-src 'self'; ", "script-src 'self' 'unsafe-inline'; ")


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add baseline security response headers to every response."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        csp = _CSP_SLIDES if request.url.path == "/static/slides.html" else _CSP
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    api.deps.init_runtime(app.state)
    yield


app = FastAPI(title="警政AI助手 API", version="3.0", lifespan=lifespan)
app.add_middleware(_BodySizeLimitMiddleware)
app.add_middleware(_SecurityHeadersMiddleware)

# --- API routes ---
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(conversation_router, prefix="/api")
app.include_router(persona_router, prefix="/api")
app.include_router(document_router, prefix="/api")
app.include_router(question_router, prefix="/api")
app.include_router(feedback_admin_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")

# --- Static files & SPA fallback ---
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")
