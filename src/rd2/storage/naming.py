"""한글 source/doc_type 값을 영어 코드로 매핑하는 단일 진실 공급원.

data/{source}/{doc_type}/ 폴더명, DB의 source/doc_type 컬럼, payload_json 안의
같은 값이 전부 이 모듈의 상수를 참조한다. 새 출처/문서유형을 추가할 때 이 파일
하나만 고치면 어댑터·conformance.py·스크립트·테스트가 전부 갱신된다
(2026-07-09 plan-eng-review 결정 1번 — data/ 폴더명 한글→영어 정리).
"""

from __future__ import annotations

# --- source 코드 ---
SOURCE_PRISM = "PRISM"  # 원래부터 영문 — 변경 없음
SOURCE_MOHW = "mohw"  # 보건복지부 — scripts/collect_mohw.py 이름과 통일
SOURCE_OPEN_GO_KR = "open_go_kr"  # 정보공개포털 — scripts/collect_open_go_kr.py 이름과 통일
SOURCE_ALIO = "alio"  # ALIO 공공기관 경영정보 공개시스템 — scripts/collect_alio.py 이름과 통일
SOURCE_MOLIT = "molit"  # 국토교통부 정책정보 게시판 — scripts/collect_molit.py 이름과 통일

# --- doc_type 코드 ---
DOC_TYPE_RESEARCH_REPORT = "research_report"  # 연구보고서 (PRISM)
DOC_TYPE_BID_NOTICE = "bid_notice"  # 입찰공고 (mohw 기본값)
DOC_TYPE_PRE_SPEC_NOTICE = "pre_spec_notice"  # 사전규격공개 (mohw)
DOC_TYPE_BID_RENOTICE = "bid_renotice"  # 입찰재공고 (mohw)
DOC_TYPE_PUBLIC_OFFERING = "public_offering"  # 공모 (mohw)
DOC_TYPE_NOTICE = "notice"  # 공고 — 위 키워드에 안 걸리는 mohw 최종 폴백
DOC_TYPE_OFFICIAL_DOCUMENT = "official_document"  # 공문 (open_go_kr, 메타데이터 전용)
DOC_TYPE_POLICY_MATERIAL = "policy_material"  # 정책정보 (molit 기본값)
DOC_TYPE_MEETING_MINUTES = "meeting_minutes"  # 회의록 (molit, 제목 키워드로 분리)
DOC_TYPE_SYNTHETIC_DOCUMENT = "synthetic_document"  # 합성문서 (synthetic-llm, 메타데이터 전용)
DOC_TYPE_AUDIT_RESULT = "audit_result"  # 감사결과 (alio 고정값 — 검색어 자체가 "감사결과")
DOC_TYPE_REPORT = "report"  # 보고서/결과보고 (open_go_kr, 제목 키워드로 분리)
DOC_TYPE_PERSONNEL = "personnel"  # 인사발령 (open_go_kr, 제목 키워드로 분리)
DOC_TYPE_APPROVAL = "approval"  # 승인/승인요청 (open_go_kr, 제목 키워드로 분리)
DOC_TYPE_REPLY_NOTIFICATION = "reply_notification"  # 회신/통보 (open_go_kr, 제목 키워드로 분리)
DOC_TYPE_BUDGET_EXECUTION = "budget_execution"  # 지급/지출/품의 등 예산집행 (open_go_kr, 최다 비중 52%)
DOC_TYPE_PLAN = "plan"  # 계획(안) (open_go_kr, 제목 키워드로 분리)
DOC_TYPE_BUSINESS_TRIP = "business_trip"  # 출장 (open_go_kr, 제목 키워드로 분리)

# 마이그레이션 스크립트가 순회할 한글→영어 딕셔너리.
# 어댑터/conformance.py는 위 개별 상수를 직접 참조하고, 값 치환이 필요한
# 일회성 스크립트만 이 두 딕셔너리를 쓴다 — 그래야 상수 하나만으로 부족한
# "기존에 저장된 한글 값을 순회하며 바꾼다" 케이스에서 별도 ad hoc 매핑을
# 다시 만들지 않는다(2026-07-09 outside voice 지적 반영).
LEGACY_SOURCE_MAP: dict[str, str] = {
    "보건복지부": SOURCE_MOHW,
    "정보공개포털": SOURCE_OPEN_GO_KR,
}

LEGACY_DOC_TYPE_MAP: dict[str, str] = {
    "연구보고서": DOC_TYPE_RESEARCH_REPORT,
    "입찰공고": DOC_TYPE_BID_NOTICE,
    "사전규격공개": DOC_TYPE_PRE_SPEC_NOTICE,
    "입찰재공고": DOC_TYPE_BID_RENOTICE,
    "공모": DOC_TYPE_PUBLIC_OFFERING,
    "공고": DOC_TYPE_NOTICE,
    "공문": DOC_TYPE_OFFICIAL_DOCUMENT,
    "합성문서": DOC_TYPE_SYNTHETIC_DOCUMENT,
}
