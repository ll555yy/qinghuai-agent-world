from __future__ import annotations


def test_create_snapshot_advance_and_hidden_boundary(client) -> None:
    response = client.post("/api/runs", json={"agendaId": "agenda_001_literary_society"})
    assert response.status_code == 201
    created = response.json()
    run_id = created["runId"]
    assert created["worldTime"]["label"] == "Day1 09:00"
    assert created["playerAgendaId"] == "agenda_001_literary_society"
    text = str(created)
    for forbidden in ("coreSecrets", "authoringNote", "privateMemory", "trust", "affinity", "tension"):
        assert forbidden not in text
    assert registry_secret_not_present(created)

    advanced = client.post(
        f"/api/runs/{run_id}/time/advance",
        json={"virtualMinutes": 60, "commandId": "advance-1"},
    )
    assert advanced.status_code == 200
    assert advanced.json()["worldTime"]["label"] == "Day1 10:00"
    assert advanced.json()["run"]["stateVersion"] == created["stateVersion"] + 1

    repeated = client.post(
        f"/api/runs/{run_id}/time/advance",
        json={"virtualMinutes": 60, "commandId": "advance-1"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["run"]["eventSeq"] == advanced.json()["run"]["eventSeq"]

    events = client.get(f"/api/runs/{run_id}/events?afterSeq={created['eventSeq']}")
    assert events.status_code == 200
    assert [event["eventType"] for event in events.json()["events"]] == ["time_advanced"]


def test_run_and_agenda_not_found_errors(client) -> None:
    missing_run = client.get("/api/runs/run_missing")
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["code"] == "run_not_found"
    missing_agenda = client.post("/api/runs", json={"agendaId": "agenda_missing"})
    assert missing_agenda.status_code == 404
    assert missing_agenda.json()["error"]["code"] == "agenda_not_found"


def registry_secret_not_present(snapshot: dict) -> bool:
    return "年轻时曾和周慎之的父亲因办学理念闹翻" not in str(snapshot)
