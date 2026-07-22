"""Authentication routes."""

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from api.auth import create_google_token, create_password_token, verify_google_id_token
from api.schemas import LoginResponse
from services.rate_limit import RateLimiter, client_ip
from services.telemetry import log_event

router = APIRouter(prefix="/auth", tags=["auth"])

# 以 IP 為主的登入失敗速率限制：同 IP 每分鐘最多 5 次失敗，逾限回 429。
_LOGIN_FAIL_LIMITER = RateLimiter(max_events=5, window_seconds=60)


class PasswordLoginRequest(BaseModel):
    password: str


class GoogleLoginRequest(BaseModel):
    credential: str  # Google id_token from GIS


async def _verify_password(submitted: str, stored: str) -> bool:
    """常數時間比對 + 非阻塞延遲（防暴力破解，沿用 Wave 2.2 模式）。"""
    await asyncio.sleep(0.1)
    if not stored:
        return False
    return hmac.compare_digest(submitted, stored)


@router.post("/login", response_model=LoginResponse)
async def password_login(body: PasswordLoginRequest, request: Request):
    settings = request.app.state.settings
    # admin 密碼優先（admin_password 存在且相符 → admin role）
    if settings.admin_password and await _verify_password(body.password, settings.admin_password):
        return LoginResponse(token=create_password_token(role="admin"), role="admin")
    if await _verify_password(body.password, settings.app_password):
        return LoginResponse(token=create_password_token(role="user"), role="user")

    # 登入失敗才計數；同 IP 短時間內失敗過多即封鎖，防線上暴力破解。
    ip = client_ip(request)
    if not _LOGIN_FAIL_LIMITER.hit(ip):
        log_event("login_rate_limited", severity="WARNING", ip=ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登入失敗次數過多，請稍後再試",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="密碼錯誤")


@router.get("/config")
async def auth_config(request: Request):
    """回傳前端初始化所需的非機密設定（Google client_id）。"""
    settings = request.app.state.settings
    return {"google_client_id": settings.google_client_id or None}


@router.post("/google", response_model=LoginResponse)
async def google_login(body: GoogleLoginRequest, request: Request):
    settings = request.app.state.settings

    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google 登入未啟用（缺少 GOOGLE_CLIENT_ID 設定）",
        )

    claims = verify_google_id_token(body.credential, settings.google_client_id)
    sub = claims["sub"]
    email = claims.get("email", "")
    # 未驗證的 email 不得用於任何信任判斷（避免聯邦/自訂網域偽造 email 提權）
    if not claims.get("email_verified", False):
        email = ""

    # Determine role: check email against admin allowlist
    admin_emails = [e.strip() for e in settings.admin_emails.split(",") if e.strip()]
    role = "admin" if email and email in admin_emails else "user"

    token = create_google_token(sub=sub, email=email, role=role)
    return LoginResponse(token=token, role=role)
