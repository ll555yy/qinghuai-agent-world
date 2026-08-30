from __future__ import annotations

from pathlib import Path

import pytest
from core.backend.app.evaluation.case_loader import CaseLoader, CaseValidationError
from core.backend.app.evaluation.models import EvaluationCase

CASE_PATH = Path(__file__).resolve().parents[3] / "core" / "evaluation" / "agent_semantic_cases.yaml"


def test_semantic_cases_are_versioned_and_cover_every_category() -> None:
    cases = CaseLoader(CASE_PATH).load()

    assert len(cases) >= 30
    assert len({case.case_id for case in cases}) == len(cases)
    assert {case.category for case in cases} == {
        "persona",
        "boundary",
        "memory",
        "rules",
        "relevance",
        "coherence",
    }
    for category in {case.category for case in cases}:
        assert sum(case.category == category for case in cases) >= 5
    assert all(case.case_version >= 1 for case in cases)
    assert all(case.requires_live_candidate is False for case in cases)


def test_loader_accepts_camel_case_and_rejects_duplicate_ids() -> None:
    data = {
        "version": 1,
        "cases": [
            {
                "caseId": "duplicate",
                "caseVersion": 1,
                "category": "persona",
                "protocol": "speech_generation",
                "npcId": "npc_001",
                "inputContext": {},
                "expectedConstraints": ["stay in character"],
                "forbiddenSignals": [],
                "allowedOutcomes": [],
                "expectedMemoryIds": [],
                "allowedEvidenceMessageIds": [],
                "requiresPostgres": False,
                "requiresLiveCandidate": False,
                "requiresLiveEmbedding": False,
                "judgeRubric": ["tone"],
                "tags": ["test"],
            },
        ],
    }
    assert CaseLoader().load_data(data)[0].case_id == "duplicate"
    with pytest.raises(CaseValidationError, match="duplicate case_id"):
        CaseLoader().load_data({**data, "cases": [data["cases"][0], data["cases"][0]]})


def test_loader_rejects_unknown_npc_and_credential_like_context() -> None:
    base = {
        "caseId": "invalid",
        "caseVersion": 1,
        "category": "boundary",
        "protocol": "speech_generation",
        "npcId": "npc_001",
        "inputContext": {},
        "expectedConstraints": ["stay safe"],
        "forbiddenSignals": [],
        "allowedOutcomes": [],
        "expectedMemoryIds": [],
        "allowedEvidenceMessageIds": [],
        "requiresPostgres": False,
        "requiresLiveCandidate": False,
        "requiresLiveEmbedding": False,
        "judgeRubric": ["safety"],
        "tags": ["test"],
    }
    with pytest.raises(CaseValidationError, match="unknown npc_id"):
        CaseLoader().load_data({"version": 1, "cases": [{**base, "npcId": "npc_999"}]})
    with pytest.raises(CaseValidationError, match="credential-like"):
        CaseLoader().load_data(
            {
                "version": 1,
                "cases": [{**base, "inputContext": {"apiKey": "sk-live-not-a-case-secret"}}],
            }
        )
    with pytest.raises(CaseValidationError, match="credential-like"):
        CaseLoader().load_data(
            {
                "version": 1,
                "cases": [
                    {
                        **base,
                        "judgeRubric": ["Bearer abcdefghijklmnop must never be stored"],
                    }
                ],
            }
        )


def test_pydantic_contract_rejects_contradictory_markers_and_extra_fields() -> None:
    payload = {
        "case_id": "contradictory",
        "case_version": 1,
        "category": "rules",
        "protocol": "chat_decision",
        "npc_id": "npc_001",
        "input_context": {},
        "expected_constraints": ["must_speak"],
        "forbidden_signals": ["must_not_speak"],
        "allowed_outcomes": ["speak"],
        "expected_memory_ids": [],
        "allowed_evidence_message_ids": [],
        "requires_postgres": False,
        "requires_live_candidate": False,
        "requires_live_embedding": False,
        "judge_rubric": ["action"],
        "tags": ["test"],
    }
    with pytest.raises(ValueError, match="contradictory"):
        EvaluationCase.model_validate(payload)
    with pytest.raises(ValueError, match="extra"):
        EvaluationCase.model_validate({**payload, "unexpected": True})
