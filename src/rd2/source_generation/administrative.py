"""행정상태 target이 생성문에 자연스럽게 실현됐는지 검증한다."""

from __future__ import annotations

import re
from datetime import date

from rd2.administrative_status import (
    ADMIN_STATUS_TEXT_POLICIES,
    AdminStatus,
)

#: 문서가 "아직 오지 않았다"고 주장하려면 기준일보다 나중이어야 하는 상태.
#:
#: 실측에서 2026년에 생성한 문서가 "공개 예정일: 2024년 10월 1일"을 달고
#: 나왔다. 이미 지난 날짜라 '미도래'가 거짓인데 아무도 못 잡았다 —
#: ``forbidden_phrases``는 고정 문구만 보고 날짜는 안 본다.
FUTURE_DATE_REQUIRED_STATUSES: frozenset[AdminStatus] = frozenset(
    {AdminStatus.RELEASE_NOT_DUE}
)

_DATE_PATTERNS = (
    re.compile(r"(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})"),
    re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
)


class AdministrativeStatusError(ValueError):
    """생성문이 지정된 행정상태와 모순될 때 발생한다."""


def administrative_status_generation_requirements(
    statuses: tuple[AdminStatus, ...],
    *,
    reference_date: date | None = None,
) -> tuple[dict[str, str], ...]:
    """generation plan에 넣을 상태별 의미 생성 요구사항.

    ``reference_date``는 "오늘"에 해당하는 기준일이다. 이걸 안 주면 모델은
    미래·과거를 판단할 근거가 없어서 아무 날짜나 쓴다 — 실제로 '미도래'
    문서에 지난 날짜가 들어간 사례가 나왔다.
    """

    requirements: list[dict[str, str]] = []
    for status in statuses:
        policy = ADMIN_STATUS_TEXT_POLICIES[status]
        instruction = (
            f"상태명 '{status.value}'이나 고정 문구를 그대로 쓰지 않아도 된다. "
            f"{policy.detail}임이 문서의 상황과 문맥에서 자연스럽게 드러나도록 "
            "작성한다."
        )
        requirement = {
            "status": status.value,
            "semantic_condition": policy.detail,
            "writing_instruction": instruction,
        }
        if reference_date is not None:
            requirement["reference_date"] = reference_date.isoformat()
            if status in FUTURE_DATE_REQUIRED_STATUSES:
                requirement["writing_instruction"] = (
                    f"{instruction} 문서에 날짜를 쓸 때는 기준일 "
                    f"{reference_date.isoformat()}을 오늘로 삼고, 아직 오지 "
                    "않았음을 나타내는 날짜는 반드시 기준일보다 나중이어야 한다."
                )
        requirements.append(requirement)
    return tuple(requirements)


def extract_dates(text: str) -> tuple[date, ...]:
    """본문에서 날짜를 추출한다. 파싱 불가능한 값은 조용히 버린다."""

    found: list[date] = []
    for pattern in _DATE_PATTERNS:
        for year, month, day in pattern.findall(text):
            try:
                found.append(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return tuple(found)


def validate_status_date_coherence(
    body_text: str,
    statuses: tuple[AdminStatus, ...],
    *,
    reference_date: date,
) -> None:
    """'아직 오지 않았다'는 상태가 실제로 미래 날짜로 뒷받침되는지 본다.

    문서에 날짜가 하나도 없으면 통과시킨다 — 날짜 없이 문맥만으로 상태를
    드러내는 것도 허용된 표현이기 때문이다. 날짜가 있는데 전부 기준일
    이전이면, 그 문서는 스스로와 모순된다.
    """

    targets = [status for status in statuses if status in FUTURE_DATE_REQUIRED_STATUSES]
    if not targets:
        return
    dates = extract_dates(body_text)
    if not dates:
        return
    if any(item > reference_date for item in dates):
        return
    latest = max(dates)
    raise AdministrativeStatusError(
        f"{'/'.join(status.value for status in targets)} 상태인데 문서의 모든 "
        f"날짜가 기준일 {reference_date.isoformat()} 이전이다"
        f"(가장 늦은 날짜 {latest.isoformat()})"
    )
