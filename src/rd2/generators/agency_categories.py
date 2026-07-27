"""기관유형 카테고리 매핑 (레이아웃 다양성 확보용, 코드 품질 리뷰 관례 — 데이터로 관리).

RD-1이 레이아웃을 판별 신호로 쓸 수 있다는 발주처 지적(2026-07-14)에 따라, C/S
합성 문서도 O트랙 수준의 레이아웃 다양성을 확보해야 한다. 특정 실제 기관의
직인·레터헤드를 복제하는 게 아니라(법무 리스크), 기관이 속한 "유형"이 실제로
쓰는 표준 양식만 흉내낸다 — 설계 문서:
~/.gstack/projects/cncn0069-Necton_WebCrawling/
안정현-feat-open-go-kr-alternative-sources-design-20260714-144123.md

규칙은 순서가 있다 — 첫 번째로 매치되는 카테고리를 쓴다. "OO교육청"이 일반
"청" 접미사 규칙(중앙부처)보다 먼저 잡히지 않으면 전부 중앙부처로 잘못
분류된다(플랜 리뷰에서 발견된 실제 버그).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

CATEGORY_CENTRAL_MINISTRY = "central_ministry"
CATEGORY_METRO_LOCAL_GOVERNMENT = "metro_local_government"
CATEGORY_RESEARCH_INSTITUTE = "research_institute"
CATEGORY_EDUCATION_OFFICE = "education_office"
CATEGORY_PUBLIC_CORPORATION = "public_corporation"  # catch-all/기본값

DEFAULT_CATEGORY = CATEGORY_PUBLIC_CORPORATION

# Coverage planner(설계 문서 design-coverage-matrix-diversity-audit-20260723.md)가
# 기관군 축을 순회할 때 쓰는 정렬 가능한 목록.
AGENCY_CATEGORIES: tuple[str, ...] = (
    CATEGORY_CENTRAL_MINISTRY,
    CATEGORY_EDUCATION_OFFICE,
    CATEGORY_METRO_LOCAL_GOVERNMENT,
    CATEGORY_PUBLIC_CORPORATION,
    CATEGORY_RESEARCH_INSTITUTE,
)


@dataclass(frozen=True)
class AgencyCategoryRule:
    category: str
    matches: Callable[[str], bool]


def _ends_with_any(name: str, suffixes: tuple[str, ...]) -> bool:
    return any(name.endswith(suf) for suf in suffixes)


def _contains_any(name: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in name for kw in keywords)


# 순서 중요 — 위에서부터 첫 매치를 쓴다.
AGENCY_CATEGORY_RULES: list[AgencyCategoryRule] = [
    # "교육청"은 "청" 접미사라 중앙부처 규칙보다 먼저 걸러야 한다.
    AgencyCategoryRule(CATEGORY_EDUCATION_OFFICE, lambda name: "교육청" in name),
    AgencyCategoryRule(
        CATEGORY_RESEARCH_INSTITUTE,
        lambda name: _contains_any(name, ("연구원", "연구소", "연구기관")),
    ),
    AgencyCategoryRule(
        CATEGORY_CENTRAL_MINISTRY,
        lambda name: _ends_with_any(name, ("부", "청", "처")) or "위원회" in name,
    ),
    AgencyCategoryRule(
        CATEGORY_METRO_LOCAL_GOVERNMENT,
        lambda name: _ends_with_any(
            name, ("특별시", "광역시", "특별자치시", "특별자치도", "도", "시", "군", "구")
        ),
    ),
]


def get_agency_category(ordering_agency: str | None) -> str:
    """실제 기관명(또는 폴백의 "가상기관(합성)")을 5개 유형 중 하나로 매핑한다.

    매치되는 규칙이 없으면(예: synthetic_fallback 행의 "가상기관(합성)")
    공공기관 catch-all로 보낸다.
    """
    if not ordering_agency:
        return DEFAULT_CATEGORY
    for rule in AGENCY_CATEGORY_RULES:
        if rule.matches(ordering_agency):
            return rule.category
    return DEFAULT_CATEGORY
