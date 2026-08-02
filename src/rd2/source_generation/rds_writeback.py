"""승인된 생성 문서(S)를 수집 코퍼스와 같은 ``documents`` 테이블 행으로 옮긴다.

**왜 별도 모듈인가.** 두 하네스가 갈라져 있었다 —
``run_source_generation_batch.py``는 RDS에서 O 원문을 읽지만 S/O 승인 게이트가
없었고, ``run_seoul_official_batch.py``는 게이트가 있지만 입력이 로컬 파일이라
결과를 RDS로 되돌리는 자리가 아예 없었다. 합치면서 "무엇을 행으로 만들 것인가"는
스크립트 인자 파싱과 섞이면 테스트가 불가능해지므로 여기로 분리한다. 이 모듈은
DB에 접속하지 않는다 — ``Document``를 조립하기만 하고 저장은 ``DocumentStore``가
한다.

**PDF는 아직 없다.** 템플릿 작업이 끝나기 전까지는 검증기를 통과한 생성 원문과
메타데이터만 보관한다. 그래서 ``body_file_path``는 None으로 들어가고, 템플릿이
나오면 같은 행을 ``DocumentStore.update_files(dedup_key, ...)``로 백필한다 —
그 백필이 성립하려면 ``dedup_key``가 재실행에도 동일해야 하므로
``generated_source_url()``이 결정론적이어야 한다(``storage/db.py``의
``_dedup_key``는 source_url이 비면 매번 새 UUID를 붙인다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSE_LABELS,
    DocumentForm,
)
from rd2.source_generation.contracts import (
    GeneratedDocumentIR,
    GenerationPlan,
    GenerationRoute,
    SensitiveConsistencyAssessment,
    SensitivePipelineStatus,
    SensitiveVerdict,
)

#: 생성 행의 ``source`` 접두사. ``is_synthetic`` 컬럼이 2026-07-15 스키마 정리로
#: 사라져(``storage/db.py`` ``_DEPRECATED_COLUMNS``) 수집 문서와 생성 문서를
#: DB에서 구분할 컬럼이 없다. 원문 출처를 보존하면서 구분도 되도록 접두사를 쓴다 —
#: ``WHERE source LIKE 'gen\\_%'``로 생성분만, ``source = 'gen_alio'``로 출처별로
#: 고를 수 있다.
GENERATED_SOURCE_PREFIX = "gen_"

#: 메타데이터를 물려받을 원문 행이 없을 때(로컬 파일 입력) 쓰는 기관명.
#: ``ordering_agency``는 DB에서 NOT NULL이라 비워둘 수 없다.
UNKNOWN_AGENCY = "미상"


@dataclass(frozen=True)
class SourceRow:
    """생성의 입력이 된 RDS ``documents`` 행 중 물려받을 값만."""

    id: int
    source: str
    doc_type: str | None = None
    title: str | None = None
    ordering_agency: str | None = None
    department: str | None = None
    unit_task: str | None = None
    production_date: date | None = None
    subject_category: str | None = None
    body_text: str | None = None
    body_file_path: str | None = None

    @property
    def document_id(self) -> str:
        """``SourceDocumentSnapshot.source_document_id``로 쓸 값.

        원문 행 id를 그대로 담아야 생성 결과에서 원문을 역추적할 수 있다.
        파일 입력 경로는 파일명 stem을 쓰기 때문에 이 연결이 끊긴다.
        """

        return f"{self.source}-{self.id}"


def generated_source_name(source: str) -> str:
    return f"{GENERATED_SOURCE_PREFIX}{source}"


def generated_source_url(source_document_id: str, plan: GenerationPlan) -> str:
    """재실행해도 같은 값이 나오는 합성 문서 URL(= dedup_key의 재료).

    목표(호·세부조항)까지 키에 넣는다. 같은 원문에서 같은 목표를 다시 생성하면
    새 행이 생기지 않고 조용히 스킵되며(``DocumentStore.upsert``가 False 반환),
    다른 세부조항으로 생성하면 별도 행이 된다.
    """

    target = plan.final_target
    clause = target.clause_no.value if target.clause_no else "none"
    subclause = target.subclause_key.value if target.subclause_key else "none"
    return f"synthetic://source-generation/{source_document_id}/{clause}-{subclause}"


def non_disclosure_reason(plan: GenerationPlan) -> str:
    """비공개 사유 문자열.

    ``Document``는 disclosure_status가 공개가 아니면 이 값을 **요구**한다
    (models.py의 ``_require_non_disclosure_reason_when_not_open``). 동시에
    이 자리가 세부조항(subclause)을 남길 유일한 곳이다 — 테이블에는 호 단위
    ``cso_sub_clause``만 있어서 ``personnel_management`` 같은 생성 목표가
    그냥 버려진다.
    """

    target = plan.final_target
    if target.clause_no is None:
        statuses = ", ".join(
            status.value for status in target.administrative_statuses
        )
        return f"정보공개법 제9조 제1항 제5~8호 해당(행정상태: {statuses or '미상'})"

    reason = f"정보공개법 제9조 제1항 제{target.clause_no.value}호"
    if target.subclause_key is not None:
        label = SUBCLAUSE_LABELS[target.subclause_key]
        reason += f" — 세부유형: {target.subclause_key.value}({label})"
    return reason


def should_commit(
    *,
    status: SensitivePipelineStatus,
    plan: GenerationPlan,
    assessment: SensitiveConsistencyAssessment | None,
    include_weak_mask_restoration: bool = False,
) -> bool:
    """이 결과를 학습 코퍼스 행으로 만들 것인지.

    ``accepted_s``만 받는 것으로는 부족하다. ``mask_restoration`` route는
    검증기가 O를 내도 무조건 ``accepted_s``로 통과한다 — 그 route의 S 근거는
    검증기 재판독이 아니라 "실무자가 그 자리를 제N호로 가렸다"는 원문의 사람
    판단이기 때문이다(``pipeline.py`` 참고). 그런데 채운 값이 약해 본문이
    실제로는 요건에 못 미치는 경우가 실측으로 확인됐고(마스킹 자리가 이름과
    짧은 사유 두 칸뿐이라 검증기가 O), 그 문서까지 S로 학습시키면 분류기가
    오탐 쪽으로 기운다.

    DB에는 route나 검증기 판정을 남길 컬럼이 없으므로(나중에 걸러낼 수 없다)
    쓰는 시점에 제외한다. 전량이 필요하면 ``include_weak_mask_restoration``으로
    켠다 — 배치 JSONL에는 어느 쪽이든 전부 남는다.
    """

    if status != SensitivePipelineStatus.ACCEPTED_S:
        return False
    if include_weak_mask_restoration:
        return True
    if plan.generation_route != GenerationRoute.MASK_RESTORATION:
        return True
    if assessment is None:
        return False
    return assessment.sensitivity_verdict == SensitiveVerdict.ACCEPTED_S


def build_generated_document(
    *,
    document: GeneratedDocumentIR,
    plan: GenerationPlan,
    source_document_id: str,
    source_row: SourceRow | None = None,
    fallback_source: str | None = None,
    document_form: DocumentForm | None = None,
) -> Document:
    """승인된 생성 문서를 ``documents`` 행으로 조립한다.

    분류 두 값은 요청 target이 아니라 **최종 target**(``plan.final_target``)에서
    읽는다. 원문이 요청 목표를 지지하지 못하면 계획기가 판별기의 호환 세부조항으로
    조용히 대체하므로, 요청 target을 그대로 쓰면 라벨과 본문이 어긋난다.
    ``ClauseNumber``의 값이 이미 ``"5"``~``"8"``이라 ``cso_sub_clause``의 숫자만
    규약과 변환 없이 맞는다.

    메타데이터는 원문 행에서 물려받는다 — 생성물이 원문 업무 맥락을 그대로
    쓰기 때문에 기관·부서·주제분류가 바뀌지 않는다. 원문 행이 없는 로컬 파일
    입력에서는 ``fallback_source``만으로 최소 필드를 채운다.
    """

    origin_source = source_row.source if source_row is not None else fallback_source
    if not origin_source:
        raise ValueError("source_row 또는 fallback_source 중 하나는 있어야 한다")

    target = plan.final_target
    doc_type = source_row.doc_type if source_row is not None else None
    if not doc_type and document_form is not None:
        # 수집 라벨이 없으면 문서 형식을 대신 쓴다. 둘 다 문자열 컬럼이고,
        # 비워두면 파일 저장 경로(files.py)가 미분류 버킷으로 떨어진다.
        doc_type = document_form.value

    return Document(
        title=document.title,
        ordering_agency=(
            (source_row.ordering_agency if source_row is not None else None)
            or UNKNOWN_AGENCY
        ),
        department=source_row.department if source_row is not None else None,
        unit_task=source_row.unit_task if source_row is not None else None,
        production_date=(
            source_row.production_date if source_row is not None else None
        ),
        disclosure_status=DisclosureStatus.CLOSED,
        subject_category=(
            source_row.subject_category if source_row is not None else None
        ),
        body_text=document.body_text,
        # 템플릿 렌더링 전이라 PDF가 없다. 템플릿이 나오면
        # DocumentStore.update_files(dedup_key, ...)로 이 자리를 백필한다.
        body_file_path=None,
        non_disclosure_reason=non_disclosure_reason(plan),
        cso_classification=CsoClassification(target.classification.value),
        cso_sub_clause=(
            target.clause_no.value if target.clause_no is not None else None
        ),
        source=generated_source_name(origin_source),
        source_url=generated_source_url(source_document_id, plan),
        doc_type=doc_type,
        is_synthetic=True,
    )
