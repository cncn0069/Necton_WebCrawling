from __future__ import annotations

import pytest

from rd2.generators.payload_document_type import (
    apply_payload_document_type,
    resolve_payload_document_type,
)
from rd2.source_generation.classification_taxonomy import DocumentForm


_FORM_DEFAULTS = {
    "meeting_minutes": "meeting_minutes",
    "official_letter": "official_document",
    "report": "status_report",
    "audit_material": "status_report",
    "personnel_material": "personnel",
    "bid_material": "official_document",
    "approval_request": "approval",
    "reply_notice": "reply_notification",
    "policy_material": "policy_material",
    "plan_draft": "plan",
    "legal_review": "research_report",
    "inspection_report": "status_report",
    "response_plan": "plan",
    "investigation_report": "research_report",
    "press_release": "press_release",
    "administrative_rule": "official_document",
    "other": "official_document",
}


def _payload(
    *,
    document_form: str,
    title: str,
    lead: str = "본문",
    document_type: str | None = None,
) -> dict:
    classification = (
        {"document_type": document_type}
        if document_type is not None
        else None
    )
    return {
        "provenance": {"document_form": document_form},
        "result": {
            "source_classification": classification,
            "generated_document": {
                "title": title,
                "blocks": [
                    {"kind": "paragraph", "block_id": "p1", "text": lead}
                ],
            },
        },
    }


def test_explicit_document_type_always_wins() -> None:
    payload = _payload(
        document_form="report",
        title="연구 분석 보고서",
        document_type="guide",
    )

    resolution = resolve_payload_document_type(payload)

    assert resolution.document_type == "guide"
    assert resolution.source == "explicit"
    assert apply_payload_document_type(payload, resolution) is payload


@pytest.mark.parametrize("invalid_value", [" ", 123, None])
def test_invalid_explicit_document_type_is_preserved_for_contract_rejection(
    invalid_value: object,
) -> None:
    payload = _payload(
        document_form="meeting_minutes",
        title="정기 회의록",
    )
    payload["result"]["source_classification"] = {
        "document_type": invalid_value
    }

    resolution = resolve_payload_document_type(payload)

    assert resolution.source == "explicit"
    assert apply_payload_document_type(payload, resolution) is payload
    assert payload["result"]["source_classification"]["document_type"] == (
        invalid_value
    )


def test_direct_document_form_mapping() -> None:
    resolution = resolve_payload_document_type(
        _payload(document_form="meeting_minutes", title="정기 회의록")
    )

    assert resolution.document_type == "meeting_minutes"
    assert resolution.reason == "document_form:meeting_minutes"


@pytest.mark.parametrize(
    ("document_form", "expected"),
    sorted(_FORM_DEFAULTS.items()),
)
def test_every_document_form_has_a_conservative_default(
    document_form: str,
    expected: str,
) -> None:
    resolution = resolve_payload_document_type(
        _payload(document_form=document_form, title="일반 문서")
    )

    assert resolution.document_type == expected
    assert resolution.source == "inferred"


def test_document_form_default_table_covers_the_complete_enum() -> None:
    assert set(_FORM_DEFAULTS) == {form.value for form in DocumentForm}


def test_report_title_disambiguates_research_and_status() -> None:
    research = resolve_payload_document_type(
        _payload(document_form="report", title="정책 타당성 검토 보고서")
    )
    status = resolve_payload_document_type(
        _payload(document_form="report", title="사업 추진 현황 보고서")
    )

    assert research.document_type == "research_report"
    assert status.document_type == "status_report"


def test_bid_material_requires_a_public_notice_signal() -> None:
    internal = resolve_payload_document_type(
        _payload(document_form="bid_material", title="입찰 평가 자료(안)")
    )
    renotice = resolve_payload_document_type(
        _payload(document_form="bid_material", title="용역 입찰 재공고(안)")
    )
    public_offering = resolve_payload_document_type(
        _payload(document_form="bid_material", title="수행기관 공모(안)")
    )

    assert internal.document_type == "official_document"
    assert renotice.document_type == "bid_renotice"
    assert public_offering.document_type == "public_offering"


@pytest.mark.parametrize(
    ("title", "lead", "expected"),
    [
        ("사전규격 공개", "본문", "pre_spec_notice"),
        ("용역 입찰공고", "본문", "bid_notice"),
        ("입찰 자료 초안", "입찰공고 자료입니다.", "bid_notice"),
    ],
)
def test_bid_material_recognizes_other_public_notice_signals(
    title: str,
    lead: str,
    expected: str,
) -> None:
    resolution = resolve_payload_document_type(
        _payload(document_form="bid_material", title=title, lead=lead)
    )

    assert resolution.document_type == expected


def test_policy_title_routes_guide_and_interpretation_compilation() -> None:
    guide = resolve_payload_document_type(
        _payload(document_form="policy_material", title="민원 업무 가이드라인")
    )
    compilation = resolve_payload_document_type(
        _payload(document_form="policy_material", title="근로기준 질의회시집")
    )

    assert guide.document_type == "guide"
    assert compilation.document_type == "interpretation_compilation"


def test_ambiguous_administrative_rule_does_not_invent_a_subtype() -> None:
    ambiguous = resolve_payload_document_type(
        _payload(document_form="administrative_rule", title="감사규칙(안)")
    )
    notification = resolve_payload_document_type(
        _payload(document_form="administrative_rule", title="도로안전 고시(안)")
    )

    assert ambiguous.document_type == "official_document"
    assert notification.document_type == "notification"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("기관 운영 훈령", "directive"),
        ("계약 업무 예규", "regulation"),
    ],
)
def test_administrative_rule_recognizes_explicit_subtypes(
    title: str,
    expected: str,
) -> None:
    resolution = resolve_payload_document_type(
        _payload(document_form="administrative_rule", title=title)
    )

    assert resolution.document_type == expected


@pytest.mark.parametrize(
    ("title", "lead", "expected"),
    [
        ("정기 회의록", "본문", "meeting_minutes"),
        ("정책 보도자료", "본문", "press_release"),
        ("업무 처리 가이드", "본문", "guide"),
        ("공사 입찰공고", "본문", "bid_notice"),
        ("타당성 검토 보고서", "본문", "research_report"),
        ("사업 운영 현황", "본문", "status_report"),
        ("기준 검토자료", "본문", "research_report"),
        ("서식 불명 자료", "본문", "official_document"),
    ],
)
def test_other_form_uses_only_strong_content_signals(
    title: str,
    lead: str,
    expected: str,
) -> None:
    resolution = resolve_payload_document_type(
        _payload(document_form="other", title=title, lead=lead)
    )

    assert resolution.document_type == expected


def test_result_provenance_is_used_when_top_level_provenance_is_missing() -> None:
    payload = _payload(document_form="meeting_minutes", title="정기 회의록")
    payload["result"]["provenance"] = payload.pop("provenance")

    resolution = resolve_payload_document_type(payload)

    assert resolution.document_form == "meeting_minutes"
    assert resolution.document_type == "meeting_minutes"


def test_validated_classification_form_wins_and_records_provenance_conflict() -> None:
    payload = _payload(document_form="press_release", title="정기 회의록")
    payload["result"]["source_classification"] = {
        "document_form": "meeting_minutes"
    }

    resolution = resolve_payload_document_type(payload)
    projected = apply_payload_document_type(payload, resolution)

    assert resolution.document_form == "meeting_minutes"
    assert resolution.document_type == "meeting_minutes"
    assert "document_form conflict" in resolution.reason
    assert projected["result"]["source_classification"] == {
        "document_form": "meeting_minutes",
        "document_type": "meeting_minutes",
    }


def test_missing_form_stays_unclassified() -> None:
    payload = _payload(document_form="other", title="정기 회의록")
    payload.pop("provenance")

    resolution = resolve_payload_document_type(payload)

    assert resolution.document_type is None
    assert resolution.source == "unclassified"


def test_unknown_future_form_stays_unclassified() -> None:
    payload = _payload(document_form="future_form", title="정기 회의록")

    resolution = resolve_payload_document_type(payload)

    assert resolution.document_type is None
    assert resolution.source == "unclassified"
    assert resolution.reason == "unknown document_form:future_form"


def test_non_list_blocks_do_not_break_conservative_fallback() -> None:
    payload = _payload(document_form="bid_material", title="입찰 평가 자료")
    payload["result"]["generated_document"]["blocks"] = {"unexpected": True}

    resolution = resolve_payload_document_type(payload)

    assert resolution.document_type == "official_document"


def test_inferred_type_is_injected_without_mutating_source_payload() -> None:
    payload = _payload(document_form="meeting_minutes", title="정기 회의록")
    resolution = resolve_payload_document_type(payload)

    projected = apply_payload_document_type(payload, resolution)

    assert projected is not payload
    assert projected["result"]["source_classification"] == {
        "document_type": "meeting_minutes"
    }
    assert payload["result"]["source_classification"] is None
