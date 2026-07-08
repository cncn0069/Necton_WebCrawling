"""어댑터별 필드 완전성 계약.

새 어댑터(나라장터/PRISM/국가기록원 등)를 추가할 때마다 "이 소스는 어떤 필드를
항상 채워야 하는가"를 잊어버리지 않도록, 어댑터별로 명시적인 계약을 선언하고
실제 수집 샘플로 검증한다.

여기 없는 필드는 조건부로만 채워지는 필드다(예: non_disclosure_reason은
공개가 아닐 때만, cso_sub_clause는 C/S 트랙일 때만) — 계약에서 강제하지 않는다.

주의: 이 계약은 mock 픽스처가 아니라 실제 수집 샘플(scripts/check_adapter_conformance.py)로
검증해야 한다. tests/test_open_go_kr_adapter.py의 SAMPLE_DETAIL_HTML은 dlsrCdNm/nstClNm에
가짜 값을 채워 넣고 있어 실제 사이트(둘 다 항상 빈 태그)와 다르다 — mock만으로는
이번에 발견된 disclosure_status 버그 같은 걸 잡을 수 없다.
"""

from __future__ import annotations

from rd2.schema.models import Document

ADAPTER_FIELD_CONTRACTS: dict[str, dict[str, list[str]]] = {
    "정보공개포털": {
        # 실사(2026-07-07, 15~16건 샘플)로 확인: 이 필드들은 항상 실제 값이 있었다.
        # 비어있으면 어댑터 파싱 버그로 간주한다.
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "content_summary",
        ],
        # 이 어댑터(O트랙, 사전정보공개 목록) 설계상 원천적으로 값이 없는 필드 —
        # 다른 트랙/어댑터에서만 의미 있음.
        "never_from_source": [
            "cso_sub_clause",
            "performing_agency",
            "start_date",
            "end_date",
            "body_file_path",
        ],
    },
    "PRISM": {
        # 실사(2026-07-07)로 확인: 목록·상세 어디서든 항상 값이 있었다.
        "always_filled": [
            "title",
            "ordering_agency",
            "disclosure_status",
            "start_date",
            "end_date",
        ],
        # production_date는 PRISM에 해당 개념이 없음(연구기간의 start/end만 존재).
        # body_file_path는 disclosure_status가 공개일 때만 채워진다(2026-07-07 결정:
        # 비공개 문서는 파일 다운로드 API 자체를 호출하지 않음) — 조건부 필드라
        # always_filled/never_from_source 어느 쪽에도 넣지 않는다.
        "never_from_source": [
            "production_date",
        ],
    },
    "보건복지부": {
        # 실사(2026-07-08, 목록 15건 + 상세 3건 샘플)로 확인: 게시판 성격상
        # 항상 값이 있었다. disclosure_status/cso_classification은 이 소스에
        # 비공개 개념이 아예 없어 항상 공개/O로 고정되고, doc_type도 제목 키워드로
        # 항상 추론되므로 always_filled에 넣는다(adapters/mohw.py 참고).
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "start_date",
            "end_date",
            "doc_type",
        ],
        # 이 게시판엔 단위업무/분류체계/목차/수행기관/비공개근거 개념 자체가 없다.
        "never_from_source": [
            "unit_task",
            "subject_category",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
        ],
    },
}


class ConformanceError(AssertionError):
    pass


def assert_conformance(source_name: str, docs: list[Document]) -> None:
    """실제 수집 샘플이 어댑터 필드 계약을 지키는지 검증한다.

    docs는 mock이 아니라 실제 수집 결과여야 한다 — mock 픽스처는 실제 사이트
    구조와 어긋날 수 있다(disclosure_status 버그가 그 사례).
    """
    if not docs:
        raise ConformanceError(f"{source_name}: 샘플이 비어있어 검증할 수 없음")
    contract = ADAPTER_FIELD_CONTRACTS.get(source_name)
    if contract is None:
        raise ConformanceError(
            f"{source_name}: ADAPTER_FIELD_CONTRACTS에 계약이 없음 — "
            "새 어댑터 추가 시 계약을 먼저 정의해야 한다."
        )
    violations: list[str] = []
    for field in contract.get("always_filled", []):
        empty = [d for d in docs if not getattr(d, field)]
        if empty:
            violations.append(
                f"{field}: 샘플 {len(docs)}건 중 {len(empty)}건 비어있음 "
                f"(always_filled 계약 위반 — 어댑터 파싱 버그 의심)"
            )
    if violations:
        raise ConformanceError(f"{source_name} 필드 완전성 계약 위반:\n" + "\n".join(violations))
