"""API-layer tests for the 2026-07-05 security remediation (P1-1/3/4)."""

import asyncio
from unittest.mock import patch

from tests_api.conftest import auth_header


# --- P1-3: chunked / length-less body rejected with 411 ---

def _run_middleware(method, headers):
    """Drive _BodySizeLimitMiddleware.dispatch with a fake request; return the
    status code (411/413) or None when the request is passed through."""
    from api.main import _BodySizeLimitMiddleware

    mw = _BodySizeLimitMiddleware(app=None)

    class _FakeURL:
        path = "/api/conversations/x/ask"

    class _FakeReq:
        def __init__(self):
            self.method = method
            self.headers = headers
            self.url = _FakeURL()

    passed = {"called": False}

    async def _next(_req):
        passed["called"] = True
        return "OK"

    result = asyncio.run(mw.dispatch(_FakeReq(), _next))
    if passed["called"]:
        return None
    return result.status_code


def test_post_without_content_length_rejected_411():
    assert _run_middleware("POST", {}) == 411


def test_post_with_content_length_passes():
    assert _run_middleware("POST", {"content-length": "100"}) is None


def test_get_without_content_length_passes():
    assert _run_middleware("GET", {}) is None


def test_oversized_content_length_rejected_413():
    assert _run_middleware("POST", {"content-length": str(2_000_000)}) == 413


# --- P1-1: login brute-force rate limiting ---

def test_login_rate_limited_after_repeated_failures(client):
    import api.routes.auth_routes as mod

    mod._LOGIN_FAIL_LIMITER._events.clear()  # isolate from other tests

    # 5 allowed failures (401), the 6th within the window is 429
    for _ in range(5):
        r = client.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 429


def test_successful_login_not_counted(client):
    import api.routes.auth_routes as mod

    mod._LOGIN_FAIL_LIMITER._events.clear()

    # Many correct logins must never trip the limiter (only failures count).
    for _ in range(10):
        r = client.post("/api/auth/login", json={"password": "testpw"})
        assert r.status_code == 200


# --- P1-3: AskRequest.question max_length ---

def test_ask_rejects_overlong_question(client, user_token):
    # Create a conversation to target
    conv = client.post(
        "/api/conversations", json={"persona_id": "traffic"}, headers=auth_header(user_token)
    ).json()
    conv_id = conv["id"]

    r = client.post(
        f"/api/conversations/{conv_id}/ask",
        json={"question": "字" * 4001},
        headers=auth_header(user_token),
    )
    assert r.status_code == 422


# --- P1-4: upload filename sanitization ---

def test_upload_sanitizes_malicious_filename(client, admin_token):
    captured = {}

    def _fake_upload(file_bytes, filename, settings, storage_client=None):
        captured["filename"] = filename
        return f"gs://b/{filename}"

    with patch("api.routes.document_routes.upload_to_gcs", side_effect=_fake_upload):
        with patch("api.routes.document_routes.import_document_from_gcs", return_value="op-1"):
            r = client.post(
                "/api/documents/upload",
                files={"file": ("../../etc/passwd.pdf", b"%PDF-1.7\nx", "application/pdf")},
                headers=auth_header(admin_token),
            )
    assert r.status_code == 201
    name = captured["filename"]
    assert "/" not in name and ".." not in name
    assert name.endswith("passwd.pdf")  # basename preserved, path stripped
