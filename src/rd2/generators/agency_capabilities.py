"""기관의 법적 유형과 실제 업무 기능을 분리해 다루는 보수적 적합성 규칙.

ALIO의 공기업/준정부기관/기타공공기관 구분은 수입 구조와 지정 요건에 따른
법적 분류다. 문서 시나리오 적합성(제품 판매, 의료 연구, 방송 등)을 보장하지
않으므로, 상업성이 강한 시나리오는 확인된 기능이 있을 때만 허용한다.

새 기관이나 모르는 기관은 ``generic_public_body``만 부여한다. 따라서 일반
행정·조달·인사 시나리오는 사용할 수 있지만 제품 출시·프랜차이즈처럼 기관의
고유 사업범위를 전제하는 시나리오에는 자동 배정되지 않는다(fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass

CAP_GENERIC_PUBLIC_BODY = "generic_public_body"
CAP_COMMERCIAL_PRODUCT_SALES = "commercial_product_sales"
CAP_CORPORATE_TRANSACTIONS = "corporate_transactions"
CAP_MEDIA_CONTENT = "media_content"
CAP_MEDICAL_CARE = "medical_care"
CAP_RESEARCH_DEVELOPMENT = "research_development"
CAP_REGULATORY_POLICY = "regulatory_policy"
CAP_SOCIAL_WELFARE = "social_welfare"
CAP_LOCAL_RESIDENT_ADMIN = "local_resident_administration"
CAP_TECHNOLOGY_TRANSFER = "technology_transfer"


@dataclass(frozen=True)
class AgencyCapabilityProfile:
    capabilities: frozenset[str]
    source: str
    confidence: str


# 공식 설립목적·주요사업을 확인한 기관만 명시한다. 이 목록은 "금지 목록"이
# 아니라 허용 기능의 근거 목록이다. 등록되지 않은 기관은 보수적 기본값을 쓴다.
_VERIFIED_PROFILES: dict[str, AgencyCapabilityProfile] = {
    "국립암센터": AgencyCapabilityProfile(
        capabilities=frozenset(
            {
                CAP_GENERIC_PUBLIC_BODY,
                CAP_MEDICAL_CARE,
                CAP_RESEARCH_DEVELOPMENT,
                CAP_TECHNOLOGY_TRANSFER,
            }
        ),
        source="국립암센터 공식 기관소개·주요기능",
        confidence="verified",
    ),
    "국악방송": AgencyCapabilityProfile(
        capabilities=frozenset({CAP_GENERIC_PUBLIC_BODY, CAP_MEDIA_CONTENT}),
        source="문화체육관광부 비영리법인현황·주요사업",
        confidence="verified",
    ),
}


# 이름 자체가 법인 형태와 판매사업을 명시하는 경우만 좁게 인정한다. '공사',
# '공단', '진흥원' 같은 접미사는 공공서비스 수행기관에도 광범위하게 쓰이므로
# 제품 판매 역량의 근거로 사용하지 않는다.
_COMMERCIAL_NAME_MARKERS = ("(주)", "주식회사", "공영홈쇼핑", "강원랜드")


def get_agency_capability_profile(agency: str | None) -> AgencyCapabilityProfile:
    """기관 기능 프로필을 반환한다. 미확인 기관은 보수적인 범용 프로필이다."""
    name = (agency or "").strip()
    verified = _VERIFIED_PROFILES.get(name)
    if verified is not None:
        return verified
    if any(marker in name for marker in _COMMERCIAL_NAME_MARKERS):
        return AgencyCapabilityProfile(
            capabilities=frozenset(
                {
                    CAP_GENERIC_PUBLIC_BODY,
                    CAP_COMMERCIAL_PRODUCT_SALES,
                    CAP_CORPORATE_TRANSACTIONS,
                }
            ),
            source="기관명에 명시된 상업 법인/판매사업 표지",
            confidence="inferred",
        )
    if any(marker in name for marker in ("복지", "건강보험", "연금", "양육비")):
        return AgencyCapabilityProfile(
            capabilities=frozenset({CAP_GENERIC_PUBLIC_BODY, CAP_SOCIAL_WELFARE}),
            source="기관명에 명시된 사회보장·복지 기능",
            confidence="inferred",
        )
    if any(marker in name for marker in ("병원", "의료원", "암센터")):
        return AgencyCapabilityProfile(
            capabilities=frozenset({CAP_GENERIC_PUBLIC_BODY, CAP_MEDICAL_CARE}),
            source="기관명에 명시된 의료 기능",
            confidence="inferred",
        )
    if name.endswith(("특별시", "광역시", "특별자치시", "시청", "군청", "구청")):
        return AgencyCapabilityProfile(
            capabilities=frozenset(
                {CAP_GENERIC_PUBLIC_BODY, CAP_LOCAL_RESIDENT_ADMIN}
            ),
            source="지방자치단체 명칭 규칙",
            confidence="inferred",
        )
    if name.endswith(("부", "청", "처")) or "위원회" in name:
        return AgencyCapabilityProfile(
            capabilities=frozenset(
                {CAP_GENERIC_PUBLIC_BODY, CAP_REGULATORY_POLICY}
            ),
            source="정부조직 명칭 규칙",
            confidence="inferred",
        )
    return AgencyCapabilityProfile(
        capabilities=frozenset({CAP_GENERIC_PUBLIC_BODY}),
        source="미확인 기관 보수적 기본값",
        confidence="unknown",
    )


# 제7호 시나리오 중 기관 고유의 상업 활동을 반드시 전제하는 항목만 엄격하게
# 제한한다. 나머지 조달·보안·인사·기술검토 시나리오는 공공기관 일반 업무로
# 볼 수 있어 기존 기관유형 필터를 유지한다.
_REQUIRED_CAPABILITIES: dict[tuple[str, int], frozenset[str]] = {
    ("6", 2): frozenset({CAP_SOCIAL_WELFARE}),  # 사회복지 급여 수급자 심사
    ("6", 3): frozenset({CAP_MEDICAL_CARE}),  # 건강검진 결과
    ("6", 5): frozenset({CAP_LOCAL_RESIDENT_ADMIN}),  # 전출입 신고
    ("7", 0): frozenset({CAP_RESEARCH_DEVELOPMENT}),  # 기술·특허 명세
    ("7", 1): frozenset({CAP_CORPORATE_TRANSACTIONS}),  # M&A 실사·인수 협상
    ("7", 4): frozenset({CAP_COMMERCIAL_PRODUCT_SALES}),  # 신제품 마케팅·가격정책
    ("7", 6): frozenset({CAP_COMMERCIAL_PRODUCT_SALES}),  # 프랜차이즈 매출·로열티
    ("7", 7): frozenset({CAP_CORPORATE_TRANSACTIONS}),  # 투자유치 사업계획
}

# 이 표는 모든 기능이 아니라 하나 이상을 충족하면 된다.
_REQUIRED_ANY_CAPABILITIES: dict[tuple[str, int], frozenset[str]] = {
    ("7", 8): frozenset(
        {CAP_RESEARCH_DEVELOPMENT, CAP_REGULATORY_POLICY}
    ),  # 국가첨단전략기술 지정 신청·판정
}


def required_capabilities_for_scenario(
    clause_no: str, scenario_index: int | None
) -> frozenset[str]:
    if scenario_index is None:
        return frozenset()
    return _REQUIRED_CAPABILITIES.get((clause_no, scenario_index), frozenset())


def is_agency_scenario_compatible(
    agency: str | None, clause_no: str, scenario_index: int | None
) -> bool:
    """필요 기능이 없는 일반 시나리오는 허용하고, 엄격 시나리오는 fail closed."""
    required = required_capabilities_for_scenario(clause_no, scenario_index)
    actual = get_agency_capability_profile(agency).capabilities
    if required and not required.issubset(actual):
        return False
    required_any = _REQUIRED_ANY_CAPABILITIES.get(
        (clause_no, scenario_index), frozenset()
    )
    if required_any and not required_any.intersection(actual):
        return False
    return True
