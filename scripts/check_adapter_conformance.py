"""어댑터 필드 완전성 계약 검증 (실제 사이트 대상).

mock 픽스처가 아니라 실제 수집 샘플로 검증한다 — mock은 실제 사이트 구조와
어긋날 수 있다(adapters/conformance.py 모듈 독스트링 참고).

사용법: python scripts/check_adapter_conformance.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.conformance import ConformanceError, assert_conformance  # noqa: E402
from rd2.adapters.mohw import MohwAdapter  # noqa: E402
from rd2.adapters.open_go_kr import OpenGoKrAdapter  # noqa: E402
from rd2.adapters.prism import PrismAdapter  # noqa: E402


def check_open_go_kr(sample_size: int = 15) -> bool:
    adapter = OpenGoKrAdapter()
    end = date.today()
    start = end - timedelta(days=29)

    docs = []
    for raw_item in adapter.fetch_list(start_date=start, end_date=end, max_items=sample_size):
        try:
            detail = adapter.parse_detail(raw_item)
            docs.append(adapter.to_schema(detail))
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP (parse error): {exc}")

    print(f"수집 샘플: {len(docs)}건")
    try:
        assert_conformance(adapter.source_name, docs)
    except ConformanceError as exc:
        print(f"FAIL: {exc}")
        return False
    print("PASS: 정보공개포털 always_filled 계약 충족")
    return True


def check_prism(sample_size: int = 10) -> bool:
    adapter = PrismAdapter()

    docs = []
    for raw_item in adapter.fetch_list(max_items=sample_size):
        try:
            detail = adapter.parse_detail(raw_item)
            docs.append(adapter.to_schema(detail))
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP (parse error): {exc}")

    print(f"PRISM 수집 샘플: {len(docs)}건")
    try:
        assert_conformance(adapter.source_name, docs)
    except ConformanceError as exc:
        print(f"FAIL: {exc}")
        return False
    print("PASS: PRISM always_filled 계약 충족")
    return True


def check_mohw(sample_size: int = 10) -> bool:
    adapter = MohwAdapter()

    docs = []
    for raw_item in adapter.fetch_list(max_items=sample_size):
        try:
            detail = adapter.parse_detail(raw_item)
            docs.append(adapter.to_schema(detail))
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP (parse error): {exc}")

    print(f"보건복지부 수집 샘플: {len(docs)}건")
    try:
        assert_conformance(adapter.source_name, docs)
    except ConformanceError as exc:
        print(f"FAIL: {exc}")
        return False
    print("PASS: 보건복지부 always_filled 계약 충족")
    return True


if __name__ == "__main__":
    ok_open_go_kr = check_open_go_kr()
    ok_prism = check_prism()
    ok_mohw = check_mohw()
    sys.exit(0 if (ok_open_go_kr and ok_prism and ok_mohw) else 1)
