"""여러 첨부파일 중 대표 파일을 고르는 공용 로직.

원래 prism.py에 있던 로직인데, mohw.py도 프로젝트당 파일이 여러 개(입찰공고서/
제안요청서, hwpx/pdf 등)일 때 같은 방식으로 대표 파일을 골라야 해서 공용 모듈로
옮겼다(2026-07-08).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path


def normalize_for_match(text: str) -> str:
    return "".join(text.split()).lower()


def pick_primary_file(files: list[dict], title: str, *, filename_key: str = "fileNm") -> dict:
    """파일 딕셔너리 목록 중, 제목과 가장 비슷한 파일명을 가진 것을 대표로 고른다
    (2026-07-07 사용자 결정 — 부수 문서가 실수로 대표 파일이 되는 걸 방지)."""
    normalized_title = normalize_for_match(title)

    def _score(f: dict) -> float:
        name = Path(f[filename_key]).stem
        return SequenceMatcher(None, normalized_title, normalize_for_match(name)).ratio()

    return max(files, key=_score)
