from __future__ import annotations


def test_world_step_uses_private_agent_graph_without_exposing_trace(client) -> None:
    created = client.post("/api/runs", json={})
    assert created.status_code == 201
    run_id = created.json()["runId"]

    stepped = client.post(
        f"/api/runs/{run_id}/world/step",
        json={"realSeconds": 540},
    )
    assert stepped.status_code == 200

    service = client.app.state.run_service
    traces = [
        trace
        for trace in service.agent_runtime.trace_sink.snapshot()
        if trace.run_id == run_id
    ]
    assert len(traces) == 5
    assert all(trace.event_type == "daily_tick" for trace in traces)
    assert all(
        trace.node_path == ("route_event", "daily_decision", "finalize")
        for trace in traces
    )

    public_text = str(stepped.json())
    assert "traceId" not in public_text
    assert "nodePath" not in public_text
    assert "graphState" not in public_text
    assert "memoryQuery" not in public_text
