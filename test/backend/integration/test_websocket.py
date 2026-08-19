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

