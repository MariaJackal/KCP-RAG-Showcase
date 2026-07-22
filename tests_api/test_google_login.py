"""#1: Google 登入必須檢查 email_verified，未驗證 email 不得提權。"""

from unittest.mock import patch

from config import Settings


def _settings_google(admin_emails="admin@corp.example"):
    return Settings(
        project_id="p", data_store_id="d", location="global",
        vertex_init_location="us-central1", app_password="testpw",
        google_client_id="client-123.apps.googleusercontent.com",
        admin_emails=admin_emails,
    )


def test_verified_admin_email_gets_admin(client):
    claims = {"sub": "g1", "email": "admin@corp.example", "email_verified": True}
    with patch("api.routes.auth_routes.verify_google_id_token", return_value=claims):
        with patch.object(client.app.state, "settings", _settings_google()):
            r = client.post("/api/auth/google", json={"credential": "x"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_unverified_admin_email_denied_admin(client):
    claims = {"sub": "g1", "email": "admin@corp.example", "email_verified": False}
    with patch("api.routes.auth_routes.verify_google_id_token", return_value=claims):
        with patch.object(client.app.state, "settings", _settings_google()):
            r = client.post("/api/auth/google", json={"credential": "x"})
    assert r.status_code == 200
    assert r.json()["role"] == "user"  # 未驗證 email → 不得提權


def test_missing_email_verified_treated_as_unverified(client):
    claims = {"sub": "g1", "email": "admin@corp.example"}  # 無 email_verified
    with patch("api.routes.auth_routes.verify_google_id_token", return_value=claims):
        with patch.object(client.app.state, "settings", _settings_google()):
            r = client.post("/api/auth/google", json={"credential": "x"})
    assert r.status_code == 200
    assert r.json()["role"] == "user"
