"""G1: 提問記錄到 GCS 的整合測試。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from config import Settings
from services.question_log import log_question


# ── unit tests for log_question ────────────────────────────────────────────────

def _settings_with_bucket(bucket="test-log-bucket"):
    return Settings(
        project_id="test-project",
        data_store_id="test-ds",
        location="global",
        vertex_init_location="us-central1",
        app_password="testpw",
        question_log_bucket=bucket,
    )


def test_log_question_writes_to_gcs():
    """有設定 bucket 時，應寫入 GCS 物件。"""
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    log_question("酒駕罰則", "user-abc", _settings_with_bucket(), storage_client=mock_client)

    mock_client.bucket.assert_called_once_with("test-log-bucket")
    assert mock_bucket.blob.call_count == 1

    blob_name: str = mock_bucket.blob.call_args[0][0]
    assert blob_name.startswith("questions/")
    assert blob_name.endswith(".json")

    upload_call = mock_blob.upload_from_string.call_args
    payload = json.loads(upload_call[0][0])
    assert payload["question"] == "酒駕罰則"
    assert payload["user_sub"] == "user-abc"
    assert "ts" in payload


def test_log_question_noop_when_bucket_empty():
    """未設定 bucket 時，不應建立任何 GCS client。"""
    mock_client = MagicMock()
    settings = _settings_with_bucket(bucket="")

    log_question("測試問題", "user-xyz", settings, storage_client=mock_client)

    mock_client.bucket.assert_not_called()


# ── integration test: GCS failure must not break /ask ─────────────────────────

def test_ask_continues_when_question_log_raises(client, user_token):
    """GCS log_question 拋例外時，/ask SSE 流程必須正常完成。"""
    from tests_api.conftest import auth_header
    from services.pipeline import PipelineResult

    headers = auth_header(user_token)

    # 建立對話
    conv_resp = client.post("/api/conversations", json={"persona_id": "traffic"}, headers=headers)
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    mock_result = PipelineResult(
        answer="正常回答",
        intent="SEARCH",
        stage_latency_ms={"router": 1.0, "answer": 1.0},
        request_id="test-req",
    )

    with patch("api.routes.chat_routes.run_rag_pipeline", return_value=mock_result):
        with patch("api.routes.chat_routes.log_question", side_effect=Exception("GCS error")):
            response = client.post(
                f"/api/conversations/{conv_id}/ask",
                json={"question": "測試提問"},
                headers=headers,
            )

    assert response.status_code == 200
    # SSE 流程完成，應有 done event 且帶正確答案
    events = []
    for block in response.text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    done_events = [e for e in events if e.get("type") == "done"]
    assert len(done_events) >= 1
    assert done_events[0]["answer"] == "正常回答"
