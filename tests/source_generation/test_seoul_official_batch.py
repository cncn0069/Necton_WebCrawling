from __future__ import annotations

from rd2.source_generation.classification_taxonomy import ClauseNumber
from rd2.source_generation.contracts import TargetClassification
from scripts.run_seoul_official_batch import (
    _report_section,
    _sensitive_seed,
    _targets,
)


def test_seoul_batch_targets_only_source_sensitive_clause_six():
    targets = _targets()

    assert targets
    assert all(item.classification == TargetClassification.S for item in targets)
    assert all(item.clause_no == ClauseNumber.CLAUSE_6 for item in targets)


def test_sensitive_seed_is_deterministic_and_contains_linked_fake_values():
    target = _targets()[0]

    first = _sensitive_seed("source-123", target)
    second = _sensitive_seed("source-123", target)

    assert first == second
    assert "010-" in first
    assert any(role in first for role in ("민원인", "지원자", "신청인", "조사대상자"))


def test_html_report_distinguishes_approval_from_technical_completion():
    html = _report_section(
        {
            "source_file": "원문.hwpx",
            "approval_status": "excluded_after_retry",
            "technical_succeeded": True,
            "attempt_count": 2,
            "attempts": [{"attempt": 1}, {"attempt": 2}],
        },
        1,
    )

    assert "재생성 후 제외" in html
    assert "S 학습 승인" not in html
    assert "technical_succeeded" in html
