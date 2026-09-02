from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from core.backend.app.evaluation.case_loader import CaseLoader
from core.backend.app.evaluation.models import CandidateObservation
from core.backend.app.evaluation.rule_scorer import RuleScorer

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = PROJECT_ROOT / "core" / "evaluation" / "fixtures" / "agent_semantic_hard_failures_v1.json"
CASE_PATH = PROJECT_ROOT / "core" / "evaluation" / "agent_semantic_cases.yaml"
BASELINE_PATH = (
    PROJECT_ROOT
    / "project"
    / "evaluation-results"
    / "live-baseline-2026-08-23"
    / "agent_semantic_evaluation.json"
)
CHECKSUM_PATH = BASELINE_PATH.parent / "SHA256SUMS"

EXPECTED_CASES = frozenset(
    {
        "boundary_005_rare_book",
        "boundary_006_evidence_scope",
        "coherence_004_participant_change",
        "coherence_006_goal_progress",
        "relevance_003_actor_goal",
        "relevance_004_visible_evidence",
        "rules_001_daily_seek_chat",
        "rules_002_daily_wait_shape",
        "rules_004_chat_action",
        "rules_005_evidence_ids",
        "rules_007_time_boundary",
        "rules_008_departed_npc",
        "rules_010_no_world_mutation",
        "rules_011_other_goal_forbidden",
        "rules_012_single_memory_call",
    }
)
EXPECTED_V2_CASES = frozenset(
    {
        "boundary_005_rare_book",
        "rules_010_no_world_mutation",
        "rules_011_other_goal_forbidden",
        "rules_012_single_memory_call",
    }
)
ATTRIBUTIONS = frozenset(
    {"candidate", "projection", "scorer", "case", "judge", "mixed"}
)
UNKNOWN = "unknown"


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _hard_failure_map() -> dict[tuple[str, int], frozenset[str]]:
    report = _load_baseline()
    result: dict[tuple[str, int], frozenset[str]] = {}
    for case in report["cases"]:
        for run in case.get("runs", []):
            score = run.get("ruleScore", {})
            if score.get("hard_failure"):
                result[(case["caseId"], run["runIndex"])] = frozenset(
                    score.get("failures", [])
                )
    return result


def test_fixture_covers_exactly_the_30_historical_hard_observations() -> None:
    fixture = _load_fixture()
    observations = fixture["observations"]
    assert fixture["syntheticReconstruction"] is True
    assert fixture["provenance"]["rawTraceAvailable"] is False
    assert fixture["provenance"]["replayMode"] == "representative_only"
    assert len(observations) == 30

    keys = [(item["caseId"], item["runIndex"]) for item in observations]
    assert len(set(keys)) == 30
    assert set(Counter(item["caseId"] for item in observations)) == EXPECTED_CASES
    assert all(keys.count((case_id, run_index)) == 1 for case_id in EXPECTED_CASES for run_index in (0, 1))

    baseline_failures = _hard_failure_map()
    assert len(baseline_failures) == 30
    for item in observations:
        key = (item["caseId"], item["runIndex"])
        assert key in baseline_failures
        assert frozenset(item["baselineFailures"]) == baseline_failures[key]
        assert item["baselineFailures"]
        assert item["attribution"] in ATTRIBUTIONS
        assert item["systemBlocked"] == UNKNOWN
        assert item["endToEndSafetyFailure"] == UNKNOWN
        assert item["syntheticReconstruction"] is True
        assert item["exactReplay"] is False
        assert item["summary"]
        assert isinstance(item["structuredReconstruction"], dict)


def test_fixture_case_ids_are_current_and_only_four_are_v2() -> None:
    fixture = _load_fixture()
    current_versions = {
        case.case_id: case.case_version for case in CaseLoader(CASE_PATH).load()
    }
    observations = fixture["observations"]
    assert {item["caseId"] for item in observations} <= current_versions.keys()
    assert all(
        item["caseVersion"] == current_versions[item["caseId"]]
        for item in observations
    )
    version_two_ids = {
        item["caseId"] for item in observations if item["caseVersion"] == 2
    }
    assert version_two_ids == EXPECTED_V2_CASES
    assert len(version_two_ids) == 4
    assert all(item["caseVersion"] in {1, 2} for item in observations)


def test_fixture_contains_no_secret_url_or_production_prompt_material() -> None:
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    forbidden = (
        r"https?://",
        r"(?:postgres(?:ql)?|mysql|redis)://",
        r"api[_-]?key",
        r"access[_-]?token",
        r"system[_-]?prompt",
        r"coreSecrets",
        r"production\s+prompt",
        r"-----BEGIN",
    )
    assert not any(re.search(pattern, raw, flags=re.IGNORECASE) for pattern in forbidden)


def test_representative_reconstructions_only_assert_their_declared_rule_subset() -> None:
    fixture = _load_fixture()
    records = {item["fixtureId"]: item for item in fixture["observations"]}
    case_by_id = {case.case_id: case for case in CaseLoader(CASE_PATH).load()}
    representatives = fixture["representativeReplays"]
    assert representatives

    for replay in representatives:
        record = records[replay["fixtureId"]]
        case = case_by_id[record["caseId"]]
        observation = CandidateObservation(
            case_id=case.case_id,
            protocol=case.protocol,
            structured_output=record["structuredReconstruction"],
        )
        score = RuleScorer().score(case, observation)
        assert set(replay["expectedFailures"]) <= set(score.failures)
        assert "full baseline replay" not in replay["purpose"]


def test_baseline_sha256_matches_the_immutable_regression_fixture() -> None:
    expected = {
        "agent_semantic_evaluation.json": "444067d97327356b5946598ad77715b29fac8dc7dd1d0e8cbb89900a8b5060a5",
    }
    lines = [line.split() for line in CHECKSUM_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    actual = {parts[1]: parts[0].lower() for parts in lines if len(parts) == 2}
    assert actual == expected
    assert "SHA256SUMS" not in actual
    for name, digest in expected.items():
        value = hashlib.sha256((CHECKSUM_PATH.parent / name).read_bytes()).hexdigest()
        assert value == digest
