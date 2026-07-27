"""Canonical JSON 직렬화와 그 위에서 계산하는 content-addressed hash.

여러 모듈이 각자 ad hoc하게 JSON을 직렬화해 SHA-256을 찍던 것(예:
``rd2.extraction.storage.build_extraction_id``의 ``ensure_ascii=True``,
``rd2.augmentation.candidates._candidate_id``의 ``ensure_ascii=False`` — 서로
다른 설정)을 이 모듈 하나로 모은다. Coverage plan/candidate profile처럼 여러
run에 걸쳐 재현 가능해야 하는 새 hash는 반드시 여기를 거친다. 기존 두 함수는
동작 변경 위험이 있어 이번 스코프에서는 손대지 않는다.

설계 문서 docs/design-coverage-matrix-diversity-audit-20260723.md §4.2, §12.3.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

NORMALIZATION_VERSION = "canonical-json-v1"

_MAX_FLOAT_DECIMALS = 12


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, _MAX_FLOAT_DECIMALS)
    if value is None:
        return None
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical_json_bytes는 문자열 키만 지원합니다: {key!r}")
            normalized[unicodedata.normalize("NFC", key)] = _normalize(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise TypeError(f"canonical_json_bytes는 {type(value).__name__} 타입을 지원하지 않습니다")


def canonical_json_bytes(value: Any, *, normalization_version: str) -> bytes:
    """value를 NFC 정규화 + 키 정렬 + 고정 separator로 결정적 직렬화한다.

    호출자는 배열 내부의 canonical 정렬(예: coverage_cell_key 오름차순)을
    직접 책임진다 — 이 함수는 배열 순서를 바꾸지 않는다. Windows 경로도
    호출자가 repo-relative POSIX 문자열로 바꿔서 넘겨야 한다.
    """
    wrapped = {"normalization_version": normalization_version, "value": _normalize(value)}
    encoded = json.dumps(wrapped, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return encoded.encode("utf-8")


def canonical_sha256(value: Any, *, normalization_version: str = NORMALIZATION_VERSION) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value, normalization_version=normalization_version)
    ).hexdigest()


def compute_matrix_hash(
    *,
    valid_cell_keys: Any,
    agency_categories: Any,
    config: Any,
    normalization_version: str = NORMALIZATION_VERSION,
) -> str:
    """coverage matrix 정체성 hash — 실측 후보 수는 절대 입력에 넣지 않는다.

    매트릭스 정체성(어떤 셀이 유효한지)은 후보 가용성과 무관해야 계획을 다시
    돌려도 같은 matrix_hash가 나온다(설계 문서 §4.2).
    """
    payload = {
        "valid_cell_keys": list(valid_cell_keys),
        "agency_categories": list(agency_categories),
        "config": config,
    }
    return "sha256:" + canonical_sha256(payload, normalization_version=normalization_version)


def compute_candidate_profile_digest(
    profile_manifest: Any, *, normalization_version: str = NORMALIZATION_VERSION
) -> str:
    return "sha256:" + canonical_sha256(profile_manifest, normalization_version=normalization_version)


def compute_plan_run_id(
    plan_without_run_id_and_created_at: Any,
    *,
    normalization_version: str = NORMALIZATION_VERSION,
) -> str:
    """설계 문서 §12.3: run_id = sha256(canonical(plan_without_run_id_and_created_at))."""
    return "sha256:" + canonical_sha256(
        plan_without_run_id_and_created_at, normalization_version=normalization_version
    )
