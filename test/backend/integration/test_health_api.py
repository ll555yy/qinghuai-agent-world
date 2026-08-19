from __future__ import annotations


def test_liveness_and_scenario_readiness(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "processAlive": True,
        "scenarioLoaded": True,
    }

