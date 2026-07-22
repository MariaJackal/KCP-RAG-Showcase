"""使用者意見回饋：送出（user）與匯出 CSV（admin）的整合測試。"""

import json
from unittest.mock import MagicMock, patch

from tests_api.conftest import auth_header


def _make_blob(ts, ftype, content):
    blob = MagicMock()
    blob.download_as_text.return_value = json.dumps(
        {"ts": ts, "type": ftype, "content": content, "user_sub": "user-x"}
    )
    return blob


def _mock_storage_with_blobs(blobs):
    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = iter(blobs)
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    return mock_client


def _mock_settings(bucket):
    """回傳帶有指定 feedback_log_bucket 的 mock settings。"""
    s = MagicMock()
    s.feedback_log_bucket = bucket
    s.project_id = "test-project"
    return s


# ── 送出 ────────────────────────────────────────────────────────────

def test_submit_feedback_writes_record(client, user_token):
    """合法送出回 200，並把一筆記錄上傳到 GCS。"""
    mock_client = _mock_storage_with_blobs([])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.post(
                "/api/feedback",
                json={"type": "答案錯誤", "content": "酒駕罰則答案有誤"},
                headers=auth_header(user_token),
            )

    assert response.status_code == 200
    mock_client.bucket.return_value.blob.assert_called_once()
    blob_name = mock_client.bucket.return_value.blob.call_args[0][0]
    assert blob_name.startswith("feedback/")
    assert blob_name.endswith(".json")


def test_submit_feedback_invalid_type_returns_422(client, user_token):
    """type 不在白名單內回 422。"""
    response = client.post(
        "/api/feedback",
        json={"type": "亂打的類型", "content": "內容"},
        headers=auth_header(user_token),
    )
    assert response.status_code == 422


def test_submit_feedback_empty_content_returns_422(client, user_token):
    """content 空白回 422。"""
    response = client.post(
        "/api/feedback",
        json={"type": "系統功能異常", "content": "   "},
        headers=auth_header(user_token),
    )
    assert response.status_code == 422


def test_submit_feedback_no_bucket_still_200(client, user_token):
    """未設定 bucket 時送出仍回 200（靜默跳過寫入，不報錯）。"""
    with patch.object(client.app.state, "settings", _mock_settings("")):
        response = client.post(
            "/api/feedback",
            json={"type": "答案錯誤", "content": "內容"},
            headers=auth_header(user_token),
        )
    assert response.status_code == 200


# ── 匯出 ────────────────────────────────────────────────────────────

def test_export_feedback_admin_returns_csv(client, admin_token):
    """admin 呼叫匯出端點，回 200 CSV 含表頭與回饋資料。"""
    blobs = [
        _make_blob("2026-06-17T01:00:00+00:00", "答案錯誤", "酒駕罰則答案有誤"),
        _make_blob("2026-06-17T02:00:00+00:00", "系統功能異常", "頁面打不開"),
    ]
    mock_client = _mock_storage_with_blobs(blobs)

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.get("/api/feedback/export", headers=auth_header(admin_token))

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]

    text = response.content.decode("utf-8-sig")
    assert "時間" in text
    assert "問題類型" in text
    assert "內容" in text
    assert "酒駕罰則答案有誤" in text
    assert "頁面打不開" in text


def test_export_feedback_user_forbidden(client, user_token):
    """一般 user 呼叫匯出端點，回 403。"""
    response = client.get("/api/feedback/export", headers=auth_header(user_token))
    assert response.status_code == 403


def test_export_feedback_no_bucket_returns_400(client, admin_token):
    """`FEEDBACK_LOG_BUCKET` 未設定時，回 400 並含說明訊息。"""
    with patch.object(client.app.state, "settings", _mock_settings("")):
        response = client.get("/api/feedback/export", headers=auth_header(admin_token))

    assert response.status_code == 400
    assert "FEEDBACK_LOG_BUCKET" in response.json()["detail"]


def test_export_feedback_empty_bucket_returns_header_only(client, admin_token):
    """bucket 存在但無任何 blob，回 200 且 CSV 只有表頭。"""
    mock_client = _mock_storage_with_blobs([])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.get("/api/feedback/export", headers=auth_header(admin_token))

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == 1  # 只有表頭
    assert "時間" in lines[0]


# ── 帶入最新對話問答 ────────────────────────────────────────────────

def _seed_conversation(client, user_sub, messages):
    """直接在 session store 裡建一個含指定訊息的對話，回傳 conv_id。"""
    from services.session_manager import add_message, create_conversation

    store = client.app.state.session_store
    state = store.get(user_sub)
    conv = create_conversation(state)
    for role, content in messages:
        add_message(state, role, content)
    store.save(user_sub, state)
    return conv.id


def _uploaded_payload(mock_client):
    raw = mock_client.bucket.return_value.blob.return_value.upload_from_string.call_args[0][0]
    return json.loads(raw)


def test_submit_feedback_with_conv_id_captures_last_qa(client, user_token):
    """帶 conv_id 送出：記錄自動帶入該對話最後一組問答。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "第一個問題"),
        ("assistant", "第一個答案"),
        ("user", "酒駕0.25會怎樣"),
        ("assistant", "**結論:** 依§35處罰..."),
    ])
    mock_client = _mock_storage_with_blobs([])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.post(
                "/api/feedback",
                json={"type": "答案錯誤", "content": "罰鍰金額寫錯了", "conv_id": conv_id},
                headers=auth_header(user_token),
            )

    assert response.status_code == 200
    payload = _uploaded_payload(mock_client)
    assert payload["question"] == "酒駕0.25會怎樣"
    assert payload["answer"] == "**結論:** 依§35處罰..."


def test_submit_feedback_unknown_conv_id_still_200(client, user_token):
    """conv_id 不存在：回饋照常成功，問答欄留空。"""
    mock_client = _mock_storage_with_blobs([])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.post(
                "/api/feedback",
                json={"type": "答案錯誤", "content": "內容", "conv_id": "no-such-conv"},
                headers=auth_header(user_token),
            )

    assert response.status_code == 200
    payload = _uploaded_payload(mock_client)
    assert payload["question"] == ""
    assert payload["answer"] == ""


def test_submit_feedback_without_conv_id_backward_compatible(client, user_token):
    """不帶 conv_id（舊前端）：照常成功。"""
    mock_client = _mock_storage_with_blobs([])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.post(
                "/api/feedback",
                json={"type": "系統功能異常", "content": "按鈕沒反應"},
                headers=auth_header(user_token),
            )

    assert response.status_code == 200
    payload = _uploaded_payload(mock_client)
    assert payload["question"] == ""
    assert payload["answer"] == ""


# ── 訊息級評分（👍👎）────────────────────────────────────────────────

def _rate(client, token, conv_id, index, rating):
    return client.post(
        "/api/feedback/rating",
        json={"conv_id": conv_id, "message_index": index, "rating": rating},
        headers=auth_header(token),
    )


def test_rating_up_persists_and_logs(client, user_token):
    """按讚：訊息 rating 更新（GET /messages 可見）且 GCS 記錄 type=讚、配對正確問題、
    含 conv_id/message_ts 識別欄位（3-2 去重用）。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "紅牌騎乘慢車道"),
        ("assistant", "**結論:** 依§45..."),
    ])
    mock_client = _mock_storage_with_blobs([])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = _rate(client, user_token, conv_id, 1, "up")

    assert response.status_code == 200
    payload = _uploaded_payload(mock_client)
    assert payload["type"] == "讚"
    assert payload["question"] == "紅牌騎乘慢車道"
    assert payload["answer"] == "**結論:** 依§45..."
    assert payload["conv_id"] == conv_id
    assert payload["message_ts"]  # 訊息識別（伺服器產生的 timestamp）

    msgs = client.get(
        f"/api/conversations/{conv_id}/messages", headers=auth_header(user_token)
    ).json()
    assert msgs[1]["rating"] == "up"
    assert msgs[0]["rating"] == ""  # user 訊息不受影響


def test_rating_down_logs_type(client, user_token):
    """倒讚：GCS 記錄 type=倒讚。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問"), ("assistant", "答"),
    ])
    mock_client = _mock_storage_with_blobs([])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = _rate(client, user_token, conv_id, 1, "down")

    assert response.status_code == 200
    assert _uploaded_payload(mock_client)["type"] == "倒讚"


def test_rating_change_updates_stored_value(client, user_token):
    """先讚後倒讚：儲存值更新為最後一次評分。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問"), ("assistant", "答"),
    ])
    with patch.object(client.app.state, "settings", _mock_settings("")):
        assert _rate(client, user_token, conv_id, 1, "up").status_code == 200
        assert _rate(client, user_token, conv_id, 1, "down").status_code == 200

    msgs = client.get(
        f"/api/conversations/{conv_id}/messages", headers=auth_header(user_token)
    ).json()
    assert msgs[1]["rating"] == "down"


def test_rating_invalid_value_returns_422(client, user_token):
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問"), ("assistant", "答"),
    ])
    assert _rate(client, user_token, conv_id, 1, "meh").status_code == 422


def test_rating_unknown_conv_returns_404(client, user_token):
    assert _rate(client, user_token, "no-such-conv", 0, "up").status_code == 404


def test_rating_index_out_of_range_returns_404(client, user_token):
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問"), ("assistant", "答"),
    ])
    assert _rate(client, user_token, conv_id, 99, "up").status_code == 404


def test_rating_user_message_returns_404(client, user_token):
    """評分對象必須是 assistant 訊息。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問"), ("assistant", "答"),
    ])
    assert _rate(client, user_token, conv_id, 0, "up").status_code == 404


def test_rating_recovers_by_timestamp_when_index_shifted(client, user_token):
    """index 失準（模擬 Firestore 超限裁切位移）：以 message_ts 尋回正確訊息。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問A"), ("assistant", "答A"),
        ("user", "問B"), ("assistant", "答B"),
    ])
    msgs = client.get(
        f"/api/conversations/{conv_id}/messages", headers=auth_header(user_token)
    ).json()
    ts_of_answer_a = msgs[1]["timestamp"]

    # 前端以為答A在 index 3（位移後），實際在 1；靠 ts 尋回
    with patch.object(client.app.state, "settings", _mock_settings("")):
        r = client.post(
            "/api/feedback/rating",
            json={"conv_id": conv_id, "message_index": 3,
                  "message_ts": ts_of_answer_a, "rating": "up"},
            headers=auth_header(user_token),
        )
    assert r.status_code == 200

    msgs = client.get(
        f"/api/conversations/{conv_id}/messages", headers=auth_header(user_token)
    ).json()
    assert msgs[1]["rating"] == "up"   # 答A（ts 對應者）被評分
    assert msgs[3]["rating"] == ""     # 答B（index 指向者）不受影響


def test_rating_bad_index_and_bad_ts_returns_404(client, user_token):
    """index 與 ts 都對不上任何 assistant 訊息：404。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問"), ("assistant", "答"),
    ])
    r = client.post(
        "/api/feedback/rating",
        json={"conv_id": conv_id, "message_index": 99,
              "message_ts": "no-such-ts", "rating": "up"},
        headers=auth_header(user_token),
    )
    assert r.status_code == 404


def test_rating_no_bucket_still_persists(client, user_token):
    """未設定 bucket：GCS 靜默跳過，但 rating 仍寫入對話。"""
    conv_id = _seed_conversation(client, "test-user-sub", [
        ("user", "問"), ("assistant", "答"),
    ])
    with patch.object(client.app.state, "settings", _mock_settings("")):
        assert _rate(client, user_token, conv_id, 1, "up").status_code == 200

    msgs = client.get(
        f"/api/conversations/{conv_id}/messages", headers=auth_header(user_token)
    ).json()
    assert msgs[1]["rating"] == "up"


def test_export_feedback_csv_includes_qa_columns(client, admin_token):
    """匯出 CSV 含「對話問題」「系統答案」欄；舊記錄（無這兩鍵）容錯為空。"""
    new_blob = MagicMock()
    new_blob.download_as_text.return_value = json.dumps({
        "ts": "2026-07-13T10:00:00+00:00", "type": "答案錯誤", "content": "回饋內容",
        "user_sub": "u1", "question": "酒駕0.25會怎樣", "answer": "依§35處罰",
    })
    old_blob = _make_blob("2026-07-12T10:00:00+00:00", "系統功能異常", "舊記錄")
    mock_client = _mock_storage_with_blobs([old_blob, new_blob])

    with patch("services.feedback_log.storage.Client", return_value=mock_client):
        with patch.object(client.app.state, "settings", _mock_settings("test-bucket")):
            response = client.get("/api/feedback/export", headers=auth_header(admin_token))

    assert response.status_code == 200
    csv_text = response.content.decode("utf-8-sig")
    assert "對話問題" in csv_text and "系統答案" in csv_text
    assert "酒駕0.25會怎樣" in csv_text
