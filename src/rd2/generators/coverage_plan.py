"""C/S 생성 Coverage Planner 핵심 라이브러리 (v1a).

docs/design-coverage-matrix-diversity-audit-20260723.md §3, §12.1, §12.2, §14.1을
구현한다. 이 모듈은 순수 함수/데이터클래스만 둔다 — DB 연결과 파일 I/O는
candidate_profile.py(프로파일링)와 generation_plan_schema.py(직렬화·게시)로
분리한다("후보 프로파일링, 셀 할당, 실행 슬롯 전개는 CLI와 LLM 호출에서 분리된
순수 library 경계로 둔다", 설계 문서 §5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence, TypeVar

from rd2.generators.agency_categories import AGENCY_CATEGORIES
from rd2.generators.clause_data import CLAUSES
from rd2.generators.template_matrix import STATUS_AWARE_TARGETS, TemplateStatusTarget


class GenerationPlanValidationError(Exception):
    """plan/cell JSON이 §4.1 typed contract를 어길 때 발생한다.

    generation_plan_schema.py의 GenerationPlan.from_dict()도 이 예외를 그대로
    쓴다 — CoverageCell 검증과 plan 전체 검증이 같은 예외 타입으로 fail-fast해야
    소비자가 하나의 except 절로 계약 위반을 잡을 수 있다.
    """


class PlanAllocationError(Exception):
    """등급/기관군 목표가 minimum_per_valid_cell 요건을 못 채울 때 fail-fast(§3 제약)."""


class CoverageKey(NamedTuple):
    classification: str
    clause_no: str
    subclause_key: str
    doc_type: str
    agency_category: str
    admin_status: str  # 적용 불가하면 빈 문자열


def coverage_cell_key(key: CoverageKey) -> str:
    """설계 문서 §4의 "C|1|legal_secret|report|central_ministry|-" 형식."""
    return (
        f"{key.classification}|{key.clause_no}|{key.subclause_key}|{key.doc_type}|"
        f"{key.agency_category}|{key.admin_status or '-'}"
    )


def classification_for_clause(clause_no: str) -> str:
    """clause_data.CLAUSES를 단일 진실 공급원으로 써서 C/S를 판별한다.

    설계 문서 자체는 ``{"1","2","3","4"} -> C`` 리터럴을 pseudocode로 쓰지만,
    "병렬 taxonomy를 만들지 않는다"는 제약에 따라 이미 존재하는
    ``ClauseDefinition.classification``(rd2.schema.models.CsoClassification)을
    재사용한다.
    """
    clause = CLAUSES.get(clause_no)
    if clause is None:
        raise ValueError(f"알 수 없는 조항 번호: {clause_no!r}")
    return clause.classification.value


def target_sort_key(target: TemplateStatusTarget) -> tuple[str, str, str, str]:
    return (target.clause_no, target.subclause_key, target.doc_type, target.admin_status or "")


# 기관군은 조합 금지 규칙이 아니라 목표 분포 축이다(설계 문서 Constraints) — 이
# 딕셔너리는 v1a에서 항상 비어 있다. 향후 실제 도메인 검증으로 특정 조합이
# 불가능하다고 확인된 경우에만 명시적 reason과 함께 채운다.
EXPLICIT_CELL_EXCLUSIONS: dict[CoverageKey, str] = {}


class CellRecord(NamedTuple):
    """enumerate_valid_cells()가 만드는, 아직 목표량을 배분하기 전인 셀 하나."""

    key: CoverageKey
    cell_state: str  # "matrix_valid" | "fallback_only" | "excluded"
    real_candidate_count: int


CandidateProfileCounts = Mapping[tuple[str, str, str, str, str], int]


def compute_candidate_profile_counts(profile_rows: Iterable[Any]) -> dict[tuple[str, str, str, str, str], int]:
    """실측 후보 profile row를 (등급,조항,세부조항,문서유형,기관군) 단위로 센다.

    admin_status는 이 키에 없다 — span 후보에는 문서별 행정상태가 붙어있지
    않다(candidate_profile.py, "Phase A가 다뤄야 할 갭" —
    scripts/generate_cs_pilot.py의 ``_bucket_span_candidates_by_subclause_doc_type``
    독스트링이 이미 명시한 동일 갭). 같은 그룹에 속한 여러 admin_status 셀은
    enumerate_valid_cells()에서 이 동일 count를 조회해 broadcast된다 —
    "실제 상태별 근거"로 오인하면 안 된다.

    profile_status가 "resolved"인 행만 센다. 기관을 못 찾은 행(unresolved)은
    어느 기관군 셀의 근거인지 알 수 없어 카운트에 넣지 않는다 — 그 문서는
    fallback으로만 채워진다.
    """
    counts: dict[tuple[str, str, str, str, str], int] = {}
    for row in profile_rows:
        if row.profile_status != "resolved":
            continue
        classification = classification_for_clause(row.clause_no)
        key = (classification, row.clause_no, row.subclause_key, row.doc_type, row.agency_category)
        counts[key] = counts.get(key, 0) + 1
    return counts


def enumerate_valid_cells(
    *,
    candidate_profile_counts: CandidateProfileCounts | None = None,
    agency_categories: Sequence[str] = AGENCY_CATEGORIES,
    exclusions: Mapping[CoverageKey, str] | None = None,
) -> list[CellRecord]:
    """설계 문서 §12.1 pseudocode: STATUS_AWARE_TARGETS × 기관군 전수 열거.

    candidate_profile_counts를 생략하면(또는 빈 dict) 모든 비제외 셀이
    fallback_only(count=0)로 나온다 — DB/프로파일 없이도 순수 열거 로직만
    테스트할 수 있게 하기 위함이다.
    """
    counts = candidate_profile_counts or {}
    exclusion_map = EXPLICIT_CELL_EXCLUSIONS if exclusions is None else exclusions
    records: list[CellRecord] = []
    for target in sorted(STATUS_AWARE_TARGETS, key=target_sort_key):
        classification = classification_for_clause(target.clause_no)
        admin_status = target.admin_status or ""
        for agency_category in sorted(agency_categories):
            key = CoverageKey(
                classification=classification,
                clause_no=target.clause_no,
                subclause_key=target.subclause_key,
                doc_type=target.doc_type,
                agency_category=agency_category,
                admin_status=admin_status,
            )
            if key in exclusion_map:
                records.append(CellRecord(key, "excluded", 0))
                continue
            count_key = (
                classification,
                target.clause_no,
                target.subclause_key,
                target.doc_type,
                agency_category,
            )
            count = counts.get(count_key, 0)
            state = "fallback_only" if count == 0 else "matrix_valid"
            records.append(CellRecord(key, state, count))
    return records


K = TypeVar("K")


def largest_remainder_allocate(
    total: int,
    weights: Mapping[K, float],
    *,
    tie_break: Callable[[K], Any],
) -> dict[K, int]:
    """exact-total largest remainder 배분(설계 문서 §12.2).

    weights의 key 집합에 total을 정수로 정확히 나눠 합이 total과 같음을
    보장한다. 동점(같은 remainder)은 tie_break(key) 오름차순으로 끊는다.
    """
    if total < 0:
        raise ValueError(f"total은 0 이상이어야 합니다: {total}")
    keys = list(weights.keys())
    if not keys:
        if total != 0:
            raise ValueError("배분할 key가 없는데 total이 0이 아닙니다")
        return {}
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise ValueError("weight 합은 0보다 커야 합니다")

    quotas = {k: total * (weights[k] / weight_sum) for k in keys}
    floors = {k: math.floor(quotas[k]) for k in keys}
    remainder = total - sum(floors.values())
    order = sorted(keys, key=lambda k: (-(quotas[k] - floors[k]), tie_break(k)))
    result = dict(floors)
    for k in order[:remainder]:
        result[k] += 1
    return result


@dataclass(frozen=True)
class CoverageCell:
    """설계 문서 §12.4 ``generation_plan.json`` cell 필드 전체."""

    classification: str
    clause_no: str
    subclause_key: str
    doc_type: str
    agency_category: str
    admin_status: str
    cell_state: str
    requested_target: int
    real_candidate_count: int
    planned_span_seeded: int
    planned_fallback: int
    candidate_shortage: int
    allocation_weight: float
    allocation_reason: str

    _VALID_CELL_STATES = ("matrix_valid", "fallback_only", "excluded")
    _VALID_CLASSIFICATIONS = ("C", "S")

    @property
    def key(self) -> CoverageKey:
        return CoverageKey(
            self.classification,
            self.clause_no,
            self.subclause_key,
            self.doc_type,
            self.agency_category,
            self.admin_status,
        )

    @property
    def coverage_cell_key(self) -> str:
        return coverage_cell_key(self.key)

    @property
    def evidence_supported(self) -> bool:
        """설계 문서 §14.1: candidate_shortage == 0."""
        return self.candidate_shortage == 0

    @property
    def fallback_ratio(self) -> float | None:
        if self.requested_target <= 0:
            return None
        return self.planned_fallback / self.requested_target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, context: str) -> "CoverageCell":
        def fail(msg: str) -> None:
            raise GenerationPlanValidationError(f"{context}: {msg}")

        if not isinstance(data, Mapping):
            fail("cell은 JSON object여야 합니다")

        required_str_fields = (
            "classification",
            "clause_no",
            "subclause_key",
            "doc_type",
            "agency_category",
            "admin_status",
            "cell_state",
            "allocation_reason",
        )
        for field in required_str_fields:
            if not isinstance(data.get(field), str):
                fail(f"필드 {field!r}가 문자열이 아니거나 없습니다")
        if data["classification"] not in cls._VALID_CLASSIFICATIONS:
            fail(f"알 수 없는 classification: {data['classification']!r}")
        if data["cell_state"] not in cls._VALID_CELL_STATES:
            fail(f"알 수 없는 cell_state: {data['cell_state']!r}")

        required_int_fields = (
            "requested_target",
            "real_candidate_count",
            "planned_span_seeded",
            "planned_fallback",
            "candidate_shortage",
        )
        values: dict[str, int] = {}
        for field in required_int_fields:
            value = data.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                fail(f"필드 {field!r}는 0 이상 정수여야 합니다: {value!r}")
            values[field] = value

        if values["planned_span_seeded"] + values["planned_fallback"] != values["requested_target"]:
            fail(
                "planned_span_seeded + planned_fallback != requested_target: "
                f"{values['planned_span_seeded']} + {values['planned_fallback']} != "
                f"{values['requested_target']}"
            )

        allocation_weight = data.get("allocation_weight")
        if not isinstance(allocation_weight, (int, float)) or isinstance(allocation_weight, bool):
            fail(f"allocation_weight는 숫자여야 합니다: {allocation_weight!r}")

        return cls(
            classification=data["classification"],
            clause_no=data["clause_no"],
            subclause_key=data["subclause_key"],
            doc_type=data["doc_type"],
            agency_category=data["agency_category"],
            admin_status=data["admin_status"],
            cell_state=data["cell_state"],
            requested_target=values["requested_target"],
            real_candidate_count=values["real_candidate_count"],
            planned_span_seeded=values["planned_span_seeded"],
            planned_fallback=values["planned_fallback"],
            candidate_shortage=values["candidate_shortage"],
            allocation_weight=float(allocation_weight),
            allocation_reason=data["allocation_reason"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_cell_key": self.coverage_cell_key,
            "classification": self.classification,
            "clause_no": self.clause_no,
            "subclause_key": self.subclause_key,
            "doc_type": self.doc_type,
            "agency_category": self.agency_category,
            "admin_status": self.admin_status,
            "cell_state": self.cell_state,
            "requested_target": self.requested_target,
            "real_candidate_count": self.real_candidate_count,
            "planned_span_seeded": self.planned_span_seeded,
            "planned_fallback": self.planned_fallback,
            "candidate_shortage": self.candidate_shortage,
            "allocation_weight": self.allocation_weight,
            "allocation_reason": self.allocation_reason,
        }


def _finalize_cell(
    record: CellRecord,
    *,
    requested_target: int,
    planned_span_seeded: int,
    planned_fallback: int,
    allocation_weight: float,
    allocation_reason: str,
    max_rows_per_candidate: int,
) -> CoverageCell:
    candidate_capacity = record.real_candidate_count * max_rows_per_candidate
    candidate_shortage = max(0, requested_target - candidate_capacity)
    return CoverageCell(
        classification=record.key.classification,
        clause_no=record.key.clause_no,
        subclause_key=record.key.subclause_key,
        doc_type=record.key.doc_type,
        agency_category=record.key.agency_category,
        admin_status=record.key.admin_status,
        cell_state=record.cell_state,
        requested_target=requested_target,
        real_candidate_count=record.real_candidate_count,
        planned_span_seeded=planned_span_seeded,
        planned_fallback=planned_fallback,
        candidate_shortage=candidate_shortage,
        allocation_weight=allocation_weight,
        allocation_reason=allocation_reason,
    )


def allocate_grade(
    classification: str,
    cell_records: Sequence[CellRecord],
    *,
    grade_target: int,
    minimum_per_valid_cell: int,
    agency_weights: Mapping[str, float],
    max_rows_per_candidate: int,
) -> list[CoverageCell]:
    """설계 문서 §12.2 + §14.1: 등급 하나(C 또는 S)의 전체 목표량 배분.

    1) grade_target을 agency_weights로 기관군별 T_a에 exact-total 배분.
    2) 기관군 a 안에서 minimum_per_valid_cell을 먼저 깔고, 남는 (T_a - n_a*m)을
       sqrt(real_candidate_count+1) 가중치로 셀에 exact-total 배분.
    3) 셀별 planned_span_seeded/planned_fallback/candidate_shortage 계산.
    """
    cells: list[CoverageCell] = []

    excluded = [r for r in cell_records if r.cell_state == "excluded"]
    for record in excluded:
        cells.append(
            _finalize_cell(
                record,
                requested_target=0,
                planned_span_seeded=0,
                planned_fallback=0,
                allocation_weight=0.0,
                allocation_reason="excluded",
                max_rows_per_candidate=max_rows_per_candidate,
            )
        )

    non_excluded = [r for r in cell_records if r.cell_state != "excluded"]
    by_agency: dict[str, list[CellRecord]] = {}
    for record in non_excluded:
        by_agency.setdefault(record.key.agency_category, []).append(record)

    if not by_agency:
        if grade_target != 0:
            raise PlanAllocationError(
                f"{classification}: 배분할 비제외 셀이 없는데 목표 {grade_target}건이 남았습니다"
            )
        return cells

    agency_totals = largest_remainder_allocate(
        grade_target,
        {agency: agency_weights[agency] for agency in by_agency},
        tie_break=lambda agency: agency,
    )

    for agency, records in sorted(by_agency.items()):
        n_a = len(records)
        t_a = agency_totals[agency]
        minimum_required = n_a * minimum_per_valid_cell
        if t_a < minimum_required:
            raise PlanAllocationError(
                f"{classification}/{agency}: 기관군 목표 {t_a}건이 최소 요건 "
                f"{minimum_required}건({n_a}개 셀 x 최소 {minimum_per_valid_cell}건)보다 "
                "작습니다 — 등급 목표를 늘리거나 minimum_per_valid_cell을 낮추세요"
            )
        remainder_pool = t_a - minimum_required
        cell_weights = {record.key: math.sqrt(record.real_candidate_count + 1) for record in records}
        weight_sum = sum(cell_weights.values())
        extra = largest_remainder_allocate(
            remainder_pool,
            cell_weights,
            tie_break=lambda key: coverage_cell_key(key),
        )
        for record in records:
            requested_target = minimum_per_valid_cell + extra[record.key]
            allocation_weight = cell_weights[record.key] / weight_sum if weight_sum else 0.0
            candidate_capacity = record.real_candidate_count * max_rows_per_candidate
            planned_span_seeded = min(requested_target, candidate_capacity)
            planned_fallback = requested_target - planned_span_seeded
            cells.append(
                _finalize_cell(
                    record,
                    requested_target=requested_target,
                    planned_span_seeded=planned_span_seeded,
                    planned_fallback=planned_fallback,
                    allocation_weight=allocation_weight,
                    allocation_reason="minimum_plus_weighted_remainder",
                    max_rows_per_candidate=max_rows_per_candidate,
                )
            )
    return cells
