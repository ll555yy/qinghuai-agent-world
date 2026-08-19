from __future__ import annotations


def _run(client) -> str:
    return client.post("/api/runs", json={}).json()["runId"]


def test_conversation_create_join_leave_and_limits(client) -> None:
    run_id = _run(client)
    created = client.post(
        f"/api/runs/{run_id}/conversations",
        json={"participantIds": ["npc_001", "npc_002"], "commandId": "create-1"},
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation"]["conversationId"]
    joined = client.post(
        f"/api/runs/{run_id}/conversations/{conversation_id}/participants",
        json={"actorId": "npc_003", "commandId": "join-1"},
    )
    assert joined.status_code == 200
    assert len(joined.json()["conversation"]["participants"]) == 3
    full = client.post(
        f"/api/runs/{run_id}/conversations/{conversation_id}/participants",
        json={"actorId": "npc_004"},
    )
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "conversation_full"
    left = client.request(
        "DELETE",
        f"/api/runs/{run_id}/conversations/{conversation_id}/participants/npc_003",
        json={"commandId": "leave-1"},
    )
    assert left.status_code == 200
    assert left.json()["conversation"]["status"] == "open"
    closed = client.request(
        "DELETE",
        f"/api/runs/{run_id}/conversations/{conversation_id}/participants/npc_002",
        json={"commandId": "leave-2"},
    )
    assert closed.status_code == 200
    assert closed.json()["conversation"]["status"] == "closed"


def test_two_open_conversation_limit_and_actor_exclusivity(client) -> None:
    run_id = _run(client)
    first = client.post(
        f"/api/runs/{run_id}/conversations", json={"participantIds": ["npc_001", "npc_002"]}
    ).json()["conversation"]["conversationId"]
    second = client.post(
        f"/api/runs/{run_id}/conversations", json={"participantIds": ["npc_003", "npc_004"]}
    )
    assert second.status_code == 200
    duplicate_actor = client.post(
        f"/api/runs/{run_id}/conversations", json={"participantIds": ["npc_001", "npc_005"]}
    )
    assert duplicate_actor.status_code == 409
    assert duplicate_actor.json()["error"]["code"] == "conversation_limit_reached"
    # Close the first; its participants are available for a new conversation.
    client.delete(f"/api/runs/{run_id}/conversations/{first}/participants/npc_002")
    third = client.post(
        f"/api/runs/{run_id}/conversations", json={"participantIds": ["npc_001", "npc_005"]}
    )
    assert third.status_code == 200
