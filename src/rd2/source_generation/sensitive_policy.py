"""원문 기반 S/O 생성·채점이 공유하는 관계 중심 민감정보 정책."""

from __future__ import annotations

import re

from rd2.source_generation.contracts import (
    IdentificationStrength,
    SensitiveAssertion,
    SensitiveAttributeKind,
    SensitiveConsistencyAssessment,
    SensitiveSubjectRole,
    SensitiveVerdict,
)

SENSITIVE_POLICY_VERSION = "source-sensitive-policy-2026-07-29-v1"

_EMPLOYEE_O_ATTRIBUTES = frozenset(
    {
        SensitiveAttributeKind.BUSINESS_IDENTITY,
        SensitiveAttributeKind.BUSINESS_CONTACT,
        SensitiveAttributeKind.PERSONNEL_EVALUATION,
        SensitiveAttributeKind.DISCIPLINE,
    }
)
_ALWAYS_O_ATTRIBUTES = frozenset(
    {
        SensitiveAttributeKind.BUSINESS_IDENTITY,
        SensitiveAttributeKind.BUSINESS_CONTACT,
    }
)
_GENERIC_SUBJECTS = frozenset(
    {
        "개인",
        "민원인",
        "신청인",
        "신고자",
        "지원자",
        "수급자",
        "직원",
        "담당자",
        "조사대상자",
    }
)
_GENERIC_VALUE_PHRASES = frozenset(
    {
        "개인정보",
        "개인정보 항목",
        "성명",
        "이름",
        "연락처",
        "전화번호",
        "이메일",
        "이메일 주소",
        "주소",
        "경력사항",
        "주민등록번호",
        "계좌번호",
        "건강정보",
        "장애정보",
        "지원자의 전화번호 및 이메일 주소",
    }
)
_MASK_PATTERN = re.compile(r"(?:\*|○|●|□|X|x){2,}")
_PHONE_PATTERN = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_PATTERN = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
_KOREAN_ID_PATTERN = re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")
_PASSPORT_PATTERN = re.compile(r"(?i)\b[A-Z]{1,2}\d{6,8}\b")
_DRIVER_LICENSE_PATTERN = re.compile(
    r"(?<!\d)\d{2}[- ]?\d{2}[- ]?\d{6}[- ]?\d{2}(?!\d)"
)
_ACCOUNT_PATTERN = re.compile(r"(?<!\d)(?:\d[- ]?){8,20}(?!\d)")
_MONEY_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:원|만원|천원)")
_ADDRESS_PATTERN = re.compile(
    r"(?:시|도|군|구|읍|면|동|로|길)\s*\d|(?:아파트|빌딩|번지)"
)


def render_sensitive_policy_guidance() -> str:
    return """\
[원문 기반 S/O 관계 판정 정책]
- 개인정보 항목명, 수집 목적, 처리 절차만 설명하면 O다.
- 사람 이름만 있으면 O다.
- 신청인·민원인·지원자 등 외부인의 구체적인 가상 개인정보 또는 개인 사정이
  식별 가능한 주체와 같은 문장·key-value 항목·표 행에서 연결되면 S다.
- 직원의 이름·부서·직위·업무 연락처, 인사평가, 징계내역은 이 연구 정책에서 O다.
- 직원의 주민·외국인등록번호, 여권·운전면허번호, 개인 연락처·주소, 계좌·급여,
  건강·장애정보는 특정 직원과 연결되면 S다.
- 개인별 행이나 식별자가 없는 집계·통계는 O다.
- 마스킹된 값 또는 신청번호·사건번호만으로 이어지는 간접 식별은 hard_case다.
- 신청번호에 실명·전화번호 같은 직접 식별정보까지 연결된 경우만 명확한 S다.
- S 판정은 subject_span, value_span, link_span을 제출한다. 세 span은 같은 block을
  사용하고 link_span 인용문 안에 subject와 value 인용문이 모두 들어 있어야 한다.
- `전화번호`, `이메일 주소` 같은 필드 이름은 value가 아니다. 실제 형식의 가상 값
  또는 구체적인 건강·복지·신청 사정이 value_span에 글자 그대로 있어야 한다.
"""


def _has_concrete_subject(assertion: SensitiveAssertion) -> bool:
    quote = assertion.subject_span.quote.strip()
    if quote in _GENERIC_SUBJECTS:
        return False
    if _MASK_PATTERN.search(quote):
        return assertion.identification_strength == IdentificationStrength.MASKED
    return len(quote) >= 2


def _is_generic_value(quote: str) -> bool:
    normalized = " ".join(quote.split()).strip(" :·-")
    if normalized in _GENERIC_VALUE_PHRASES:
        return True
    return normalized.endswith("등 개인정보") or normalized.endswith("관련 개인정보")


def _has_concrete_value(assertion: SensitiveAssertion) -> bool:
    quote = assertion.value_span.quote.strip()
    if _is_generic_value(quote):
        return False
    kind = assertion.attribute_kind
    if kind == SensitiveAttributeKind.PHONE:
        return _PHONE_PATTERN.search(quote) is not None
    if kind == SensitiveAttributeKind.EMAIL:
        return _EMAIL_PATTERN.search(quote) is not None
    if kind in {
        SensitiveAttributeKind.NATIONAL_ID,
        SensitiveAttributeKind.FOREIGNER_ID,
    }:
        return _KOREAN_ID_PATTERN.search(quote) is not None
    if kind == SensitiveAttributeKind.PASSPORT_ID:
        return _PASSPORT_PATTERN.search(quote) is not None
    if kind == SensitiveAttributeKind.DRIVER_LICENSE_ID:
        return _DRIVER_LICENSE_PATTERN.search(quote) is not None
    if kind == SensitiveAttributeKind.ACCOUNT:
        return _ACCOUNT_PATTERN.search(quote) is not None
    if kind == SensitiveAttributeKind.SALARY:
        return _MONEY_PATTERN.search(quote) is not None
    if kind == SensitiveAttributeKind.ADDRESS:
        return len(quote) >= 6 and _ADDRESS_PATTERN.search(quote) is not None
    return len(quote) >= 4


def validate_sensitive_assertion(assertion: SensitiveAssertion) -> None:
    if not _has_concrete_subject(assertion):
        raise ValueError("sensitive assertion subject is only a generic role label")
    if assertion.attribute_kind in _ALWAYS_O_ATTRIBUTES:
        raise ValueError(
            f"{assertion.attribute_kind.value} is O under the source-sensitive policy"
        )
    if (
        assertion.subject_role == SensitiveSubjectRole.EMPLOYEE
        and assertion.attribute_kind in _EMPLOYEE_O_ATTRIBUTES
    ):
        raise ValueError(
            f"employee {assertion.attribute_kind.value} is O under the research policy"
        )
    if assertion.identification_strength == IdentificationStrength.MASKED:
        if not (
            _MASK_PATTERN.search(assertion.subject_span.quote)
            or _MASK_PATTERN.search(assertion.value_span.quote)
        ):
            raise ValueError("masked assertion must contain a visible mask")
        return
    if _MASK_PATTERN.search(assertion.value_span.quote):
        raise ValueError("unmasked assertion cannot use a masked value")
    if not _has_concrete_value(assertion):
        raise ValueError(
            f"{assertion.attribute_kind.value} assertion lacks a concrete value"
        )


def validate_sensitive_assessment(
    assessment: SensitiveConsistencyAssessment,
) -> None:
    for assertion in assessment.assertions:
        validate_sensitive_assertion(assertion)
    if assessment.sensitivity_verdict == SensitiveVerdict.ACCEPTED_S:
        if not any(
            assertion.identification_strength == IdentificationStrength.DIRECT
            for assertion in assessment.assertions
        ):
            raise ValueError("accepted_s requires a directly identifying assertion")
