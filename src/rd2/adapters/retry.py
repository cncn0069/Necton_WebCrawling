"""어댑터 공통 재시도 정책 (설계 문서: 3회 재시도 후 실패 로그)."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 재시도 대상은 "일시적일 수 있는 실패"로 한정한다 — browse CLI 호출 실패(RuntimeError,
# browse_client._run_browse 참고), 서브프로세스 타임아웃, API 응답이 JSON이 아닌 경우.
# KeyError/AttributeError/TypeError 등 코드 자체의 버그는 여기서 잡지 않고 바로
# 전파되어야 한다 — bare Exception으로 잡으면 진짜 버그도 "일시적 실패"로 오인해
# 3회(최대 3초) 재시도한 뒤에야 드러나 디버깅을 늦춘다(plan-eng-review 지적).
RETRYABLE_EXCEPTIONS = (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError)


def with_retry(fn: Callable[[], T], *, max_attempts: int = 3, backoff_seconds: float = 1.0) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            logger.warning("attempt %d/%d failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                time.sleep(backoff_seconds * attempt)
    assert last_exc is not None
    raise last_exc
