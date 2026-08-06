"""O트랙 소스 어댑터 공통 인터페이스 (Approach B)."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv

from rd2.schema.models import Document

# 본문파일 저장 루트 기본값 — repo 루트의 data/ 폴더.
# 파일을 저장하는 모든 어댑터(prism.py, mohw.py)가 개별적으로 계산하던 걸
# 여기로 옮겨 중복 제거(2026-07-09 plan-eng-review 결정 5번). 운영 환경에서
# 기존 파일 저장소를 재사용해야 하므로 RD2_FILES_ROOT로 덮어쓸 수 있다.
load_dotenv()
DEFAULT_FILES_ROOT = Path(
    os.environ.get("RD2_FILES_ROOT") or Path(__file__).resolve().parents[3] / "data"
)


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
