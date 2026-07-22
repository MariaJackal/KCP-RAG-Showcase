from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException, status
from jose import jwt

from api.auth import _get_secret_key
from tests_api.conftest import auth_header


# ---------------------------------------------------------------------------
# 密碼登入測試
# ---------------------------------------------------------------------------

def test_password_login_user_role(client):
    """送正確 APP_PASSWORD → 200、role=user、token 含 sub。"""
    response = client.post("/api/auth/login", json={"password": "testpw"})
    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "user"
    assert isinstance(data["token"], str)
    payload = jwt.decode(data["token"], _get_secret_key(), algorithms=["HS256"])
    assert payload.get("sub")


def test_password_login_admin_role(client):
    """送正確 ADMIN_PASSWORD → 200、role=admin。"""
    response = client.post("/api/auth/login", json={"password": "adminpw"})
    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "admin"


def test_password_login_wrong_password(client):
    """送錯密碼 → 401。"""
    response = client.post("/api/auth/login", json={"password": "wrongpw"})
    assert response.status_code == 401


def test_password_login_unique_sub(client):
    """連續兩次登入，兩個 token 的 sub 不同（驗證匿名 UUID 隔離）。"""
    r1 = client.post("/api/auth/login", json={"password": "testpw"})
    r2 = client.post("/api/auth/login", json={"password": "testpw"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    sub1 = jwt.decode(r1.json()["token"], _get_secret_key(), algorithms=["HS256"])["sub"]
    sub2 = jwt.decode(r2.json()["token"], _get_secret_key(), algorithms=["HS256"])["sub"]
    assert sub1 != sub2


def _fake_google_claims(email="testuser@example.com", sub="google-sub-123"):
    return {"sub": sub, "email": email, "email_verified": True, "aud": "test-client-id", "iss": "accounts.google.com"}


def test_google_login_user_role(client):
    """一般 email 登入後取得 user role。"""
    with patch("api.routes.auth_routes.verify_google_id_token", return_value=_fake_google_claims()):
        with patch.object(client.app.state, "settings") as mock_settings:
            mock_settings.google_client_id = "test-client-id"
            mock_settings.admin_emails = "admin@example.com"
            response = client.post("/api/auth/google", json={"credential": "fake-id-token"})

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data.get("token"), str)
    assert data["role"] == "user"


def test_google_login_admin_role(client):
    """admin_emails 內的 email 登入後取得 admin role。"""
    with patch("api.routes.auth_routes.verify_google_id_token",
               return_value=_fake_google_claims(email="kawas4ki.z2@gmail.com")):
        with patch.object(client.app.state, "settings") as mock_settings:
            mock_settings.google_client_id = "test-client-id"
            mock_settings.admin_emails = "kawas4ki.z2@gmail.com"
            response = client.post("/api/auth/google", json={"credential": "fake-id-token"})

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data.get("token"), str)
    assert data["role"] == "admin"


def test_google_login_disabled_when_no_client_id(client):
    """`GOOGLE_CLIENT_ID` 未設定時回 501。"""
    with patch.object(client.app.state, "settings") as mock_settings:
        mock_settings.google_client_id = ""
        mock_settings.admin_emails = ""
        response = client.post("/api/auth/google", json={"credential": "fake-id-token"})

    assert response.status_code == 501


def test_google_login_invalid_token(client):
    """id_token 驗證失敗時回 401。"""
    _401 = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google 身分驗證失敗")
    with patch("api.routes.auth_routes.verify_google_id_token", side_effect=_401):
        with patch.object(client.app.state, "settings") as mock_settings:
            mock_settings.google_client_id = "test-client-id"
            mock_settings.admin_emails = ""
            response = client.post("/api/auth/google", json={"credential": "bad-token"})

    assert response.status_code == 401


def test_auth_config_returns_client_id(client):
    """/auth/config 回傳 google_client_id。"""
    with patch.object(client.app.state, "settings") as mock_settings:
        mock_settings.google_client_id = "my-client-id.apps.googleusercontent.com"
        response = client.get("/api/auth/config")

    assert response.status_code == 200
    assert response.json()["google_client_id"] == "my-client-id.apps.googleusercontent.com"


def test_access_without_token(client):
    response = client.get("/api/personas")
    assert response.status_code in (401, 403)


def test_access_with_valid_token(client, user_token):
    response = client.get("/api/personas", headers=auth_header(user_token))

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert {"id", "display_name", "icon"}.issubset(data[0].keys())


def test_access_with_expired_token(client):
    payload = {
        "sub": "user",
        "role": "user",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
    }
    token = jwt.encode(payload, _get_secret_key(), algorithm="HS256")

    response = client.get("/api/personas", headers=auth_header(token))
    assert response.status_code == 401


def test_admin_endpoint_with_user_token(client, user_token):
    response = client.get("/api/documents", headers=auth_header(user_token))

    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理員權限"
