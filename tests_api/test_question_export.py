"""G4: 管理員匯出提問 CSV 的整合測試。"""

import json
from unittest.mock import MagicMock, patch

from tests_api.conftest import auth_header


def _make_blob(ts, question, user_sub):
    blob = MagicMock()
    blob.download_as_text.return_value = json.dumps(
        {"ts": ts, "question": question, "user_sub": user_sub}
    )
    return blob


def _mock_storage_with_blobs(blobs):
    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = iter(blobs)
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    return mock_client


def _mock_settings(bucket):
    """回傳帶有指定 question_log_bucket 的 mock settings。"""
    s = MagicMock()
    s.question_log_bucket = bucket
    s.project_id = "test-project"
    return s


def test_export_questions_admin_returns_csv(client, admin_token):
    """admin 呼叫匯出端點，回 200 CSV 含表頭與提問資料。"""
    blobs = [
        _make_blob("2026-06-04T01:00:00+00:00", "酒駕罰則", "user-a"),
        _make_blob("2026-06-04T02:00:00+00:00", "闖紅燈處罰", "user-b"),
    ]
    mock_client = _mock_storage_with_blobs(blobs)

    with patch("services.question_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.get("/api/questions/export", headers=auth_header(admin_token))

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]

    text = response.content.decode("utf-8-sig")
    assert "時間" in text
    assert "問題" in text
    assert "使用者" in text
    assert "酒駕罰則" in text
    assert "闖紅燈處罰" in text


def test_export_questions_user_forbidden(client, user_token):
    """一般 user 呼叫匯出端點，回 403。"""
    response = client.get("/api/questions/export", headers=auth_header(user_token))
    assert response.status_code == 403


def test_export_questions_no_bucket_returns_400(client, admin_token):
    """`QUESTION_LOG_BUCKET` 未設定時，回 400 並含說明訊息。"""
    with patch.object(client.app.state, "settings", _mock_settings("")):
        response = client.get("/api/questions/export", headers=auth_header(admin_token))

    assert response.status_code == 400
    assert "QUESTION_LOG_BUCKET" in response.json()["detail"]


def test_export_questions_empty_bucket_returns_header_only(client, admin_token):
    """bucket 存在但無任何 blob，回 200 且 CSV 只有表頭。"""
    mock_client = _mock_storage_with_blobs([])

    with patch("services.question_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.get("/api/questions/export", headers=auth_header(admin_token))

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == 1  # 只有表頭
    assert "時間" in lines[0]
