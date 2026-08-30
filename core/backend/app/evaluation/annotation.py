"""Preparation-only helpers for a two-person semantic annotation packet.

The generated sheets are intentionally blank.  No Candidate, Judge, or
evaluation worker is allowed to populate a human label; the package records
the exact sample roster and leaves agreement/arbitration pending until two
real, independently identified annotators return their sheets.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AnnotationSample:
    sample_id: str
    case_id: str
    category: str
    protocol: str


def _value(case: object, *names: str, default: Any = "") -> Any:
    if isinstance(case, Mapping):
        for name in names:
            if name in case:
                return case[name]
    for name in names:
        if hasattr(case, name):
            return getattr(case, name)
    return default


def freeze_annotation_samples(
    cases: Iterable[object],
    *,
    sample_count: int = 24,
) -> tuple[AnnotationSample, ...]:
    """Select a stable 20--30-case roster stratified by semantic category."""

    if not 20 <= sample_count <= 30:
        raise ValueError("sample_count must be between 20 and 30")
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen: set[str] = set()
    for case in cases:
        case_id = str(_value(case, "case_id", "caseId", default="")).strip()
        category = str(_value(case, "category", default="")).strip()
        protocol = str(_value(case, "protocol", default="")).strip()
        if not case_id or not category or not protocol or case_id in seen:
            continue
        seen.add(case_id)
        grouped[category].append((case_id, protocol))
    if sum(len(values) for values in grouped.values()) < sample_count:
        raise ValueError("not enough unique cases for the requested annotation roster")
    for values in grouped.values():
        values.sort(key=lambda item: item[0])
    categories = sorted(grouped)
    selected: list[tuple[str, str, str]] = []
    cursor = 0
    # Round-robin keeps the blank sheets representative even when one
    # category has many more cases than another.  Tie-breaking is lexical.
    while len(selected) < sample_count:
        category = categories[cursor % len(categories)]
        offset = cursor // len(categories)
        if offset < len(grouped[category]):
            case_id, protocol = grouped[category][offset]
            selected.append((category, case_id, protocol))
        cursor += 1
        if cursor > sample_count * max(2, len(categories)) * 2:
            break
    selected.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        AnnotationSample(
            sample_id=f"sample-{index:03d}",
            category=category,
            case_id=case_id,
            protocol=protocol,
        )
        for index, (category, case_id, protocol) in enumerate(selected[:sample_count], start=1)
    )


def annotation_roster_digest(samples: Sequence[AnnotationSample]) -> str:
    payload = [
        {
            "sampleId": sample.sample_id,
            "caseId": sample.case_id,
            "category": sample.category,
            "protocol": sample.protocol,
        }
        for sample in samples
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_LABEL_COLUMNS = (
    "criticalBooleanLabel",
    "scoreBandLabel",
    "requiredMajorIssuesLabel",
    "forbiddenMajorIssuesLabel",
    "injectionHandlingLabel",
    "notes",
)


def render_annotation_instructions(samples: Sequence[AnnotationSample]) -> str:
    digest = annotation_roster_digest(samples)
    return "\n".join(
        [
            "# 人工语义标注说明（准备包）",
            "",
            "状态：等待两名真实人工；本文件不包含任何自动填入的金标。",
            "",
            f"- 样本数：`{len(samples)}`（冻结范围 20--30）",
            f"- 样本清单 SHA-256：`{digest}`",
            "- 标注者 A 与 B 必须独立、不可见对方答案；只能填写各自空白表。",
            "- Candidate、Judge、子智能体、脚本和规则评分器均不得代填人工列。",
            "- 仅依据样本中脱敏的公开上下文和候选输出判断；不得补猜未展示的私有状态。",
            "- 分歧交由仲裁表记录，禁止为了提高一致率回写或改写原始标签。",
            "",
            "## 每个样本填写",
            "",
            "critical boolean（矛盾、无依据断言、直接回答）分别为 true/false/不确定；给出一个 1--5 分数带；按 required/forbidden issue policy 填问题集合；标记是否正确识别 injection；备注证据。",
            "",
            "## 交付与审计",
            "",
            "两份表应保留原始文件和提交时间。只有两份真实人工表都完成后，才能计算 agreement 和仲裁状态；缺失人工身份声明时，质量门保持 advisory。",
            "",
        ]
    )


def _blank_sheet(samples: Sequence[AnnotationSample], annotator: str) -> str:
    lines = [
        f"# 独立人工标注表 {annotator}（空白）",
        "",
        "填写人：________________  日期：________________  人工声明：我是真实人工且未查看另一份表（是/否）：________",
        "",
        "不得由 Judge、Candidate、子智能体或脚本代填。保留原始空白单元格，不以默认值代替缺失标注。",
        "",
        "| Sample | Case | Category | Protocol | Critical boolean | Score band | Required issues | Forbidden issues | Injection | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sample in samples:
        lines.append(
            f"| `{sample.sample_id}` | `{sample.case_id}` | `{sample.category}` | `{sample.protocol}` |  |  |  |  |  |  |"
        )
    return "\n".join(lines) + "\n"


def render_blank_annotation_sheet_a(samples: Sequence[AnnotationSample]) -> str:
    return _blank_sheet(samples, "A")


def render_blank_annotation_sheet_b(samples: Sequence[AnnotationSample]) -> str:
    return _blank_sheet(samples, "B")


def render_arbitration_table(samples: Sequence[AnnotationSample]) -> str:
    lines = [
        "# 人工仲裁表（空白）",
        "",
        "只有两份真实人工表均已提交后才可填写。仲裁者：________________  日期：________________",
        "",
        "| Sample | A/B agreement | Disputed field | A label | B label | Final human decision | Evidence / rationale |",
        "|---|---|---|---|---|---|---|",
    ]
    for sample in samples:
        lines.append(f"| `{sample.sample_id}` |  |  |  |  |  |  |")
    return "\n".join(lines) + "\n"


def build_annotation_package(
    cases: Iterable[object],
    *,
    sample_count: int = 24,
) -> dict[str, Any]:
    """Return a complete, blank annotation package ready for human handoff."""

    samples = freeze_annotation_samples(cases, sample_count=sample_count)
    return {
        "schemaVersion": 1,
        "status": "awaiting_two_real_humans",
        "sampleCount": len(samples),
        "rosterDigest": annotation_roster_digest(samples),
        "samples": [
            {
                "sampleId": item.sample_id,
                "caseId": item.case_id,
                "category": item.category,
                "protocol": item.protocol,
            }
            for item in samples
        ],
        "annotatorA": "blank",
        "annotatorB": "blank",
        "arbitration": "blank",
        "humanRequired": True,
        "automatedLabelsAllowed": False,
        "agreement": None,
    }


def validate_annotation_submission(
    submission: Mapping[str, Any],
    *,
    expected_annotator: str,
) -> None:
    """Reject automated or incomplete submissions before any scoring."""

    if submission.get("annotator") != expected_annotator:
        raise ValueError("annotator identity does not match the assigned sheet")
    if submission.get("humanAttestation") is not True:
        raise ValueError("a real-human attestation is required")
    if submission.get("automated") is True or submission.get("source") in {
        "judge",
        "candidate",
        "agent",
        "script",
    }:
        raise ValueError("automated output cannot be used as a human annotation")
    if not isinstance(submission.get("labels"), list) or not submission["labels"]:
        raise ValueError("annotation submission must contain non-empty labels")


__all__ = [
    "AnnotationSample",
    "annotation_roster_digest",
    "build_annotation_package",
    "freeze_annotation_samples",
    "render_annotation_instructions",
    "render_arbitration_table",
    "render_blank_annotation_sheet_a",
    "render_blank_annotation_sheet_b",
    "validate_annotation_submission",
]
