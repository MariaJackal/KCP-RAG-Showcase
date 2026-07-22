import json
from unittest.mock import patch

from services.pipeline import PipelineResult
from tests_api.conftest import auth_header


def _create_conversation(client, headers):
    response = client.post("/api/conversations", json={"persona_id": "traffic"}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def _parse_sse_events(raw_text):
    events = []
    for block in raw_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _streaming_pipeline_result(*args, **kwargs):
    progress_callback = kwargs.get("progress_callback")
    stream_callback = kwargs.get("stream_callback")

    if progress_callback is not None:
        progress_callback("working")
    if stream_callback is not None:
        stream_callback("Hello")
        stream_callback(" world")

    return PipelineResult(
        answer="Hello world",
        intent="SEARCH",
        stage_latency_ms={"router": 10.0, "rewrite": 20.0, "search": 30.0, "answer": 40.0},
        request_id="req123456789",
    )


@patch("api.routes.chat_routes.run_rag_pipeline", side_effect=_streaming_pipeline_result)
def test_ask_streams_token_and_done_events(_mock_pipeline, client, user_token):
    headers = auth_header(user_token)
    conv_id = _create_conversation(client, headers)

    response = client.post(
        f"/api/conversations/{conv_id}/ask",
        json={"question": "stream this answer"},
        headers=headers,
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    token_events = [event for event in events if event.get("type") == "token"]
    done_events = [event for event in events if event.get("type") == "done"]

    assert [event["text"] for event in token_events] == ["Hello", " world"]
    assert len(done_events) == 1
    assert done_events[0]["answer"] == "Hello world"
    # 評分按鈕定位用：done 帶回 assistant 訊息在對話中的索引（user=0, assistant=1）與 timestamp
    assert done_events[0]["message_index"] == 1
    assert done_events[0]["message_ts"]
    # grounding 未啟用（PipelineResult 預設）：欄位存在且為 None
    assert done_events[0]["grounding_score"] is None
