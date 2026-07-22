from tests_api.conftest import auth_header


def _create_conversation(client, headers, payload=None):
    response = client.post("/api/conversations", json=payload or {}, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_list_conversations_empty(client, user_token):
    response = client.get("/api/conversations", headers=auth_header(user_token))

    assert response.status_code == 200
    assert response.json() == []


def test_create_conversation(client, user_token):
    response = client.post(
        "/api/conversations",
        json={"persona_id": "traffic"},
        headers=auth_header(user_token),
    )

    assert response.status_code == 201
    data = response.json()
    assert {"id", "title", "persona_id", "created_at"}.issubset(data.keys())
    assert data["persona_id"] == "traffic"


def test_create_conversation_default_persona(client, user_token):
    response = client.post("/api/conversations", json={}, headers=auth_header(user_token))

    assert response.status_code == 201
    assert response.json()["persona_id"] == "traffic"


def test_list_after_create(client, user_token):
    headers = auth_header(user_token)
    _create_conversation(client, headers, {"persona_id": "traffic"})
    _create_conversation(client, headers, {"persona_id": "traffic"})

    response = client.get("/api/conversations", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_delete_conversation(client, user_token):
    headers = auth_header(user_token)
    conv = _create_conversation(client, headers, {"persona_id": "traffic"})

    delete_response = client.delete(f"/api/conversations/{conv['id']}", headers=headers)
    assert delete_response.status_code == 204

    list_response = client.get("/api/conversations", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_delete_nonexistent(client, user_token):
    response = client.delete("/api/conversations/not-exist", headers=auth_header(user_token))

    assert response.status_code == 404


def test_get_messages_empty(client, user_token):
    headers = auth_header(user_token)
    conv = _create_conversation(client, headers)

    response = client.get(f"/api/conversations/{conv['id']}/messages", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_patch_persona(client, user_token):
    headers = auth_header(user_token)
    conv = _create_conversation(client, headers, {"persona_id": "traffic"})

    patch_response = client.patch(
        f"/api/conversations/{conv['id']}/persona",
        json={"persona_id": "traffic"},
        headers=headers,
    )
    assert patch_response.status_code == 204

    list_response = client.get("/api/conversations", headers=headers)
    convs = list_response.json()
    updated = next(item for item in convs if item["id"] == conv["id"])
    assert updated["persona_id"] == "traffic"


def test_patch_nonexistent(client, user_token):
    response = client.patch(
        "/api/conversations/not-exist/persona",
        json={"persona_id": "traffic"},
        headers=auth_header(user_token),
    )
    assert response.status_code == 404


def test_get_messages_nonexistent(client, user_token):
    response = client.get(
        "/api/conversations/not-exist/messages",
        headers=auth_header(user_token),
    )
    assert response.status_code == 404
