from __future__ import annotations

import json

import pytest
from core.backend.app.ai.decision_service import extract_json_object


def test_extract_json_object_accepts_exact_plain_object() -> None:
    assert extract_json_object('  {"action":"wait"}\n') == {"action": "wait"}


@pytest.mark.parametrize(
    "raw",
    (
        '```json\n{"action":"wait"}\n```',
        '说明：{"action":"wait"}',
        '{"action":"wait"} 额外说明',
        '[{"action":"wait"}]',
    ),
)
def test_extract_json_object_rejects_wrappers_trailing_text_and_arrays(raw: str) -> None:
    with pytest.raises((json.JSONDecodeError, ValueError)):
        extract_json_object(raw)
