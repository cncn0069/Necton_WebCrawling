from __future__ import annotations

from datetime import date

from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SubclauseKey,
    SUBCLAUSES_BY_CLAUSE,
)
from rd2.source_generation.contracts import (
    GenerationMode,
    GenerationTarget,
    TargetClassification,
)
from rd2.source_generation.legacy_synthetic import (
    FullySyntheticContext,
    LegacySensitiveSyntheticGenerator,
    SENSITIVE_SYNTHETIC_PROFILES,
    legacy_document_to_ir,
)


def _legacy_document(body_text: str) -> Document:
    return Document(
        title="입찰 평가 검토안",
        ordering_agency="조달청",
        production_date=date(2025, 1, 15),
        disclosure_status=DisclosureStatus.CLOSED,
        non_disclosure_reason="제5호",
        subject_category="입찰계약",
        body_text=body_text,
        cso_classification=CsoClassification.S,
        cso_sub_clause="5",
        source="synthetic",
        is_synthetic=True,
    )


def _bid_target() -> GenerationTarget:
    return GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def test_profiles_cover_every_sensitive_subclause_from_clause_5_through_8():
    expected = {
        (clause.value, subclause.value)
        for clause, subclauses in SUBCLAUSES_BY_CLAUSE.items()
        if clause in {
            ClauseNumber.CLAUSE_5,
            ClauseNumber.CLAUSE_6,
            ClauseNumber.CLAUSE_7,
            ClauseNumber.CLAUSE_8,
        }
        for subclause in subclauses
    }

    assert set(SENSITIVE_SYNTHETIC_PROFILES) == expected


def test_legacy_body_is_converted_to_key_value_bullet_and_paragraph_blocks():
    document = _legacy_document(
        "사업명: 차세대 조달시스템\n"
        "예정가격: 8억 4천만원\n\n"
        "- 기술평가 배점 70점\n"
        "- 가격평가 배점 30점\n\n"
        "평가위원 후보 명단은 제안서 평가 종료 전까지 비공개로 관리한다."
    )

    result = legacy_document_to_ir(document)

    assert [block.kind for block in result.blocks] == [
        "key_value",
        "key_value",
        "bullet_list",
        "paragraph",
    ]
    assert result.blocks[0].render_text() == "기관명: 조달청\n생산일자: 2025-01-15"
    assert "예정가격: 8억 4천만원" in result.body_text
    assert "기술평가 배점 70점" in result.body_text


def test_bid_contract_adapter_passes_only_target_specific_source_free_guidance():
    calls = []

    def fake_factory(clause_no, **kwargs):
        calls.append((clause_no, kwargs))
        return _legacy_document(
            "사업명: 디지털 조달 고도화\n"
            "예정가격: 8억 4천만원\n\n"
            "제안서 평가 세부 배점은 기술 70점, 가격 30점으로 검토 중이다."
        )

    generator = LegacySensitiveSyntheticGenerator(document_factory=fake_factory)
    result = generator.generate(
        target=_bid_target(),
        context=FullySyntheticContext(
            scenario_id="s5-bid-001",
            ordering_agency="조달청",
            production_date="2025-01-15",
        ),
    )

    assert result.title == "입찰 평가 검토안"
    assert calls[0][0] == "5"
    kwargs = calls[0][1]
    assert kwargs["scenario_index"] == 1
    guidance = kwargs["generation_guidance"]
    assert "공개 원문을 사용하지 않는 완전 합성" in guidance
    assert "S / 정보공개법 제5호 / bid_contract" in guidance
    assert "예정가격" in guidance
    assert "원문 인용" in guidance
    assert "source" not in kwargs
