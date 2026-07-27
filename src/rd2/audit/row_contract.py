"""감사 입력 계약(설계 문서 §6)과 row 단위 무결성 정책(§12.5).

여기서 다루는 건 "CSV 헤더 자체에 필수 컬럼이 있는가"(§10, 없으면 감사 전체
fail-fast)와 "row 하나가 계약을 지키는가"(§12.5, malformed row는 격리하고
나머지는 계속)뿐이다. 중복 row_id/중복 coverage slot/plan run_id 불일치처럼
전체 run을 fail-fast시켜야 하는 검사는 여기서 하지 않는다 — 스트리밍 중
누적해야 알 수 있는 전역 상태라 오케스트레이터(orchestrator.py)가 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

REQUIRED_COLUMNS = (
    "row_id",
    "status",
    "body_text",
    "title",
    "cso_classification",
    "clause_no",
    "cso_subclause_key",
    "doc_type",
    "document_status",
    "ordering_agency",
    "agency_category",
    "template_id",
    "seed_candidate_id",
    "seed_extraction_id",
    "seed_source_path",
    "coverage_cell_key",
    "coverage_slot",
    "coverage_plan_run_id",
)


class AuditContractError(Exception):
    """설계 문서 §10: CSV 필수 필드 누락, plan/manifest hash 불일치 등 계약 무결성 오류."""


def validate_header(fieldnames: Iterable[str] | None) -> None:
    present = set(fieldnames or ())
    missing = [column for column in REQUIRED_COLUMNS if column not in present]
    if missing:
        raise AuditContractError(f"generated CSV에 필수 필드가 없습니다: {missing}")


@dataclass(frozen=True)
class AuditRow:
    source_row_number: int
    row_id: str
    status: str
    body_text: str
    title: str
    classification: str
    clause_no: str
    subclause_key: str
    doc_type: str
    document_status: str
    ordering_agency: str
    agency_category: str
    template_id: str
    seed_candidate_id: str
    seed_extraction_id: str
    seed_source_path: str
    coverage_cell_key: str
    coverage_slot: str
    coverage_plan_run_id: str

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class RowError:
    source_row_number: int
    row_id: str
    error_code: str  # unknown_enum | missing_field | unknown_cell | empty_body | malformed
    field: str
    detail: str


_VALID_CLASSIFICATIONS = ("C", "S")


def parse_row(
    raw: dict, *, row_number: int, plan_cell_keys: frozenset
) -> tuple[AuditRow | None, RowError | None]:
    """CSV DictReader가 준 raw dict 하나를 검증된 AuditRow 또는 RowError로 바꾼다.

    status != "ok" 행은 §6대로 성공 coverage에서는 빠지지만 그 자체가 에러는
    아니다 — classification/coverage_cell_key 계약 검사는 status == "ok"인
    행에만 적용한다(실패한 생성 시도가 완전한 계약을 만족할 이유가 없다).
    """
    row_id = (raw.get("row_id") or "").strip()
    if not row_id:
        return None, RowError(row_number, "", "missing_field", "row_id", "row_id가 비어 있습니다")

    status = (raw.get("status") or "").strip()
    if not status:
        return None, RowError(row_number, row_id, "missing_field", "status", "status가 비어 있습니다")

    classification = (raw.get("cso_classification") or "").strip()
    clause_no = (raw.get("clause_no") or "").strip()
    subclause_key = (raw.get("cso_subclause_key") or "").strip()
    doc_type = (raw.get("doc_type") or "").strip()
    body_text = raw.get("body_text") or ""
    coverage_cell_key = raw.get("coverage_cell_key") or ""

    if status == "ok":
        if classification not in _VALID_CLASSIFICATIONS:
            return None, RowError(
                row_number, row_id, "unknown_enum", "cso_classification",
                f"알 수 없는 classification: {classification!r}",
            )
        if not body_text.strip():
            return None, RowError(
                row_number, row_id, "empty_body", "body_text",
                "status=ok인데 body_text가 비어 있습니다",
            )
        if not coverage_cell_key:
            return None, RowError(
                row_number, row_id, "missing_field", "coverage_cell_key",
                "coverage_cell_key가 비어 있습니다",
            )
        if coverage_cell_key not in plan_cell_keys:
            return None, RowError(
                row_number, row_id, "unknown_cell", "coverage_cell_key",
                f"plan에 없는 coverage cell: {coverage_cell_key!r}",
            )

    row = AuditRow(
        source_row_number=row_number,
        row_id=row_id,
        status=status,
        body_text=body_text,
        title=raw.get("title") or "",
        classification=classification,
        clause_no=clause_no,
        subclause_key=subclause_key,
        doc_type=doc_type,
        document_status=raw.get("document_status") or "",
        ordering_agency=raw.get("ordering_agency") or "",
        agency_category=raw.get("agency_category") or "",
        template_id=raw.get("template_id") or "",
        seed_candidate_id=raw.get("seed_candidate_id") or "",
        seed_extraction_id=raw.get("seed_extraction_id") or "",
        seed_source_path=raw.get("seed_source_path") or "",
        coverage_cell_key=coverage_cell_key,
        coverage_slot=raw.get("coverage_slot") or "",
        coverage_plan_run_id=raw.get("coverage_plan_run_id") or "",
    )
    return row, None
