import json
import subprocess

import pytest

from rd2.adapters.retry import with_retry


def test_succeeds_on_first_try_without_retry():
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        return "ok"

    assert with_retry(_fn, backoff_seconds=0.0) == "ok"
    assert calls["n"] == 1


def test_retries_retryable_exception_then_succeeds():
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient browse failure")
        return "ok"

    assert with_retry(_fn, backoff_seconds=0.0) == "ok"
    assert calls["n"] == 3


def test_raises_last_exception_after_max_attempts():
    def _fn():
        raise RuntimeError("still failing")

    with pytest.raises(RuntimeError, match="still failing"):
        with_retry(_fn, max_attempts=3, backoff_seconds=0.0)


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(cmd="browse", timeout=30.0),
        json.JSONDecodeError("bad json", "doc", 0),
    ],
)
def test_retries_other_retryable_exception_types(exc):
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise exc
        return "ok"

    assert with_retry(_fn, backoff_seconds=0.0) == "ok"
    assert calls["n"] == 2


def test_non_retryable_exception_propagates_immediately():
    """2026-07-08 plan-eng-review: with_retry가 bare Exception 대신 RETRYABLE_EXCEPTIONS만
    잡도록 좁혔다 — 코드 버그(KeyError 등)는 재시도 없이 즉시 전파되어야 한다."""
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        raise KeyError("missing_field")

    with pytest.raises(KeyError):
        with_retry(_fn, max_attempts=3, backoff_seconds=0.0)
    assert calls["n"] == 1
