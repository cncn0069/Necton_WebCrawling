from __future__ import annotations

from inspect import getsource
from pathlib import Path
import subprocess
import sys

from rd2.source_generation.classification_taxonomy import ClauseNumber
from rd2.source_generation.contracts import TargetClassification
from scripts.run_seoul_official_batch import (
    _report_section,
    _targets,
    main,
)


def test_seoul_batch_targets_only_source_sensitive_clause_six():
    targets = _targets()

    assert targets
    assert all(item.classification == TargetClassification.S for item in targets)
    assert all(item.clause_no == ClauseNumber.CLAUSE_6 for item in targets)


def test_seoul_batch_does_not_override_the_source_aware_seed():
    """완성된 채용·민원 시나리오를 주입하지 않고 core 조립기에 맡긴다."""

    source = getsource(main)
    assert "sensitive_seed=" not in source
    assert "_sensitive_seed" not in source


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


def test_seoul_batch_script_adds_repo_root_when_run_by_path():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "run_seoul_official_batch.py"
    code = (
        "import runpy, sys; "
        f"runpy.run_path({str(script)!r}); "
        f"assert {str(root)!r} in sys.path"
    )

    subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
