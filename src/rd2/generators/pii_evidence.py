"""제6호 합성 문서에 실제 판정 근거가 되는 합성 PII 값을 보장한다."""

from __future__ import annotations

import hashlib
import re

_RRN_RE = re.compile(r"\b\d{6}-[1-4]\d{6}\b")
_PHONE_RE = re.compile(r"\b01[016789]-\d{3,4}-\d{4}\b")
_BIRTH_RE = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
_ACCOUNT_RE = re.compile(r"\b\d{3}-\d{2,4}-\d{4,6}\b")

_NAMES = ("김하람", "이도윤", "박서현", "최가온", "정다온", "윤해솔")


def _seed_number(row_id: str, offset: int = 0) -> int:
    digest = hashlib.sha256(f"{row_id}:{offset}".encode()).hexdigest()
    return int(digest[:8], 16)


def _synthetic_rrn(row_id: str, offset: int) -> str:
    value = _seed_number(row_id, offset)
    year = 70 + value % 25
    month = 1 + (value // 7) % 12
    day = 1 + (value // 13) % 28
    gender = 1 + value % 2
    # 마지막 자리는 실제 체크섬을 계산하지 않아 실존 식별번호가 되지 않게 한다.
    suffix = (value // 17) % 1_000_000
    return f"{year:02d}{month:02d}{day:02d}-{gender}{suffix:06d}"


def _records(row_id: str) -> list[tuple[str, str]]:
    base = _seed_number(row_id)
    return [
        (_NAMES[(base + i) % len(_NAMES)], _synthetic_rrn(row_id, i))
        for i in range(3)
    ]


def build_synthetic_pii_evidence(scenario_index: int | None, row_id: str) -> str:
    """시나리오별로 실제 값이 들어간 최소 증거 블록을 결정적으로 만든다."""
    idx = 0 if scenario_index is None else scenario_index
    people = _records(row_id)
    if idx == 2:
        lines = ["붙임 1. 수급자격 심사 대상자 명부", "연번 | 성명 | 주민등록번호 | 가구원 현황"]
        households = ("배우자 1명·자녀 2명", "1인 가구", "배우자 1명·부양부모 1명")
        lines.extend(
            f"{i + 1} | {name} | {rrn} | {households[i]}"
            for i, (name, rrn) in enumerate(people)
        )
        return "\n".join(lines)
    if idx == 3:
        lines = ["붙임 1. 건강검진 대상자 결과 명부", "연번 | 성명 | 생년월일 | 검진결과"]
        results = ("공복혈당 재검 권고", "고혈압 의심", "간기능 수치 추적관찰")
        lines.extend(
            f"{i + 1} | {name} | 19{rrn[:2]}-{rrn[2:4]}-{rrn[4:6]} | {results[i]}"
            for i, (name, rrn) in enumerate(people)
        )
        return "\n".join(lines)
    if idx in (6, 7):
        lines = ["붙임 1. 직원 인사관리 대상 명부", "연번 | 성명 | 주민등록번호 | 인사사항"]
        actions = ("전보 검토", "승진 심사", "복무평정")
        lines.extend(
            f"{i + 1} | {name} | {rrn} | {actions[i]}"
            for i, (name, rrn) in enumerate(people)
        )
        return "\n".join(lines)
    name, rrn = people[0]
    if idx == 1:
        return (
            "붙임 1. 채용 지원자 개인정보\n"
            f"성명: {name}\n생년월일: 19{rrn[:2]}-{rrn[2:4]}-{rrn[4:6]}\n"
            "연락처: 010-7421-5836\n계좌번호: 321-45-678901"
        )
    if idx == 4:
        return (
            "붙임 1. 손해배상 청구인 지급정보\n"
            f"성명: {name}\n주민등록번호: {rrn}\n계좌번호: 321-45-678901"
        )
    if idx == 5:
        return (
            "붙임 1. 전입 신고인 개인정보\n"
            f"성명: {name}\n주민등록번호: {rrn}\n신주소: 한빛시 새길로 24, 301호"
        )
    return (
        "붙임 1. 민원인 개인정보\n"
        f"성명: {name}\n주민등록번호: {rrn}\n주소: 한빛시 중앙대로 88\n"
        "연락처: 010-7421-5836"
    )


def ensure_clause6_pii_evidence(
    body_text: str, scenario_index: int | None, row_id: str
) -> str:
    """설명만 있고 값이 없으면 시나리오별 합성 PII 증거 블록을 붙인다."""
    if validate_clause6_pii_evidence(body_text, scenario_index) == []:
        return body_text
    evidence = build_synthetic_pii_evidence(scenario_index, row_id)
    return f"{body_text.rstrip()}\n\n{evidence}".strip()


def validate_clause6_pii_evidence(
    body_text: str, scenario_index: int | None
) -> list[str]:
    """PII라는 단어가 아니라 실제 합성 값과 시나리오 필드가 있는지 검사한다."""
    idx = 0 if scenario_index is None else scenario_index
    violations: list[str] = []
    if idx in (1, 3):
        if not _BIRTH_RE.search(body_text):
            violations.append("생년월일 실제 값이 없음")
    elif not _RRN_RE.search(body_text):
        violations.append("주민등록번호 형식의 합성 값이 없음")
    if idx in (0, 1) and not _PHONE_RE.search(body_text):
        violations.append("개인 연락처 실제 값이 없음")
    if idx in (1, 4) and not _ACCOUNT_RE.search(body_text):
        violations.append("계좌번호 실제 값이 없음")
    required_phrase = {
        2: "가구원 현황",
        3: "검진결과",
        5: "신주소",
        6: "인사사항",
        7: "인사사항",
    }.get(idx)
    if required_phrase and required_phrase not in body_text:
        violations.append(f"시나리오 필수 필드 '{required_phrase}'가 없음")
    return violations
