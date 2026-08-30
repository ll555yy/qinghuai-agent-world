"""Public metadata needed before the player creates a Run."""

from __future__ import annotations


def test_public_agendas_are_available_without_a_run(client) -> None:
    response = client.get("/api/scenario/agendas")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter"]["chapterId"] == "chapter_01_proposal_deadline"
    assert payload["chapter"]["endsAt"] == "Day7 18:00"
    assert len(payload["agendas"]) == 5
    assert len(payload["actors"]) == 5
    assert all("coreSecrets" not in actor for actor in payload["actors"])
    assert set(payload["agendas"][0]) == {
        "agendaId",
        "ownerNpcId",
        "title",
        "publicSummary",
    }
    assert "goal" not in str(payload).lower()
    assert "secret" not in str(payload).lower()
