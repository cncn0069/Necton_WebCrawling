"""생성 계약과 렌더러가 공유하는 표제부 필드 정의."""

from __future__ import annotations


DRAFT_BLANK_HEADER_KEYS: frozenset[str] = frozenset({"문서번호", "시행일자"})
