from __future__ import annotations


def test_websocket_starts_with_snapshot_and_receives_run_event(client) -> None:
    run_id = client.post("/api/runs", json={}).json()["runId"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["runId"] == run_id
        client.post(f"/api/runs/{run_id}/time/advance", json={"virtualMinutes": 1})
        event = websocket.receive_json()
        assert event["runId"] == run_id
        assert event["eventType"] == "time_advanced"


def test_websocket_reconnect_replays_durable_events_after_sequence(client) -> None:
    created = client.post("/api/runs", json={}).json()
    run_id = created["runId"]
    advanced = client.post(
        f"/api/runs/{run_id}/time/advance",
        json={"virtualMinutes": 1},
    )
    assert advanced.status_code == 200
    with client.websocket_connect(
        f"/ws/runs/{run_id}?afterSeq={created['eventSeq']}"
    ) as websocket:
        replayed = websocket.receive_json()
        assert replayed["eventType"] == "time_advanced"
        snapshot = websocket.receive_json()
        assert snapshot["runId"] == run_id
        assert snapshot["eventSeq"] >= replayed["eventSeq"]
