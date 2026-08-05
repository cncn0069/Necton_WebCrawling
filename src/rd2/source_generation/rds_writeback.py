"""승인된 생성 문서(S)를 수집 코퍼스와 같은 ``documents`` 테이블 행으로 옮긴다.

**왜 별도 모듈인가.** "무엇을 행으로 만들 것인가"는 스크립트 인자 파싱과 섞이면
테스트가 불가능해지므로 여기로 분리한다. 이 모듈은 DB에 접속하지 않는다 —
``Document``를 조립하기만 하고 저장은 ``DocumentStore``가 한다.

**PDF 경로는 있으면 받고, 없어도 된다.** 최소 프롬프트 루트는 행을 **렌더보다
먼저** 넣는다 — 행에 들어갈 값이 전부 생성 시점에 정해지고 렌더는 경로 하나만
더하기 때문이다. 그래서 None으로 조립한 뒤 ``DocumentStore.set_body_file_path``가
그 칸만 뒤에서 메운다. 렌더가 먼저인 호출자는 경로를 그대로 넘기면 된다
(``body_file_path`` 인자).

그래서 ``generated_source_url()``은 결정론적이어야 한다 — 나중에 같은 행을 다시
찾으려면(그리고 재실행이 새 행을 만들지 않고 조용히 스킵되려면) ``dedup_key``가
같아야 하고, ``storage/db.py``의 ``_dedup_key``는 source_url이 비면 매번 새
UUID를 붙인다.

**계획기가 없는 경로도 받는다.** 행 조립이 ``GenerationPlan``에서 읽는 것은
``final_target``과 ``generation_route`` 둘뿐이라, 그 둘을 직접 받는 길을 함께
연다(``resolve_target_and_route``). 최소 프롬프트 루트처럼 계획기가 없는
하네스가 plan의 해시 필드를 지어내지 않게 하기 위한 것이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSE_LABELS,
    DocumentForm,
)
from rd2.source_generation.contracts import (
    ConfidentialSnippet,
    GeneratedDocumentIR,
    GenerationPlan,
    GenerationRoute,
    GenerationTarget,
    SensitiveConsistencyAssessment,
    SensitivePipelineStatus,
    SensitiveVerdict,
)
from rd2.source_generation.doc_type_bucket import doc_type_for_form

#: 생성 행의 ``source`` 접두사. 수집분/생성분 구분 자체는 2026-08-04에 추가된
#: ``data_origin`` 컬럼('O'/'G')이 담당하므로 접두사는 더 이상 유일한 표시가
#: 아니지만, 원문 출처를 보존하면서 출처별로도 고를 수 있어(``source = 'gen_alio'``)
#: 그대로 둔다. 생성분 전체를 고를 때는 접두사 LIKE보다 ``WHERE data_origin = 'G'``를
#: 쓴다 — 접두사 규약을 안 따르는 옛 생성 경로(``generators/generate.py``의
#: ``synthetic-llm``)가 있어 LIKE는 전량을 잡지 못한다.
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


@dataclass(frozen=True)
class RowMetadata:
    """원문 행이 **없는** 생성 경로가 직접 들고 오는 메타데이터.

    C트랙(제1~4호)에는 물려받을 원문이 없다 — rd2 DB에 해당 기관 문서가 0건이라
    seed를 뽑을 원문 자체가 없고, 기관·부서는 템플릿이 잠근 축에서 온다
    (``c_track_templates.CaseFrame``). 그런데 ``build_generated_document``가
    이 값들을 ``SourceRow``에서만 읽어서, 아는 기관이 ``UNKNOWN_AGENCY``로
    떨어지고 부서는 NULL로 남았다. 모르는 값이 아니라 **통로가 없던 값**이다.

    필드 이름을 ``SourceRow``와 같게 둔다. 조립 쪽이 둘을 같은 이름으로 읽으면
    "원문에서 왔든 템플릿에서 왔든 이 칸에 들어간다"가 한 줄로 끝나고, 한쪽에만
    있는 칸이 생기면 그게 곧 타입 오류로 드러난다.
    """

    ordering_agency: str | None = None
    department: str | None = None
    unit_task: str | None = None
    production_date: date | None = None
    subject_category: str | None = None
    doc_type: str | None = None


def generated_source_name(source: str) -> str:
    return f"{GENERATED_SOURCE_PREFIX}{source}"


def generated_source_url(
    source_document_id: str,
    target: GenerationTarget,
) -> str:
    """재실행해도 같은 값이 나오는 합성 문서 URL(= dedup_key의 재료).

    목표(호·세부조항)까지 키에 넣는다. 같은 원문에서 같은 목표를 다시 생성하면
    새 행이 생기지 않고 조용히 스킵되며(``DocumentStore.upsert``가 False 반환),
    다른 세부조항으로 생성하면 별도 행이 된다.
    """

    clause = target.clause_no.value if target.clause_no else "none"
    subclause = target.subclause_key.value if target.subclause_key else "none"
    return f"synthetic://source-generation/{source_document_id}/{clause}-{subclause}"


#: 검증 근거에 붙일 인용 개수와 인용당 길이 상한. 근거 span은 개수 제한이 없어
#: 그대로 이으면 본문을 통째로 복사하는 행이 나온다.
MAX_EVIDENCE_QUOTES = 3
MAX_QUOTE_CHARS = 120


def _target_reason(target: GenerationTarget) -> str:
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


def non_disclosure_reason(
    target: GenerationTarget,
    generation_route: GenerationRoute,
    assessment: SensitiveConsistencyAssessment | None = None,
    snippets: Sequence[ConfidentialSnippet] = (),
    storage_reasoning: str | None = None,
) -> str:
    """비공개 사유 + 검증기가 그렇게 판정한 근거.

    ``Document``는 disclosure_status가 공개가 아니면 이 값을 **요구**한다
    (models.py의 ``_require_non_disclosure_reason_when_not_open``). 동시에
    이 자리가 생성 provenance를 남길 유일한 곳이다 — 테이블에는 호 단위
    ``cso_sub_clause``만 있어서 세부조항도, 생성 route도, 검증기 판정도
    남길 컬럼이 없다.

    **왜 컬럼을 늘리지 않고 여기 담나.** 학습에는 본문(PDF)만 쓴다는 것이
    전제다(2026-08-03 사용자 결정). 메타데이터가 모델 입력에 들어가지 않으므로
    검증기 문체가 합성 문서를 지목하는 누출 경로가 되지 않는다. 그 전제가
    바뀌어 메타데이터까지 학습에 쓰게 되면 이 필드는 라벨 누출 채널이 된다 —
    그때는 route/verdict를 별도 컬럼으로 빼야 한다.

    route와 verdict는 **문장 앞의 고정 형식**으로 둔다. 사후에
    ``WHERE non_disclosure_reason LIKE '%검증: assessed_o%'``로 근거가 약한
    행을 골라낼 수 있어야 하기 때문이다(``should_commit`` 참고).

    ``snippets``·``storage_reasoning``은 C트랙 3단계 경로가 낸 3단계 산출물이다
    (``CTrackCoTResponse``). **어느 block 때문에 비공개인지**를 여기 적는다 —
    호 번호만으로는 "이 문서가 왜 제1호인가"에 답할 수 없고, 답할 재료는 이미
    모델이 block을 지목해 내놓았는데 지금까지 행에 남지 않았다.

    S트랙의 ``assessment``와 자리가 겹치지 않는다. 저쪽은 **검증기**가 사후에
    매긴 판정이고 이쪽은 **생성기**가 쓰면서 지목한 자리다. 그래서 접두사도
    ``검증 근거:``와 ``근거 block:``으로 나눈다 — 사후에 둘을 LIKE로 갈라야
    한다.
    """

    parts = [_target_reason(target)]
    parts.append(
        f"[생성 route: {generation_route.value}"
        + (
            f" / 검증: {assessment.sensitivity_verdict.value}]"
            if assessment is not None
            else "]"
        )
    )
    if assessment is not None:
        if assessment.rationale:
            parts.append(f"검증 근거: {assessment.rationale}")
        quotes = [
            span.quote[:MAX_QUOTE_CHARS]
            for span in assessment.evidence_spans[:MAX_EVIDENCE_QUOTES]
        ]
        if quotes:
            parts.append("근거 인용: " + " | ".join(quotes))

    if snippets:
        # block_id를 앞에 세운다. 사유만 읽고도 본문의 어느 자리를 펴 봐야
        # 하는지 바로 알 수 있어야 하고, 그 자리는 ``generated_text`` envelope의
        # 같은 block_id로 그대로 찾아진다.
        parts.append(
            "근거 block: "
            + " | ".join(
                f"[{snippet.block_id}] {snippet.quote[:MAX_QUOTE_CHARS]}"
                for snippet in snippets[:MAX_EVIDENCE_QUOTES]
            )
        )
    if storage_reasoning:
        parts.append(f"저장 사유: {storage_reasoning}")
    return "\n".join(parts)


def should_commit(
    *,
    status: SensitivePipelineStatus,
    generation_route: GenerationRoute,
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
    if generation_route != GenerationRoute.MASK_RESTORATION:
        return True
    if assessment is None:
        return False
    return assessment.sensitivity_verdict == SensitiveVerdict.ACCEPTED_S


def resolve_target_and_route(
    *,
    plan: GenerationPlan | None,
    target: GenerationTarget | None,
    generation_route: GenerationRoute | None,
) -> tuple[GenerationTarget, GenerationRoute]:
    """행을 조립하는 데 실제로 필요한 두 조각만 꺼낸다.

    ``GenerationPlan``은 계획기가 돌아야 나오는 물건이다 — 해시 세 개와 정책
    해시를 품고 있고, 그 값들은 "계획기 v3가 이 입력으로 만들었다"는 주장이다.
    그런데 행 조립이 plan에서 읽는 것은 ``final_target``과 ``generation_route``
    둘뿐이다. 계획기가 없는 경로(최소 프롬프트 루트)까지 plan을 요구하면 해시를
    지어내야 하고, 그러면 provenance가 거짓이 된다. 그래서 두 조각을 직접 받는
    길을 연다.

    plan을 주면 거기서 뽑고, 아니면 target과 route를 직접 받는다. 둘 다 주거나
    둘 다 없으면 거부한다 — 조용히 한쪽을 이기게 하면 어느 값으로 라벨이
    붙었는지 호출부만 봐서는 알 수 없다.
    """

    if plan is not None:
        if target is not None or generation_route is not None:
            raise ValueError(
                "plan과 target/generation_route를 함께 줄 수 없다"
            )
        return plan.final_target, plan.generation_route
    if target is None or generation_route is None:
        raise ValueError(
            "plan을 주지 않으면 target과 generation_route가 모두 있어야 한다"
        )
    return target, generation_route


def build_generated_document(
    *,
    document: GeneratedDocumentIR,
    source_document_id: str,
    plan: GenerationPlan | None = None,
    target: GenerationTarget | None = None,
    generation_route: GenerationRoute | None = None,
    source_row: SourceRow | None = None,
    metadata: RowMetadata | None = None,
    fallback_source: str | None = None,
    document_form: DocumentForm | None = None,
    assessment: SensitiveConsistencyAssessment | None = None,
    snippets: Sequence[ConfidentialSnippet] = (),
    storage_reasoning: str | None = None,
    input_prompt: str | None = None,
    content: str | None = None,
    body_file_path: str | None = None,
    generated_text_json: Mapping[str, object] | None = None,
    store_generated_body_text: bool = True,
) -> Document:
    """승인된 생성 문서를 ``documents`` 행으로 조립한다.

    목표는 ``plan``(계획기 경로) 또는 ``target``+``generation_route``(계획기가
    없는 경로)로 받는다 — ``resolve_target_and_route`` 참고.

    plan으로 받을 때 분류 두 값은 요청 target이 아니라 **최종 target**
    (``plan.final_target``)에서 읽는다. 원문이 요청 목표를 지지하지 못하면
    계획기가 판별기의 호환 세부조항으로 조용히 대체하므로, 요청 target을 그대로
    쓰면 라벨과 본문이 어긋난다. ``ClauseNumber``의 값이 이미 ``"5"``~``"8"``이라
    ``cso_sub_clause``의 숫자만 규약과 변환 없이 맞는다.

    메타데이터는 원문 행에서 물려받는다 — 생성물이 원문 업무 맥락을 그대로
    쓰기 때문에 기관·부서·주제분류가 바뀌지 않는다. 원문 행이 없는 로컬 파일
    입력에서는 ``fallback_source``만으로 최소 필드를 채운다.

    원문이 **아예 없는** 경로(C트랙)는 ``metadata``로 같은 칸을 직접 채운다 —
    ``RowMetadata`` 참고. ``source_row``와 함께 주는 것은 거부한다. 둘 다 값이
    있으면 어느 쪽이 이겼는지가 산출물만 봐서는 드러나지 않고, 그 착각은 기관이
    한 칸 어긋난 행으로 남는다.

    ``input_prompt``·``content``는 생성 provenance다. 주지 않으면 NULL로 남는다 —
    호출자가 프롬프트를 손에 쥐고 있지 않을 수 있고(재조립 경로), 그때 빈 문자열을
    넣으면 "프롬프트 없이 만든 문서"와 구분되지 않는다.

    **생성물은 ``generated_text`` 한 칸이다.** 잠시 세 칸이던 때가 있었다 —
    평문은 ``generated_text``, 문서 IR은 ``pdf_renderd_json``, 배치 좌표는
    ``batch_json``. 그 둘은 RDS에 ADD되기 전에 스키마에서 빠졌다(2026-08-05
    사용자 결정). 읽는 쪽이 세 칸을 조인해야 했고 그중 하나만 비어도 조용히
    반쪽이 됐기 때문이다.

    ``generated_text_json``을 주면 이 칸이 평문이 아니라 그 값을 직렬화한
    JSON이 된다. 소비하는 쪽이 본문만이 아니라 **문서유형·조항·IR까지 한
    칸에서** 읽어야 하는 경로를 위한 자리이고, C트랙이 그렇게 쓴다
    (``c_track_result_envelope``). 주지 않으면 지금까지처럼 평문이 들어간다.

    평문이 사라지는 것이 아니다 — envelope의 IR이 그 평문을 만든 원본이므로,
    같은 값을 두 칸에 두지 않는다는 규약이 여기서도 그대로다.

    ``body_file_path``는 렌더된 PDF 경로다. upsert가 렌더 이후인 하네스는 그 시점에
    경로를 이미 쥐고 있으므로 여기서 함께 넣고, 행을 렌더보다 먼저 넣는 최소
    프롬프트 루트는 None으로 둔 뒤 ``DocumentStore.set_body_file_path``로 채운다.
    어느 쪽이든 렌더가 안 된 문서는 경로가 NULL인 것으로 그 행이 식별된다.

    옛 계약 버전을 받는 인자는 여기 없다. 그 값이 되살아나던 자리가
    ``pdf_renderd_json``이었고 그 칸이 없어졌기 때문이다. IR을 싣는 경로는
    envelope을 만들 때 스스로 되살린다(``c_track_result_envelope``의
    ``contract_version``) — 되살릴 곳을 아는 쪽이 되살린다.

    ``ref_id``는 원문 행 id다. ``source_row``가 없으면 참조할 행 자체가 없으므로
    (로컬 파일 입력) None이 된다 — 그 경우 원문 연결은 ``source_url`` 문자열의
    ``source_document_id``에만 남는다.

    ``store_generated_body_text``는 생성 평문을 ``body_text``에도 둘지다.
    **컬럼 규약은 "생성분은 ``generated_text``, ``body_text``는 원문"**이고,
    그 규약대로면 이 값은 False여야 한다. 기본값이 True인 것은 5~8호 경로가
    지금까지 생성 평문을 이 칸에 넣어 왔고(기존 행이 그 상태다) 기본값을 뒤집는
    순간 같은 컬럼에 두 규약이 섞이기 때문이다 — 어느 쪽으로 통일할지는 기존
    행을 함께 옮기는 결정이라 사람이 정한다. 원문이 아예 없는 C트랙은 넣을
    원문이 없으므로 False로 부르고 ``body_text``는 NULL로 남는다.
    """

    if source_row is not None and metadata is not None:
        raise ValueError("source_row와 metadata는 함께 줄 수 없다")

    origin_source = source_row.source if source_row is not None else fallback_source
    if not origin_source:
        raise ValueError("source_row 또는 fallback_source 중 하나는 있어야 한다")

    #: 물려받을 값의 출처. 둘은 필드 이름이 같아 이 아래로는 구분이 필요 없다.
    inherited: SourceRow | RowMetadata = (
        source_row if source_row is not None else (metadata or RowMetadata())
    )

    target, route = resolve_target_and_route(
        plan=plan,
        target=target,
        generation_route=generation_route,
    )
    # **형식이 원문 라벨을 이긴다.** 이 함수가 만드는 행은 전부 생성분
    # (``generated_text``가 차 있고 ``data_origin='G'``)이고, 생성분의
    # ``doc_type``은 6칸 한글이라는 것이 컬럼 규약이다. 물려받은 값을 먼저
    # 읽던 때는 원문에서 온 경로만 영어 코드(``official_document``)로 들어가
    # 같은 ``WHERE data_origin='G'`` 안에 두 어휘가 섞였다.
    #
    # **형식 값을 그대로 넣지도 않는다.** 생성 쪽 17종을 6칸으로 몰아주는 것이
    # 마지막에 붙는 어댑터의 일이다(``doc_type_bucket``). 원래 형식은 생성물
    # envelope의 ``source_classification.document_form``에 그대로 남아 있으므로
    # 여기서 합쳐도 되짚을 수 있다.
    #
    # 형식을 모르는 호출만 원문 라벨로 떨어진다. 비워두면 파일 저장 경로
    # (files.py)가 미분류 버킷으로 떨어지므로 아무것도 없는 것보다는 낫다.
    doc_type = (
        doc_type_for_form(document_form)
        if document_form is not None
        else inherited.doc_type
    )

    return Document(
        title=document.title,
        ordering_agency=inherited.ordering_agency or UNKNOWN_AGENCY,
        department=inherited.department,
        unit_task=inherited.unit_task,
        production_date=inherited.production_date,
        disclosure_status=DisclosureStatus.CLOSED,
        subject_category=inherited.subject_category,
        body_text=document.body_text if store_generated_body_text else None,
        body_file_path=body_file_path,
        non_disclosure_reason=non_disclosure_reason(
            target,
            route,
            assessment,
            snippets=snippets,
            storage_reasoning=storage_reasoning,
        ),
        cso_classification=CsoClassification(target.classification.value),
        cso_sub_clause=(
            target.clause_no.value if target.clause_no is not None else None
        ),
        source=generated_source_name(origin_source),
        source_url=generated_source_url(source_document_id, target),
        doc_type=doc_type,
        is_synthetic=True,
        input_prompt=input_prompt,
        content=content,
        generated_text=(
            document.body_text
            if generated_text_json is None
            else json.dumps(dict(generated_text_json), ensure_ascii=False)
        ),
        ref_id=source_row.id if source_row is not None else None,
    )
