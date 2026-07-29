"""행정상태 target이 P1 생성문에 자연스럽게 실현됐는지 검증한다."""

from __future__ import annotations

from rd2.administrative_status import (
    ADMIN_STATUS_TEXT_POLICIES,
    AdminStatus,
)


def administrative_status_generation_requirements(
    statuses: tuple[AdminStatus, ...],
) -> tuple[dict[str, str], ...]:
    """P1 generation plan에 넣을 상태별 의미 생성 요구사항."""

    return tuple(
        {
            "status": status.value,
            "semantic_condition": ADMIN_STATUS_TEXT_POLICIES[status].detail,
            "writing_instruction": (
                f"상태명 '{status.value}'이나 고정 문구를 그대로 쓰지 않아도 된다. "
                f"{ADMIN_STATUS_TEXT_POLICIES[status].detail}임이 문서의 상황과 "
                "문맥에서 자연스럽게 드러나도록 작성한다."
            ),
        }
        for status in statuses
    )
