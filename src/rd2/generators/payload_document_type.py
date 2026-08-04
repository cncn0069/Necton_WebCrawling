"""렌더 payload의 문서 형식을 의미상 문서 유형으로 보완한다.

source-sensitive batch의 ``provenance.document_form``은 생성 문서가 지켜야
할 행정 서식을 나타내지만, 렌더러가 요구하는 ``document_type``보다 범주가
넓다. 이 모듈은 명시적인 document_type이 없는 과거 payload에 한해서 문서
형식과 생성된 제목·첫 문단을 이용해 보수적으로 렌더러 유형을 복원한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from rd2.source_generation.classification_taxonomy import SemanticDocumentType


@dataclass(frozen=True)
class PayloadDocumentTypeResolution:
    """payload에 적용할 문서 유형과 그 판정 근거."""

    document_type: str | None
    source: str
    document_form: str | None
    reason: str

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


_RESEARCH_KEYWORDS = (
    "연구",
    "분석",
    "조사",
    "평가",
    "검토",
    "진단",
    "타당성",
)
_STATUS_KEYWORDS = (
    "현황",
    "실적",
    "결과",
    "경과",
    "운영",
    "추진",
    "점검",
    "감사",
    "계획",
)
_GUIDE_KEYWORDS = (
    "가이드라인",
    "가이드",
    "매뉴얼",
    "지침",
    "길잡이",
    "안내서",
    "사례집",
    "수첩",
    "리플렛",
)


def _non_blank_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _result(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    result = payload.get("result")
    return result if isinstance(result, Mapping) else None


def _explicit_document_type(
    payload: Mapping[str, Any],
) -> tuple[bool, str | None]:
    result = _result(payload)
    if result is None:
        return False, None
    classification = result.get("source_classification")
    if (
        not isinstance(classification, Mapping)
        or "document_type" not in classification
    ):
        return False, None
    value = classification.get("document_type")
    return True, value if isinstance(value, str) else None


def _document_form(
    payload: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    provenance = payload.get("provenance")
    provenance_form = (
        _non_blank_string(provenance.get("document_form"))
        if isinstance(provenance, Mapping)
        else None
    )

    result = _result(payload)
    if result is None:
        return provenance_form, None

    classification = result.get("source_classification")
    classification_form = (
        _non_blank_string(classification.get("document_form"))
        if isinstance(classification, Mapping)
        else None
    )
    result_provenance = result.get("provenance")
    result_provenance_form = (
        _non_blank_string(result_provenance.get("document_form"))
        if isinstance(result_provenance, Mapping)
        else None
    )

    if classification_form is not None:
        conflicting_forms = sorted(
            {
                value
                for value in (provenance_form, result_provenance_form)
                if value is not None and value != classification_form
            }
        )
        conflict_note = (
            "document_form conflict: used result.source_classification "
            f"over {', '.join(conflicting_forms)}"
            if conflicting_forms
            else None
        )
        return classification_form, conflict_note
    if provenance_form is not None:
        conflict_note = (
            "document_form conflict: used top-level provenance "
            f"over {result_provenance_form}"
            if result_provenance_form is not None
            and result_provenance_form != provenance_form
            else None
        )
        return provenance_form, conflict_note
    return result_provenance_form, None


def _generated_document(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    result = _result(payload)
    if result is None:
        return None
    document = result.get("generated_document")
    return document if isinstance(document, Mapping) else None


def _title_and_lead(payload: Mapping[str, Any]) -> tuple[str, str]:
    document = _generated_document(payload)
    if document is None:
        return "", ""

    title = _non_blank_string(document.get("title")) or ""
    blocks = document.get("blocks")
    if not isinstance(blocks, list):
        return title, ""

    paragraphs: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("kind") != "paragraph":
            continue
        text = _non_blank_string(block.get("text"))
        if text is not None:
            paragraphs.append(text)
        if len(paragraphs) == 2:
            break
    return title, "\n".join(paragraphs)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _bid_document_type(title: str, lead: str) -> tuple[str, str]:
    if "사전규격" in title:
        return SemanticDocumentType.PRE_SPEC_NOTICE.value, "title:사전규격"
    if "재공고" in title:
        return SemanticDocumentType.BID_RENOTICE.value, "title:재공고"
    if "공모" in title:
        return SemanticDocumentType.PUBLIC_OFFERING.value, "title:공모"
    if "입찰공고" in title or "공고문" in title:
        return SemanticDocumentType.BID_NOTICE.value, "title:입찰공고"
    if "입찰공고 자료" in lead:
        return SemanticDocumentType.BID_NOTICE.value, "lead:입찰공고 자료"
    return (
        SemanticDocumentType.OFFICIAL_DOCUMENT.value,
        "bid subtype is not explicit in generated content",
    )


def _administrative_rule_type(title: str) -> tuple[str, str]:
    if "고시" in title:
        return SemanticDocumentType.NOTIFICATION.value, "title:고시"
    if "훈령" in title:
        return SemanticDocumentType.DIRECTIVE.value, "title:훈령"
    if "예규" in title:
        return SemanticDocumentType.REGULATION.value, "title:예규"
    return (
        SemanticDocumentType.OFFICIAL_DOCUMENT.value,
        "administrative-rule subtype is not explicit in title",
    )


def _report_type(title: str) -> tuple[str, str]:
    if _contains_any(title, _RESEARCH_KEYWORDS):
        return SemanticDocumentType.RESEARCH_REPORT.value, "research keyword"
    if _contains_any(title, _STATUS_KEYWORDS):
        return SemanticDocumentType.STATUS_REPORT.value, "status keyword"
    return SemanticDocumentType.STATUS_REPORT.value, "generic report"


def _policy_type(title: str) -> tuple[str, str]:
    if "질의회시" in title or "질의·회시" in title:
        return (
            SemanticDocumentType.INTERPRETATION_COMPILATION.value,
            "title:질의회시",
        )
    if _contains_any(title, _GUIDE_KEYWORDS):
        return SemanticDocumentType.GUIDE.value, "guide keyword"
    return SemanticDocumentType.POLICY_MATERIAL.value, "generic policy material"


def _other_type(title: str, lead: str) -> tuple[str, str]:
    combined = f"{title}\n{lead}"
    if "회의록" in title:
        return SemanticDocumentType.MEETING_MINUTES.value, "title:회의록"
    if "보도자료" in title:
        return SemanticDocumentType.PRESS_RELEASE.value, "title:보도자료"
    administrative_type, administrative_reason = _administrative_rule_type(title)
    if administrative_type != SemanticDocumentType.OFFICIAL_DOCUMENT.value:
        return administrative_type, administrative_reason
    policy_type, policy_reason = _policy_type(title)
    if policy_type != SemanticDocumentType.POLICY_MATERIAL.value:
        return policy_type, policy_reason
    bid_type, bid_reason = _bid_document_type(title, lead)
    if bid_type != SemanticDocumentType.OFFICIAL_DOCUMENT.value:
        return bid_type, bid_reason
    if "보고서" in title and _contains_any(combined, _RESEARCH_KEYWORDS):
        return SemanticDocumentType.RESEARCH_REPORT.value, "report research keyword"
    if _contains_any(title, _STATUS_KEYWORDS):
        return SemanticDocumentType.STATUS_REPORT.value, "status keyword"
    if "검토안" in title or "검토자료" in title:
        return SemanticDocumentType.RESEARCH_REPORT.value, "title:검토"
    return SemanticDocumentType.OFFICIAL_DOCUMENT.value, "ambiguous other form"


def resolve_payload_document_type(
    payload: Mapping[str, Any],
) -> PayloadDocumentTypeResolution:
    """명시값을 보존하고, 없을 때만 provenance와 생성 본문으로 보완한다."""

    has_explicit, explicit = _explicit_document_type(payload)
    document_form, form_conflict = _document_form(payload)
    if has_explicit:
        explicit_reason = "result.source_classification.document_type"
        if form_conflict is not None:
            explicit_reason = f"{explicit_reason}; {form_conflict}"
        return PayloadDocumentTypeResolution(
            document_type=explicit,
            source="explicit",
            document_form=document_form,
            reason=explicit_reason,
        )
    if document_form is None:
        return PayloadDocumentTypeResolution(
            document_type=None,
            source="unclassified",
            document_form=None,
            reason="document_form and explicit document_type are missing",
        )

    title, lead = _title_and_lead(payload)
    if document_form == "meeting_minutes":
        document_type, reason = (
            SemanticDocumentType.MEETING_MINUTES.value,
            "document_form:meeting_minutes",
        )
    elif document_form == "press_release":
        document_type, reason = (
            SemanticDocumentType.PRESS_RELEASE.value,
            "document_form:press_release",
        )
    elif document_form == "official_letter":
        document_type, reason = (
            SemanticDocumentType.OFFICIAL_DOCUMENT.value,
            "document_form:official_letter",
        )
    elif document_form == "report":
        document_type, reason = _report_type(title)
    elif document_form in {"inspection_report", "audit_material"}:
        document_type, reason = (
            SemanticDocumentType.STATUS_REPORT.value,
            f"document_form:{document_form}",
        )
    elif document_form in {"investigation_report", "legal_review"}:
        document_type, reason = (
            SemanticDocumentType.RESEARCH_REPORT.value,
            f"document_form:{document_form}",
        )
    elif document_form == "bid_material":
        document_type, reason = _bid_document_type(title, lead)
    elif document_form == "policy_material":
        document_type, reason = _policy_type(title)
    elif document_form == "administrative_rule":
        document_type, reason = _administrative_rule_type(title)
    elif document_form == "plan_draft":
        document_type, reason = (
            SemanticDocumentType.PLAN.value,
            "document_form:plan_draft",
        )
    elif document_form == "approval_request":
        document_type, reason = (
            SemanticDocumentType.APPROVAL.value,
            "document_form:approval_request",
        )
    elif document_form == "reply_notice":
        document_type, reason = (
            SemanticDocumentType.REPLY_NOTIFICATION.value,
            "document_form:reply_notice",
        )
    elif document_form == "personnel_material":
        document_type, reason = (
            SemanticDocumentType.PERSONNEL.value,
            "document_form:personnel_material",
        )
    elif document_form == "response_plan":
        document_type, reason = (
            SemanticDocumentType.PLAN.value,
            "document_form:response_plan",
        )
    elif document_form == "other":
        document_type, reason = _other_type(title, lead)
    else:
        return PayloadDocumentTypeResolution(
            document_type=None,
            source="unclassified",
            document_form=document_form,
            reason=f"unknown document_form:{document_form}",
        )

    if form_conflict is not None:
        reason = f"{reason}; {form_conflict}"
    return PayloadDocumentTypeResolution(
        document_type=document_type,
        source="inferred",
        document_form=document_form,
        reason=reason,
    )


def apply_payload_document_type(
    payload: dict[str, Any],
    resolution: PayloadDocumentTypeResolution,
) -> dict[str, Any]:
    """추론값을 렌더 입력 복사본에 넣고 원본 payload는 변경하지 않는다."""

    if resolution.source != "inferred" or resolution.document_type is None:
        return payload
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    classification = result.get("source_classification")
    projected_classification = (
        dict(classification) if isinstance(classification, Mapping) else {}
    )
    projected_classification["document_type"] = resolution.document_type
    return {
        **payload,
        "result": {
            **result,
            "source_classification": projected_classification,
        },
    }
