import json
from unittest.mock import patch

from services.pipeline import PipelineResult
from tests_api.conftest import auth_header


def _create_conversation(client, headers):
    response = client.post("/api/conversations", json={"persona_id": "traffic"}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def _mock_pipeline_result(answer="測試回答", sources=()):
    return PipelineResult(
        answer=answer,
        intent="SEARCH",
        stage_latency_ms={"router": 10.0, "rewrite": 20.0, "search": 30.0, "answer": 40.0},
        request_id="req123456789",
        sources=tuple(sources),
    )


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


@patch("api.routes.chat_routes.run_rag_pipeline", return_value=_mock_pipeline_result())
def test_ask_returns_sse_stream(_mock_pipeline, client, user_token):
    headers = auth_header(user_token)
    conv_id = _create_conversation(client, headers)

    response = client.post(
        f"/api/conversations/{conv_id}/ask",
        json={"question": "請問酒駕罰則"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


@patch("api.routes.chat_routes.run_rag_pipeline", return_value=_mock_pipeline_result())
def test_ask_sse_events_format(_mock_pipeline, client, user_token):
    headers = auth_header(user_token)
    conv_id = _create_conversation(client, headers)

    response = client.post(
        f"/api/conversations/{conv_id}/ask",
        json={"question": "請問酒駕罰則"},
        headers=headers,
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    done_events = [event for event in events if event.get("type") == "done"]
    assert len(done_events) >= 1
    assert "answer" in done_events[0]


def test_ask_nonexistent_conversation(client, user_token):
    response = client.post(
        "/api/conversations/not-exist/ask",
        json={"question": "測試問題"},
        headers=auth_header(user_token),
    )
    assert response.status_code == 404


@patch("api.routes.chat_routes.run_rag_pipeline", return_value=_mock_pipeline_result("這是回答"))
def test_ask_saves_messages(_mock_pipeline, client, user_token):
    headers = auth_header(user_token)
    conv_id = _create_conversation(client, headers)

    ask_response = client.post(
        f"/api/conversations/{conv_id}/ask",
        json={"question": "測試提問"},
        headers=headers,
    )
    assert ask_response.status_code == 200

    messages_response = client.get(f"/api/conversations/{conv_id}/messages", headers=headers)
    assert messages_response.status_code == 200

    messages = messages_response.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "測試提問"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "這是回答"


@patch("api.routes.chat_routes.run_rag_pipeline", return_value=_mock_pipeline_result("A的回答"))
def test_ask_saves_answer_to_asked_conversation_not_active(_mock_pipeline, client, user_token):
    """在 A 發問後即使 active 指向 B（模擬使用者切換對話），答案仍必須寫回 A，B 不得被寫入。"""
    headers = auth_header(user_token)
    conv_a = _create_conversation(client, headers)
    conv_b = _create_conversation(client, headers)  # 建立 B 後 active_conversation_id 指向 B

    r = client.post(
        f"/api/conversations/{conv_a}/ask",
        json={"question": "問A的問題"},
        headers=headers,
    )
    assert r.status_code == 200

    msgs_a = client.get(f"/api/conversations/{conv_a}/messages", headers=headers).json()
    msgs_b = client.get(f"/api/conversations/{conv_b}/messages", headers=headers).json()
    assert [m["content"] for m in msgs_a] == ["問A的問題", "A的回答"]
    assert msgs_b == []


def test_ask_conversation_deleted_mid_generation(client, user_token):
    """答案生成期間原對話被刪除：不得 crash、答案不得寫進其他對話、active 不得指向已刪除的對話。"""
    headers = auth_header(user_token)
    conv_a = _create_conversation(client, headers)
    conv_b = _create_conversation(client, headers)

    store = client.app.state.session_store
    uid = "test-user-sub"

    def _fake_pipeline(question, persona, recent_messages, **kwargs):
        # 模擬生成期間使用者刪除 A 並切到 B
        state = store.get(uid)
        del state["conversations"][conv_a]
        state["active_conversation_id"] = conv_b
        store.save(uid, state)
        return _mock_pipeline_result("遲到的回答")

    with patch("api.routes.chat_routes.run_rag_pipeline", side_effect=_fake_pipeline):
        r = client.post(
            f"/api/conversations/{conv_a}/ask",
            json={"question": "問A的問題"},
            headers=headers,
        )
        assert r.status_code == 200

    msgs_b = client.get(f"/api/conversations/{conv_b}/messages", headers=headers).json()
    assert msgs_b == [], "答案不得寫進其他對話"

    final_state = store.get(uid)
    assert final_state["active_conversation_id"] == conv_b, "active 指標不得被寫回已刪除的對話"


def test_preset_returns_assistant_index(client, user_token):
    """preset 回應帶 assistant_index（評分按鈕定位用），且與訊息實際位置一致。"""
    headers = auth_header(user_token)
    conv_id = _create_conversation(client, headers)

    r = client.post(
        f"/api/conversations/{conv_id}/preset",
        json={"preset_id": "traffic_accident_def"},
        headers=headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["assistant_index"] == 1  # user=0, assistant=1

    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    assert msgs[data["assistant_index"]]["role"] == "assistant"
    assert data["assistant_ts"] == msgs[data["assistant_index"]]["timestamp"]


@patch(
    "api.routes.chat_routes.run_rag_pipeline",
    return_value=_mock_pipeline_result(
        answer=(
            "**結論:** 應依**《道路交通管理處罰條例》第 35 條**規定處罰。\n"
            "**法規依據:**\n**《道路交通管理處罰條例》第 35 條**\n「條文原文」\n"
        ),
    ),
)
def test_ask_cross_references_law_mentions(_mock_pipeline, client, user_token):
    """結論提及的法條與法規依據條目由確定性規則層標上同號 [1]，並存檔。"""
    headers = auth_header(user_token)
    conv_id = _create_conversation(client, headers)

    response = client.post(
        f"/api/conversations/{conv_id}/ask",
        json={"question": "酒駕罰多少"},
        headers=headers,
    )
    assert response.status_code == 200
    done = [e for e in _parse_sse_events(response.text) if e.get("type") == "done"][0]
    assert "規定 [1]" in done["answer"]  # 結論處
    assert "第 35 條** [1]\n「條文原文" in done["answer"]  # 依據段
    assert "citations" not in done  # 卡片功能已移除，不再發 citations

    messages = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    assistant = [m for m in messages if m["role"] == "assistant"][0]
    assert "規定 [1]" in assistant["content"]


def test_followup_digit_sees_previous_history_in_memory_store(client, user_token):
    """記憶體 store 必須在兩輪之間保留對話歷史，追問數字解析才能成功。

    第一輪：回答含追問 menu（直接輸入數字即可）。
    第二輪：送數字 "1"，捕捉 pipeline 被呼叫時的 recent_messages。
    驗收：recent_messages 包含第一輪的 user + assistant 訊息。
    """
    headers = auth_header(user_token)
    conv_id = _create_conversation(client, headers)

    menu_answer = (
        "**結論:** 酒駕的罰則如下。\n\n"
        "想提供更精確的答案，請問你的情況是：\n"
        "(1) 初犯\n(2) 累犯\n(3) 以上皆想了解\n\n直接輸入數字即可"
    )

    captured = {}

    def _fake_pipeline(question, persona, recent_messages, **kwargs):
        captured["question"] = question
        captured["recent_messages"] = list(recent_messages)
        answer = menu_answer if question == "酒駕" else "第二輪回答"
        return PipelineResult(
            answer=answer,
            intent="SEARCH",
            stage_latency_ms={"router": 1.0, "answer": 1.0},
            request_id="test-req",
        )

    with patch("api.routes.chat_routes.run_rag_pipeline", side_effect=_fake_pipeline):
        # 第一輪：問「酒駕」
        r1 = client.post(
            f"/api/conversations/{conv_id}/ask",
            json={"question": "酒駕"},
            headers=headers,
        )
        assert r1.status_code == 200

        # 第二輪：送數字追問
        r2 = client.post(
            f"/api/conversations/{conv_id}/ask",
            json={"question": "1"},
            headers=headers,
        )
        assert r2.status_code == 200

    # 第二輪呼叫 pipeline 時，recent_messages 必須包含第一輪的 2 則訊息
    msgs = captured["recent_messages"]
    assert len(msgs) >= 2, "記憶體 store 應保留第一輪的 user + assistant 訊息"
    roles = [m.role for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
    # assistant 訊息必須包含 menu 標記，pipeline 才能識別為追問
    assistant_contents = [m.content for m in msgs if m.role == "assistant"]
    assert any("直接輸入數字即可" in c for c in assistant_contents)
