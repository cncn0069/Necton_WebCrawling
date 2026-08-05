"""C트랙(제1~4호) 생성물을 ``documents`` 행으로 옮길 때 쓰는 값들.

``rds_writeback``이 조립을 맡고, 이 모듈은 **C트랙에만 있는 사정**을 그쪽 인자
모양으로 번역한다. 사정은 하나다 — **원문이 없다.**

5~8호 경로는 원문 행에서 기관·부서·주제분류를 물려받는다. 1~4호는 rd2 DB에 해당
기관 문서가 0건이라 seed를 뽑을 원문 자체가 없고, 엔트로피가 전부
``expand_cases``가 전개하는 사건 프레임에서 나온다. 그래서 세 자리가 달라진다:

    1. 메타데이터는 ``CaseFrame``에서 온다 (``RowMetadata``)
    2. ``content``·``ref_id``는 **NULL로 남긴다** — 참조한 원문이 없다는 사실
       자체가 값이다. 빈 문자열이나 0을 넣으면 "원문을 봤는데 비어 있었다"와
       구분되지 않는다.
    3. route는 ``fully_synthetic``이다. 원문 골격을 유지하는
       ``source_aligned``(최소 경로)와 달리 문서를 통째로 지어낸다.

``source_url``은 ``(세부조항, 기관, seed, case_index)`` 좌표로 만든다. 그 넷이
같으면 ``expand_cases``가 같은 프레임을 내므로, 같은 건을 다시 생성해도 새 행이
생기지 않고 ``DocumentStore.upsert``가 조용히 스킵한다.
"""

from __future__ import annotations

from typing import Any, Sequence

from rd2.disclosure.military_secret import MILITARY_SECRET_GRADES
from rd2.schema.models import Document
from rd2.source_generation.c_track_templates import (
    C_TRACK_TEMPLATE_VERSION,
    CaseFrame,
    CTrackTemplate,
)
from rd2.source_generation.classification_taxonomy import (
    clause_of_subclause,
    expected_classification,
)
from rd2.source_generation.contracts import (
    CONTRACT_SCHEMA_VERSION,
    ConfidentialSnippet,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    TargetClassification,
)
from rd2.source_generation.rds_writeback import RowMetadata, build_generated_document

#: 원문 골격이 없다. ``MINIMAL_GENERATION_ROUTE``(source_aligned)와 갈리는 자리다.
C_TRACK_GENERATION_ROUTE = GenerationRoute.FULLY_SYNTHETIC

#: ``fallback_source``. 행에는 ``gen_`` 접두사가 붙어 ``gen_c_track``으로 남는다.
#: 수집 출처 이름이 아니라 **생성 경로 이름**이다 — 물려받을 출처가 없으니
#: 이 자리가 가리킬 수 있는 것은 무엇으로 만들었는지뿐이다.
C_TRACK_SOURCE = "c_track"


def c_track_generation_target(frame: CaseFrame) -> GenerationTarget:
    """사건 프레임을 생성 목표로 고정한다.

    조항과 분류는 세부조항에서 유도한다(``minimal_generation_target``과 같은
    이유 — 손으로 ``"C"``를 박으면 정합성 검사와 어긋날 자리가 생긴다).

    ``security_grade``는 군사기밀 등급(1~3급)일 때만 ``military_secret_grade``에
    싣는다. 비군사기관 축의 값은 '대외비'인데 그건 군사기밀 등급이 아니라 문서
    표기라 계약이 받지 않는다. 그 값이 가던 ``batch_json``이 없어졌으므로
    '대외비'는 이제 행에 남지 않는다 — 좌표(``source_url``의 seed·case_index)로
    프레임을 다시 세우면 나온다.
    """

    clause_no = clause_of_subclause(frame.subclause_key)
    grade = frame.security_grade if frame.security_grade in MILITARY_SECRET_GRADES else None
    return GenerationTarget(
        classification=TargetClassification(expected_classification(clause_no).value),
        clause_no=clause_no,
        subclause_key=frame.subclause_key,
        generation_mode=GenerationMode.COUNTERFACTUAL,
        military_secret_grade=grade,
    )


def c_track_source_document_id(frame: CaseFrame, *, seed: int) -> str:
    """``source_url``의 재료. 같은 좌표면 항상 같은 값이 나온다.

    원문 id 자리를 대신한다. 5~8호는 여기에 ``seoul_opengov-19510``처럼 원문
    행을 가리키는 값이 오는데, C트랙에는 가리킬 원문이 없으므로 **그 문서를
    다시 만들 수 있는 좌표**를 넣는다. ``seed``가 빠지면 다른 전개에서 나온
    같은 인덱스의 문서가 같은 행으로 뭉개진다.
    """

    return (
        f"c-track/{frame.subclause_key.value}/{frame.agency}"
        f"/seed{seed}-case{frame.case_index}"
    )


def c_track_row_metadata(frame: CaseFrame) -> RowMetadata:
    """표제부에 찍히는 값들을 프레임에서 꺼낸다.

    ``unit_task``에 사안(``frame.subject``)이 온다. 단위업무가 곧 그 문서를
    만들게 한 업무이고, C트랙에서 그 자리를 아는 것은 사안축뿐이다.
    ``subject_category``(주제분류)는 비운다 — 수집 코퍼스의 그 칸은 출처가 준
    분류체계 값이라, 사안 이름을 밀어 넣으면 같은 컬럼에 두 가지 어휘가 섞인다.

    ``production_date``도 비운다. 사건 프레임에 날짜축이 없다. 지어내면 그
    날짜로 정렬·필터한 결과가 조용히 틀린다.

    ``doc_type``도 여기서 채우지 않는다. 예전에는 ``frame.document_form.value``를
    실었는데, 그 값이 ``build_generated_document``에서 형식→6칸 어댑터
    (``doc_type_bucket``)보다 **먼저** 읽히는 자리라 C트랙만 어댑터를 우회했다.
    형식은 ``document_form`` 인자로 이미 넘어가므로 이 칸을 비워 두면 컬럼 값이
    한 곳에서만 정해진다.
    """

    return RowMetadata(
        ordering_agency=frame.agency,
        department=frame.department,
        unit_task=frame.subject,
    )


def c_track_result_envelope(
    frame: CaseFrame,
    document: GeneratedDocumentIR,
    *,
    contract_version: str | None = None,
) -> dict[str, Any]:
    """이 행의 생성물 전체를 담는 ``result`` envelope. ``generated_text``로 간다.

    **한 칸에 모으는 이유.** 소비하는 쪽이 본문만으로는 이 문서가 무엇인지
    모른다 — 문서형식과 조항이 IR 밖에 있다. 세 칸(평문·IR·좌표)으로 나눠
    두면 읽는 쪽이 세 칸을 조인해야 하고, 그중 하나만 비어도 조용히 반쪽이
    된다. envelope 하나로 두면 그 행의 생성물은 그 칸 하나로 완결된다
    (2026-08-05 사용자 결정).

    이름이 ``render_envelope``이던 때는 이 값이 ``pdf_renderd_json``으로
    갔다. 가는 칸이 바뀌었으므로 이름도 바꾼다 — 렌더러 전용이 아니다.

    렌더러도 여전히 이 값을 읽으면 된다. 문서 IR만으로는 서식을 못 고른다 —
    어떤 문서유형인지
    (``source_classification.document_type``)와 어떤 등급 표기를 붙일지
    (``generation_target.military_secret_grade``)가 IR 밖에 있다. 그래서 IR을
    맨몸으로 두지 않고 그 둘을 함께 싸서 한 칸에 담는다.

    ``minimal_envelope.build_generation_envelope``와 같은 모양이다. 저쪽은 원문
    ``doc_type``(수집 코퍼스의 ``SemanticDocumentType`` 26종)을 그대로 싣지만,
    C트랙은 물려받을 원문이 없어 **``DocumentForm`` 값을 그 이름 그대로** 싣는다.

    두 축은 값이 5개만 겹치고, C트랙이 쓰는 ``legal_review``·
    ``inspection_report``·``response_plan``·``investigation_report`` 넷은 수집
    코퍼스에 아예 없던 유형이다. 가까운 수집 타입으로 바꿔 싣는 길도 있었지만
    그건 관측이 아니라 추측이고, 추측한 값으로 고른 서식은 산출물만 봐서는
    틀린 줄 모른다. 여기서는 **만든 그대로의 이름**을 넘기고, 렌더러 쪽이 그
    값을 알게 하는 것을 택했다(2026-08-05 사용자 결정).

    ``generation_target``은 ``GenerationTarget``을 그대로 dump한다. 손으로 두세
    칸만 골라 적으면 계약이 늘어날 때 이 자리가 조용히 뒤처진다.
    """

    target = c_track_generation_target(frame)
    dumped = document.model_dump(mode="json", exclude_computed_fields=True)
    if contract_version:
        dumped["contract_version"] = contract_version

    return {
        "result": {
            "contract_version": CONTRACT_SCHEMA_VERSION,
            "generation_route": C_TRACK_GENERATION_ROUTE.value,
            "source_classification": {
                "document_type": frame.document_form.value,
                # 같은 값이지만 두 이름으로 둔다 — 이 행의 문서유형이 수집
                # 코퍼스 타입이 아니라 생성 축의 문서형식에서 왔다는 사실이
                # envelope 안에 남아야 한다.
                "document_form": frame.document_form.value,
            },
            "generation_target": target.model_dump(mode="json", exclude_none=True),
            "generated_document": dumped,
        }
    }


def build_c_track_row(
    *,
    template: CTrackTemplate,
    frame: CaseFrame,
    document: GeneratedDocumentIR,
    seed: int,
    system_prompt: str,
    user_prompt: str,
    snippets: Sequence[ConfidentialSnippet] = (),
    storage_reasoning: str | None = None,
    prompt_version: str | None = None,
    document_contract_version: str | None = None,
) -> Document:
    """C트랙 생성 결과 하나를 ``documents`` 행으로 조립한다.

    **생성 직후와 사후 되쓰기가 같은 함수를 부른다.** 두 자리가 각자 조립하면
    같은 문서가 언제 저장됐느냐에 따라 다른 행이 되는데, 그 차이는 행만 봐서는
    드러나지 않는다. 되쓰기 스크립트가 하는 일은 기록에서 여기 인자를 복원하는
    것뿐이고(``writeback_c_track_to_rds``), 생성 스크립트는 이미 손에 쥔 값을
    그대로 넘긴다.

    ``document_contract_version``은 옛 기록을 현재 계약으로 맞춰 읽었을 때만
    준다 — 새로 생성한 문서에는 필요 없다.
    """

    return build_generated_document(
        document=document,
        source_document_id=c_track_source_document_id(frame, seed=seed),
        target=c_track_generation_target(frame),
        generation_route=C_TRACK_GENERATION_ROUTE,
        metadata=c_track_row_metadata(frame),
        fallback_source=C_TRACK_SOURCE,
        document_form=frame.document_form,
        # 3단계 경로의 3단계 산출물. 어느 block 때문에 비공개인지가 여기서
        # non_disclosure_reason으로 들어간다. 단일 출력 경로에는 없다.
        snippets=snippets,
        storage_reasoning=storage_reasoning,
        input_prompt=f"{system_prompt}\n\n{user_prompt}",
        # 참조한 원문이 없다. content/ref_id는 NULL로 남는다.
        #
        # ``batch_json``·``pdf_renderd_json``은 주지 않는다 — 둘 다 NULL로
        # 남는다. 이 행의 생성물은 ``generated_text``의 envelope 하나로 완결되고,
        # 거기 문서형식·조항·IR이 다 들어 있다(2026-08-05 사용자 결정). 배치
        # 좌표(부서·단계·슬롯값)는 행에서 빠지지만 ``source_url``의
        # ``seed``·``case_index``로 같은 프레임을 다시 전개할 수 있다.
        generated_text_json=c_track_result_envelope(
            frame, document, contract_version=document_contract_version
        ),
        # body_text는 원문 자리이고, C트랙에는 그 원문이 없다.
        store_generated_body_text=False,
    )
