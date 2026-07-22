"""JWT authentication utilities."""

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt

_ALGORITHM = "HS256"
_TOKEN_TTL_HOURS = 24

_bearer_scheme = HTTPBearer()


def _get_secret_key() -> str:
    key = os.getenv("JWT_SECRET_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise RuntimeError(
            "缺少必要設定: JWT_SECRET_KEY（不可與 APP_PASSWORD 共用，"
            "請另設 32+ 字元隨機字串：python -c \"import secrets; print(secrets.token_urlsafe(32))\"）"
        )
    return key


def assert_jwt_secret_safe(app_password: str) -> None:
    """啟動時檢查：JWT_SECRET_KEY 必須存在，且不得與 APP_PASSWORD 相同。

    若兩者相同，知道共用登入密碼者即可自簽 admin token。
    """
    key = _get_secret_key()  # 缺少時於此 raise
    if app_password and key == app_password:
        raise RuntimeError(
            "JWT_SECRET_KEY 不可與 APP_PASSWORD 相同，請另設獨立的隨機字串："
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )


def create_password_token(role: str) -> str:
    """簽發共用密碼登入的 JWT。sub 為隨機匿名 UUID，使各瀏覽器對話自動隔離。"""
    expire = datetime.now(timezone.utc) + timedelta(hours=_TOKEN_TTL_HOURS)
    payload = {"sub": uuid.uuid4().hex, "role": role, "exp": expire}
    return jwt.encode(payload, _get_secret_key(), algorithm=_ALGORITHM)


def create_google_token(sub: str, email: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=_TOKEN_TTL_HOURS)
    payload = {"sub": sub, "email": email, "role": role, "exp": expire}
    return jwt.encode(payload, _get_secret_key(), algorithm=_ALGORITHM)


def verify_google_id_token(credential: str, client_id: str) -> dict:
    """Verify a Google id_token and return its claims dict.

    Raises HTTPException 401 on invalid token.
    """
    try:
        claims = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), client_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google 身分驗證失敗: {exc}",
        )
    return claims


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _get_secret_key(), algorithms=[_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 無效或已過期",
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    return _decode_token(credentials.credentials)


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理員權限",
        )
    return user
