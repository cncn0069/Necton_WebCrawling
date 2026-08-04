"""군사기밀 등급 어휘.

``agency_resolver``에서 떼어냈다. 그 모듈은 기관명을 로고 파일로 해석하고 시나리오
태깅을 읽는 **행위**인데, 등급 표기는 여러 층이 함께 보는 상수다 — 렌더러가 마크
이미지를 고를 때, C트랙 프롬프트가 등급 지시를 쓸 때 같은 값을 봐야 한다.
그 상수를 행위 모듈에 두었더니 ``source_generation.c_track_templates``가 프롬프트
한 줄을 쓰려고 ``generators``를 import했다.
"""

from __future__ import annotations

import random

# 군사기밀보호법 시행령 [별표 2](제5조제1항)의 등급 표시 방식을 적용할 기관.
# 화이트리스트의 나머지 기관(외교부·통일부·검찰청·고위공직자범죄수사처·법무부 등)은
# 실제로는 각자 다른 근거 법령(예: 국가정보원법)을 따르지만, 이 프로젝트는 "군사기밀
# 스타일 등급 마크가 붙는 기관"인지 여부만 국방부·국가정보원 둘로 단순화한다
# (2026-07-21 사용자 결정) — 그 외는 기존 "대외비" 마크를 그대로 쓴다.
MILITARY_SECRET_AGENCIES = frozenset({"국방부", "국가정보원"})

# [별표 2] 1호 "가": 붉은색(Ⅰ급/TOP SECRET) > 노란색(Ⅱ급/SECRET) > 파란색(Ⅲ급/CONFIDENTIAL).
MILITARY_SECRET_GRADES: tuple[str, ...] = ("1급", "2급", "3급")

MILITARY_SECRET_GRADE_LABELS: dict[str, str] = {
    "1급": "Ⅰ급비밀(TOP SECRET)",
    "2급": "Ⅱ급비밀(SECRET)",
    "3급": "Ⅲ급비밀(CONFIDENTIAL)",
}

# logo/ 폴더에 사용자가 준비해둔 등급별 마크 원본(2026-07-21 추가) — [별표 2]와
# 동일한 "군사 / X급비밀 / TOP SECRET 등" 박스 도안.
MILITARY_SECRET_MARK_FILENAMES: dict[str, str] = {
    "1급": "1급_비밀.png",
    "2급": "2급_비밀.png",
    "3급": "3급_비밀.png",
}


def is_military_secret_agency(agency: str) -> bool:
    """국방부/국가정보원 문서만 [별표 2] 스타일 등급 마크 대상으로 본다.

    나머지 화이트리스트 기관은 기존 "대외비" 마크를 유지한다(2026-07-21 사용자 결정).
    """
    return agency in MILITARY_SECRET_AGENCIES


def select_military_secret_grade(rng: random.Random) -> str:
    """Ⅰ/Ⅱ/Ⅲ급 중 하나를 랜덤 배정한다 — 등급별 고정 매핑 없음(2026-07-21 사용자 결정).

    반환값은 본문 생성 프롬프트(그 등급에 맞는 심각성으로 작성)와 PDF 렌더링(등급별
    마크 이미지 선택) 양쪽에 동일하게 써야 마크와 내용이 어긋나지 않는다.
    """
    return rng.choice(MILITARY_SECRET_GRADES)
