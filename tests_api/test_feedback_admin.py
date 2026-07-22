"""回饋審核後台（3-2）：列表、去重、過濾、標註的整合測試。"""

import json
from unittest.mock import MagicMock, patch

from tests_api.conftest import auth_header


def _make_blob(name, payload):
    blob = MagicMock()
    blob.name = name
    blob.download_as_text.return_value = json.dumps(payload, ensure_ascii=False)
    return blob


def _rating_blob(uuid_hex, ts, rating_type, conv_id="c1", message_ts="mt1",
                 question="問", answer="答"):
    return _make_blob(
        f"feedback/2026-07-19/{uuid_hex}.json",
        {"ts": ts, "type": rating_type, "content": "", "user_sub": "user-x",
         "question": question, "answer": answer,
         "conv_id": conv_id, "message_ts": message_ts},
    )


def _review_blob(record_id, correct_laws="處罰條例 第35條"):
    return _make_blob(
        f"reviews/{record_id.rsplit('/', 1)[-1]}",
        {"record_id": record_id, "correct_laws": correct_laws,
         "category": "酒駕", "note": "", "reviewer_sub": "admin",
         "reviewed_at": "2026-07-20T00:00:00+00:00"},
    )


def _mock_storage(feedback_blobs, review_blobs=()):
    """依 prefix 分流的 mock：feedback/ 與 reviews/ 各回對應清單。"""
    mock_bucket = MagicMock()

    def list_blobs(prefix=""):
        if prefix == "feedback/":
            return iter(feedback_blobs)
        if prefix == "reviews/":
            return iter(review_blobs)
        return iter([])

    mock_bucket.list_blobs.side_effect = list_blobs
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    return mock_client, mock_bucket


def _mock_settings(bucket="test-bucket"):
    s = MagicMock()
    s.feedback_log_bucket = bucket
    s.project_id = "test-project"
    return s


UUID_A = "a" * 32
UUID_B = "b" * 32
UUID_C = "c" * 32


# ── 權限 ────────────────────────────────────────────────────────────

def test_list_requires_admin(client, user_token):
    res = client.get("/api/feedback/admin/records", headers=auth_header(user_token))
    assert res.status_code == 403


def test_review_requires_admin(client, user_token):
    res = client.post(
        "/api/feedback/admin/review",
        json={"record_id": f"feedback/2026-07-19/{UUID_A}.json"},
        headers=auth_header(user_token),
    )
    assert res.status_code == 403


# ── 列表 ────────────────────────────────────────────────────────────

def test_list_no_bucket_returns_400(client, admin_token):
    with patch.object(client.app.state, "settings", _mock_settings(bucket="")):
        res = client.get(
            "/api/feedback/admin/records", headers=auth_header(admin_token)
        )
    assert res.status_code == 400


def test_list_empty(client, admin_token):
    mock_client, _ = _mock_storage([])
    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res = client.get(
                "/api/feedback/admin/records", headers=auth_header(admin_token)
            )
    assert res.status_code == 200
    assert res.json() == {"total": 0, "page": 1, "page_size": 20, "items": []}


def test_list_rating_dedup_latest_wins(client, admin_token):
    """同一則訊息改評（倒讚→讚）：只呈現 ts 最新的最終狀態。"""
    blobs = [
        _rating_blob(UUID_A, "2026-07-19T10:00:00+00:00", "倒讚"),
        _rating_blob(UUID_B, "2026-07-19T11:00:00+00:00", "讚"),
    ]
    mock_client, _ = _mock_storage(blobs)
    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res = client.get(
                "/api/feedback/admin/records", headers=auth_header(admin_token)
            )
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["type"] == "讚"
    assert data["items"][0]["record_id"] == f"feedback/2026-07-19/{UUID_B}.json"


def test_list_type_filter_and_sidebar_records_kept(client, admin_token):
    """type=倒讚 只回倒讚；側欄表單記錄（無 conv_id）不參與去重。"""
    blobs = [
        _rating_blob(UUID_A, "2026-07-19T10:00:00+00:00", "倒讚"),
        _rating_blob(UUID_B, "2026-07-19T11:00:00+00:00", "讚", conv_id="c2",
                     message_ts="mt2"),
        _make_blob(
            f"feedback/2026-07-19/{UUID_C}.json",
            {"ts": "2026-07-19T12:00:00+00:00", "type": "答案錯誤",
             "content": "答案不對", "user_sub": "user-x"},
        ),
    ]
    mock_client, _ = _mock_storage(blobs)
    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res = client.get(
                "/api/feedback/admin/records?type=倒讚",
                headers=auth_header(admin_token),
            )
            res_all = client.get(
                "/api/feedback/admin/records", headers=auth_header(admin_token)
            )
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["type"] == "倒讚"
    assert res_all.json()["total"] == 3  # 排序 ts 降冪
    assert [r["type"] for r in res_all.json()["items"]] == ["答案錯誤", "讚", "倒讚"]


def test_list_review_merge_and_unreviewed_filter(client, admin_token):
    record_id = f"feedback/2026-07-19/{UUID_A}.json"
    blobs = [
        _rating_blob(UUID_A, "2026-07-19T10:00:00+00:00", "倒讚"),
        _rating_blob(UUID_B, "2026-07-19T11:00:00+00:00", "倒讚", conv_id="c2",
                     message_ts="mt2"),
    ]
    mock_client, _ = _mock_storage(blobs, [_review_blob(record_id)])
    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res_all = client.get(
                "/api/feedback/admin/records", headers=auth_header(admin_token)
            )
            res_un = client.get(
                "/api/feedback/admin/records?unreviewed=true",
                headers=auth_header(admin_token),
            )
    items = {r["record_id"]: r for r in res_all.json()["items"]}
    assert items[record_id]["review"]["correct_laws"] == "處罰條例 第35條"
    assert items[f"feedback/2026-07-19/{UUID_B}.json"]["review"] is None
    assert res_un.json()["total"] == 1
    assert res_un.json()["items"][0]["record_id"] == f"feedback/2026-07-19/{UUID_B}.json"


def test_list_pagination(client, admin_token):
    blobs = [
        _rating_blob(f"{i:032x}", f"2026-07-19T1{i}:00:00+00:00", "倒讚",
                     conv_id=f"c{i}", message_ts=f"mt{i}")
        for i in range(3)
    ]
    mock_client, _ = _mock_storage(blobs)
    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res = client.get(
                "/api/feedback/admin/records?page=2&page_size=2",
                headers=auth_header(admin_token),
            )
    data = res.json()
    assert data["total"] == 3
    assert len(data["items"]) == 1  # 第二頁只剩 1 筆（ts 降冪最舊者）
    assert data["items"][0]["conv_id"] == "c0"


# ── 標註 ────────────────────────────────────────────────────────────

def test_save_review_writes_blob(client, admin_token):
    record_id = f"feedback/2026-07-19/{UUID_A}.json"
    mock_client, mock_bucket = _mock_storage([])
    mock_bucket.blob.return_value.exists.return_value = True

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res = client.post(
                "/api/feedback/admin/review",
                json={"record_id": record_id, "correct_laws": "處罰條例 第35條",
                      "category": "酒駕", "note": "缺刑責"},
                headers=auth_header(admin_token),
            )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    # 寫到 reviews/{uuid}.json，payload 含標註欄位與 reviewer
    mock_bucket.blob.assert_any_call(f"reviews/{UUID_A}.json")
    uploaded = json.loads(
        mock_bucket.blob.return_value.upload_from_string.call_args[0][0]
    )
    assert uploaded["record_id"] == record_id
    assert uploaded["correct_laws"] == "處罰條例 第35條"
    assert uploaded["category"] == "酒駕"
    assert uploaded["note"] == "缺刑責"
    assert uploaded["reviewer_sub"]
    assert uploaded["reviewed_at"]


def test_save_review_invalid_record_id(client, admin_token):
    mock_client, _ = _mock_storage([])
    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res = client.post(
                "/api/feedback/admin/review",
                json={"record_id": "reviews/../evil.json"},
                headers=auth_header(admin_token),
            )
    assert res.status_code == 422


def test_save_review_record_not_found(client, admin_token):
    mock_client, mock_bucket = _mock_storage([])
    mock_bucket.blob.return_value.exists.return_value = False
    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings()):
            res = client.post(
                "/api/feedback/admin/review",
                json={"record_id": f"feedback/2026-07-19/{UUID_A}.json"},
                headers=auth_header(admin_token),
            )
    assert res.status_code == 404


def test_save_review_no_bucket_returns_400(client, admin_token):
    with patch.object(client.app.state, "settings", _mock_settings(bucket="")):
        res = client.post(
            "/api/feedback/admin/review",
            json={"record_id": f"feedback/2026-07-19/{UUID_A}.json"},
            headers=auth_header(admin_token),
        )
    assert res.status_code == 400
