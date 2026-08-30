from __future__ import annotations

from pathlib import Path

import yaml

CALIBRATION_PATH = Path(__file__).resolve().parents[3] / "core" / "evaluation" / "judge_calibration_cases.yaml"


def test_calibration_cases_have_minimum_size_and_injection_coverage() -> None:
    document = yaml.safe_load(CALIBRATION_PATH.read_text(encoding="utf-8"))
    assert document["version"] == 1
    cases = document["cases"]
    assert len(cases) >= 10
    assert sum(bool(case.get("injection_attempt")) for case in cases) >= 3
    assert len({case["case_id"] for case in cases}) == len(cases)


def test_calibration_cases_are_synthetic_and_have_complete_expectations() -> None:
    document = yaml.safe_load(CALIBRATION_PATH.read_text(encoding="utf-8"))
    for case in document["cases"]:
        assert case["candidate_output"]
        assert case["case_context"]
        expected = case["expected"]
        assert expected["confidence"] in {"low", "medium", "high"}
        assert isinstance(expected["score_band"], list)
        assert len(expected["score_band"]) == 2
        assert expected["score_band"][0] <= expected["score_band"][1]
        # The fixture must not accidentally turn into a real-data test.
        text = str(case)
        assert "ARK_API_KEY" not in text
        assert "postgres://" not in text
        assert "doubao-seed-2.0-lite" not in text
