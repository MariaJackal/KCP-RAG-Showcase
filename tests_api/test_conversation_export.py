"""管理員匯出對話記錄 CSV 的整合測試。"""

from unittest.mock import patch

from tests_api.conftest import auth_header


def test_export_conversations_admin_returns_csv(client, admin_token):
    """admin 呼叫匯出端點，回 200 CSV。"""
    fake_csv = "使用者ID,對話標題,對話建立時間,使用者問題,系統答案,答案時間\r\n".encode("utf-8-sig")
    with patch(
        "api.routes.conversation_routes.export_conversations_csv",
        return_value=fake_csv,
    ):
        response = client.get("/api/conversations/export", headers=auth_header(admin_token))

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "conversations_export_" in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")
    assert "使用者問題" in text and "系統答案" in text


def test_export_conversations_user_forbidden(client, user_token):
    """一般 user 呼叫匯出端點，回 403。"""
    response = client.get("/api/conversations/export", headers=auth_header(user_token))
    assert response.status_code == 403


def test_export_conversations_failure_returns_502(client, admin_token):
    """Firestore 讀取失敗時，回 502 並含 trace。"""
    with patch(
        "api.routes.conversation_routes.export_conversations_csv",
        side_effect=RuntimeError("boom"),
    ):
        response = client.get("/api/conversations/export", headers=auth_header(admin_token))

    assert response.status_code == 502
    assert "trace" in response.json()["detail"]
