"""JSONL에서 RDS 행을 다시 조립하는 적재기."""

from __future__ import annotations

import json

from rd2.source_generation.rds_writeback import SourceRow
from scripts.load_generated_documents import build_rows, load_records


def _record(**overrides) -> dict:
    """배치가 실제로 남기는 모양의 레코드."""

    target = {
        "classification": "S",
        "clause_no": "6",
        "subclause_key": "petitioner_pii",
        "administrative_statuses": [],
        "generation_mode": "counterfactual",
    }
    record = {
        "source_document_id": "alio-7269",
        "source_row_id": 7269,
        "approval_status": "accepted_s",
        "source_document_form": "official_letter",
        "generation_plan": {
            "requested_target": target,
            "final_target": target,
            "generation_route": "anchored",
            "source_assessment_sha256": "0" * 64,
            "source_sha256": "a" * 64,
            "selection_sha256": "b" * 64,
            "planner_policy_sha256": "c" * 64,
        },
        "generation_artifact": {
            "generated_document": {
                "title": "민원 처리 결과 통보",
                # 배치 dump에는 computed field가 그대로 들어 있다. 계약은
                # 입력으로는 이를 금지하므로 적재기가 빼고 검증해야 한다
                # (실측: EC2 산출물로 처음 확인).
                "body_text": "신청인 김민서의 개인 연락처는 010-1234-5678이다.",
                "blocks": [
                    {
                        "block_id": "generated:b0",
                        "kind": "paragraph",
                        "text": "신청인 김민서의 개인 연락처는 010-1234-5678이다.",
                    }
                ],
            },
        },
        "consistency_assessment": {
            "document_form": "official_letter",
            "classification": "S",
            "clause_no": "6",
            "subclause_key": "petitioner_pii",
            "evidence_spans": [
                {
                    "block_id": "generated:b0",
                    "quote": "신청인 김민서의 개인 연락처는 010-1234-5678이다.",
                }
            ],
            "rationale": "신청인과 개인 연락처가 직접 연결된다.",
            "sensitivity_verdict": "accepted_s",
            "assertions": [],
        },
    }
    record.update(overrides)
    return record


def _source_rows() -> dict[int, SourceRow]:
    return {
        7269: SourceRow(
            id=7269,
            source="alio",
            doc_type="audit_result",
            ordering_agency="한국가스공사",
        )
    }


def test_records_rebuild_into_rows_without_regenerating():
    documents, skipped = build_rows([_record()], _source_rows())

    assert not skipped
    assert len(documents) == 1
    document = documents[0]
    assert document.source == "gen_alio"
    assert document.cso_sub_clause == "6"
    assert document.ordering_agency == "한국가스공사"
    assert document.body_text.startswith("신청인 김민서")
    # 검증 근거도 JSONL에서 그대로 복원된다.
    assert "신청인과 개인 연락처가 직접 연결된다." in document.non_disclosure_reason


def test_unapproved_records_are_skipped_with_their_status():
    documents, skipped = build_rows(
        [_record(approval_status="excluded_after_retry")], _source_rows()
    )

    assert documents == []
    assert skipped == [{"id": "alio-7269", "reason": "excluded_after_retry"}]


def test_weak_mask_restoration_is_held_back_here_too():
    """배치와 같은 기준을 쓴다 — 적재기가 우회로가 되면 안 된다."""

    record = _record()
    record["generation_plan"]["generation_route"] = "mask_restoration"
    record["consistency_assessment"]["sensitivity_verdict"] = "assessed_o"
    record["consistency_assessment"]["classification"] = "O"
    record["consistency_assessment"]["clause_no"] = None
    record["consistency_assessment"]["subclause_key"] = None

    documents, skipped = build_rows([record], _source_rows())

    assert documents == []
    assert skipped[0]["reason"] == "weak_mask_restoration"

    documents, _ = build_rows(
        [record], _source_rows(), include_weak_mask_restoration=True
    )
    assert len(documents) == 1


def test_records_without_a_generated_document_are_skipped():
    record = _record()
    record.pop("generation_artifact")

    documents, skipped = build_rows([record], _source_rows())

    assert documents == []
    assert skipped[0]["reason"] == "artifact_missing"


def test_missing_source_row_falls_back_to_the_given_source(tmp_path):
    documents, _ = build_rows(
        [_record()], {}, fallback_source="alio"
    )

    assert documents[0].source == "gen_alio"
    assert documents[0].ordering_agency == "미상"


def test_load_records_reads_several_batches(tmp_path):
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    second.write_text(
        json.dumps(_record(source_document_id="alio-7270")) + "\n\n",
        encoding="utf-8",
    )

    records = load_records([first, second])

    assert [record["source_document_id"] for record in records] == [
        "alio-7269",
        "alio-7270",
    ]
