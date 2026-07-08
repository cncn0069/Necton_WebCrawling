"""O트랙 소스 어댑터 공통 인터페이스 (Approach B)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterator

from rd2.schema.models import Document


class SourceAdapter(ABC):
    """출처(정보공개포털, 나라장터, PRISM, 국가기록원 등)별로 구현하는 공통 인터페이스."""

    source_name: str

    @abstractmethod
    def fetch_list(self, **kwargs: Any) -> Iterator[dict]:
        """목록 페이지(또는 API)에서 원시(raw) 아이템들을 순회한다."""
        raise NotImplementedError

    @abstractmethod
    def parse_detail(self, raw_item: dict) -> dict:
        """필요 시 상세 페이지를 조회해 raw_item을 보강한다."""
        raise NotImplementedError

    @abstractmethod
    def to_schema(self, enriched_item: dict) -> Document:
        """보강된 raw dict를 통합 Document 스키마로 변환한다."""
        raise NotImplementedError
