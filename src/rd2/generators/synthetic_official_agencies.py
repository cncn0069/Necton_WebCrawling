"""문서 렌더링에 재사용하는 가상 공공기관 아이덴티티 풀.

실제 지자체명이나 기관 로고를 학습 데이터에 반복 노출하지 않도록, 가상
어간 25개와 기관 계열 12개를 조합해 정확히 300개의 기관을 만든다. 이
모듈은 HTML 템플릿과 독립적이므로 다른 합성 문서 렌더러에서도 사용할 수
있다.
"""

from __future__ import annotations

from dataclasses import dataclass

SYNTHETIC_AGENCY_POOL_SIZE = 300


@dataclass(frozen=True)
class SyntheticAgency:
    """기관명과 문서 발신 표기에 필요한 최소 메타데이터."""

    pool_index: int
    stem: str
    organization_category: str
    organization_type: str
    issuer_role: str
    romanized_name: str
    slogan: str
    brand_note: str
    copy_recipients: str

    @property
    def agency_name(self) -> str:
        return f"{self.stem}{self.organization_type}"

    @property
    def issuer_title(self) -> str:
        return f"{self.agency_name}{self.issuer_role}"


@dataclass(frozen=True)
class _AgencyVariant:
    organization_type: str
    issuer_role: str
    romanized_suffix: str


@dataclass(frozen=True)
class _AgencyFamily:
    category: str
    variants: tuple[_AgencyVariant, ...]
    slogan_template: str
    brand_note: str
    copy_recipients: str


_SYNTHETIC_STEMS: tuple[tuple[str, str], ...] = (
    ("가온", "gaon"),
    ("새롬", "saerom"),
    ("다온", "daon"),
    ("해솔", "haesol"),
    ("누리", "nuri"),
    ("마루", "maru"),
    ("이음", "ieum"),
    ("아람", "aram"),
    ("라온", "raon"),
    ("한결", "hangyeol"),
    ("온빛", "onbit"),
    ("나래", "narae"),
    ("다솜", "dasom"),
    ("새결", "saegyeol"),
    ("모아", "moa"),
    ("윤슬", "yoonseul"),
    ("미리내", "mirinae"),
    ("푸른샘", "pureunsaem"),
    ("솔가람", "solgaram"),
    ("별하", "byeolha"),
    ("도담", "dodam"),
    ("해든", "haedeun"),
    ("빛가람", "bitgaram"),
    ("한울", "hanul"),
    ("여울", "yeoul"),
)


def _variants(
    *items: tuple[str, str, str],
) -> tuple[_AgencyVariant, ...]:
    return tuple(_AgencyVariant(*item) for item in items)


_AGENCY_FAMILIES: tuple[_AgencyFamily, ...] = (
    _AgencyFamily(
        category="local_government",
        variants=_variants(
            ("시", "장", "city"),
            ("군", "수", "county"),
            ("구", "청장", "district"),
        ),
        slogan_template="주민과 함께 여는 {stem}",
        brand_note="주민 중심 행정",
        copy_recipients="각 부서장, 관계기관장",
    ),
    _AgencyFamily(
        category="police",
        variants=_variants(
            ("경찰청", "장", "police-agency"),
            ("경찰서", "장", "police-station"),
            ("해양경찰서", "장", "coast-guard-station"),
        ),
        slogan_template="안전한 일상을 지키는 {stem}",
        brand_note="현장 중심 치안",
        copy_recipients="각 과장, 소속 경찰관서장",
    ),
    _AgencyFamily(
        category="fire_and_rescue",
        variants=_variants(
            ("소방본부", "장", "fire-headquarters"),
            ("소방서", "장", "fire-station"),
            ("119안전센터", "장", "safety-center"),
        ),
        slogan_template="신속한 대응, 안전한 {stem}",
        brand_note="재난 대응 우선",
        copy_recipients="각 과장, 관할 소방관서장",
    ),
    _AgencyFamily(
        category="education",
        variants=_variants(
            ("교육지원청", "교육장", "education-office"),
            ("교육연수원", "장", "education-institute"),
            ("평생학습관", "장", "learning-center"),
        ),
        slogan_template="배움과 성장을 잇는 {stem}",
        brand_note="배움 중심 지원",
        copy_recipients="각 과장, 관할 교육기관장",
    ),
    _AgencyFamily(
        category="public_corporation",
        variants=_variants(
            ("시설관리공단", "이사장", "facilities-corporation"),
            ("환경공단", "이사장", "environment-corporation"),
            ("교통공사", "사장", "transit-corporation"),
        ),
        slogan_template="생활 가까이, 더 나은 {stem}",
        brand_note="신뢰받는 공공서비스",
        copy_recipients="각 부서장, 산하기관장",
    ),
    _AgencyFamily(
        category="regional_development",
        variants=_variants(
            ("도시개발공사", "사장", "urban-development"),
            ("산업진흥원", "장", "industry-promotion"),
            ("관광재단", "대표이사", "tourism-foundation"),
        ),
        slogan_template="지역의 가능성을 키우는 {stem}",
        brand_note="지속 가능한 성장",
        copy_recipients="각 부서장, 협력기관장",
    ),
    _AgencyFamily(
        category="research",
        variants=_variants(
            ("정책연구원", "장", "policy-institute"),
            ("보건환경연구원", "장", "health-environment-institute"),
            ("재난안전연구원", "장", "disaster-safety-institute"),
        ),
        slogan_template="정책과 현장을 잇는 {stem}",
        brand_note="근거 중심 연구",
        copy_recipients="각 연구부서장, 관계기관장",
    ),
    _AgencyFamily(
        category="health_and_welfare",
        variants=_variants(
            ("보건소", "장", "health-center"),
            ("의료원", "장", "medical-center"),
            ("복지재단", "대표이사", "welfare-foundation"),
        ),
        slogan_template="건강한 일상을 함께하는 {stem}",
        brand_note="건강과 돌봄 우선",
        copy_recipients="각 부서장, 보건복지 관계기관장",
    ),
    _AgencyFamily(
        category="field_service",
        variants=_variants(
            ("농업기술센터", "소장", "agriculture-center"),
            ("수도사업소", "장", "water-service-office"),
            ("차량등록사업소", "장", "vehicle-registration-office"),
        ),
        slogan_template="현장 가까이에서 함께하는 {stem}",
        brand_note="현장 중심 행정",
        copy_recipients="각 팀장, 관계 사업소장",
    ),
    _AgencyFamily(
        category="culture_and_youth",
        variants=_variants(
            ("문화재단", "대표이사", "culture-foundation"),
            ("청소년재단", "대표이사", "youth-foundation"),
            ("도서관", "장", "public-library"),
        ),
        slogan_template="문화와 사람을 잇는 {stem}",
        brand_note="열린 문화 서비스",
        copy_recipients="각 부서장, 문화교육 관계기관장",
    ),
    _AgencyFamily(
        category="public_committee",
        variants=_variants(
            ("안전관리위원회", "위원장", "safety-committee"),
            ("도시계획위원회", "위원장", "planning-committee"),
            ("정보공개위원회", "위원장", "information-committee"),
        ),
        slogan_template="공정한 논의, 투명한 {stem}",
        brand_note="투명한 의사결정",
        copy_recipients="각 위원, 관계 부서장",
    ),
    _AgencyFamily(
        # 기관 정보가 없는 생성 문서의 안전한 fallback이다. 특정 직역을
        # 암시하는 경찰서·소방서·농업기술센터가 본문과 무작위로 결합되지
        # 않도록 범용 명칭만 둔다.
        category="generic_public",
        variants=_variants(
            ("공공행정원", "장", "public-administration-institute"),
            ("공공정책지원원", "장", "public-policy-support-institute"),
            ("공공서비스원", "장", "public-service-institute"),
        ),
        slogan_template="신뢰받는 공공서비스, {stem}",
        brand_note="공공서비스 지원",
        copy_recipients="각 부서장, 관계기관장",
    ),
)


def _build_agency_pool() -> tuple[SyntheticAgency, ...]:
    agencies: list[SyntheticAgency] = []
    for stem_index, (stem, romanized_stem) in enumerate(_SYNTHETIC_STEMS):
        for family_index, family in enumerate(_AGENCY_FAMILIES):
            variant = family.variants[
                (stem_index + family_index) % len(family.variants)
            ]
            agencies.append(
                SyntheticAgency(
                    pool_index=len(agencies),
                    stem=stem,
                    organization_category=family.category,
                    organization_type=variant.organization_type,
                    issuer_role=variant.issuer_role,
                    romanized_name=(
                        f"{romanized_stem}-{variant.romanized_suffix}"
                    ),
                    slogan=family.slogan_template.format(stem=stem),
                    brand_note=family.brand_note,
                    copy_recipients=family.copy_recipients,
                )
            )

    names = {agency.agency_name for agency in agencies}
    if len(agencies) != SYNTHETIC_AGENCY_POOL_SIZE:
        raise RuntimeError(
            "Synthetic agency pool size drifted: "
            f"expected {SYNTHETIC_AGENCY_POOL_SIZE}, got {len(agencies)}"
        )
    if len(names) != len(agencies):
        raise RuntimeError("Synthetic agency pool contains duplicate names")
    return tuple(agencies)


SYNTHETIC_AGENCIES = _build_agency_pool()
