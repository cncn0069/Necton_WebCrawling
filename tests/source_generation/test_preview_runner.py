from __future__ import annotations

import csv
import json

from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    GeneratedDocumentIR,
    GenerationMode,
    GenerationTarget,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
    TargetClassification,
)
from scripts.generate_source_generation_previews import (
    PreviewCase,
    _case_summary,
    _content_quality_issues,
    _synthetic_public_snapshot,
    main,
    preview_cases,
)


def _clause_five_case() -> PreviewCase:
    return PreviewCase(
        case_id="clause-5",
        sample_axis="clause",
        sample_value="5",
        target=GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        ),
        required_content_markers=("75점", "380,000,000원"),
    )


def test_preview_matrix_contains_only_clauses_five_to_eight_and_admin_cases():
    cases = preview_cases()
    clause_cases = [case for case in cases if case.sample_axis == "clause"]
    admin_cases = [
        case for case in cases if case.sample_axis == "admin_status"
    ]

    assert [case.sample_value for case in clause_cases] == ["5", "6", "7", "8"]
    assert len(admin_cases) == 12
    assert all(case.target.classification == TargetClassification.S for case in cases)
    assert all(len(case.required_content_markers) >= 2 for case in cases)


def test_synthetic_preview_source_has_stable_source_block_ids():
    source = _synthetic_public_snapshot()

    assert source.source_document_id == "synthetic-public-policy-source-001"
    assert source.pages[0].blocks
    assert all(
        block.block_id.startswith("source:")
        for block in source.pages[0].blocks
    )
    assert "민원창구 5개소" in source.pages[0].blocks[1].text


def test_content_quality_rejects_meta_description_and_missing_facts():
    document = GeneratedDocumentIR(
        title="평가 검토 보고서",
        blocks=(
            ParagraphBlock(
                block_id="generated:b0",
                text="본 문서는 평가 기준에 관한 내용을 포함하고 있습니다.",
            ),
        ),
    )

    issues = _content_quality_issues(_clause_five_case(), document)

    assert "missing required content marker: 75점" in issues
    assert "missing required content marker: 380,000,000원" in issues
    assert (
        "meta-description phrase present: 내용을 포함하고 있습니다"
        in issues
    )
    assert "no structured content block" in issues
    assert "fewer than two concrete numeric facts" in issues


def test_content_quality_accepts_direct_facts_in_structured_content():
    document = GeneratedDocumentIR(
        title="평가항목 확정안",
        blocks=(
            KeyValueBlock(
                block_id="generated:kv0",
                entries=(
                    KeyValueEntry(key="기술평가 통과선", value="75점"),
                    KeyValueEntry(
                        key="협상 상한",
                        value="380,000,000원",
                    ),
                ),
            ),
        ),
    )

    assert _content_quality_issues(_clause_five_case(), document) == ()


def test_case_summary_reads_v2_stage_fields(tmp_path):
    case = _clause_five_case()
    case_dir = tmp_path / case.case_id
    case_dir.mkdir()
    payload = {
        "source_assessment": {
            "source_classification": {
                "classification": "O",
                "clause_no": None,
            }
        },
        "generation_plan": {
            "generation_route": "anchored",
            "final_target": {
                "classification": "S",
                "clause_no": "5",
                "subclause_key": "bid_contract",
            },
        },
        "generation_artifact": {
            "generated_document": {"title": "평가항목 확정안"}
        },
        "consistency_assessment": {
            "classification": "S",
            "clause_no": "5",
            "subclause_key": "bid_contract",
        },
        "comparison": {
            "classification_match": True,
            "clause_match": True,
            "subclause_match": True,
            "subject_role_match": True,
            "requires_review": False,
        },
        "failure": None,
        "content_quality": {"passed": True, "issues": []},
    }
    (case_dir / "pipeline_result.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = _case_summary(case, case_dir)

    assert summary["status"] == "ok"
    assert summary["source_classification"] == "O"
    assert summary["final_subclause_key"] == "bid_contract"
    assert summary["validation_classification"] == "S"
    assert summary["content_quality_pass"] == "True"


def test_summary_only_mode_never_requires_an_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = main(
        [
            "--summary-only",
            "--case",
            "clause-5",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    rows = list(
        csv.DictReader(
            (tmp_path / "preview_summary.csv").open(
                encoding="utf-8-sig",
                newline="",
            )
        )
    )
    assert rows[0]["case_id"] == "clause-5"
    assert rows[0]["status"] == "missing"
