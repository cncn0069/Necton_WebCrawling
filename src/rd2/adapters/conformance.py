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
from rd2.storage.naming import (
    SOURCE_ALIO,
    SOURCE_KOREA_KR,
    SOURCE_ME,
    SOURCE_MOE,
    SOURCE_MOEL,
    SOURCE_MOEL_POLICY,
    SOURCE_MOHW,
    SOURCE_MOLIT,
    SOURCE_OPEN_GO_KR,
    SOURCE_ORGINL_INFO,
    SOURCE_PRISM,
)

ADAPTER_FIELD_CONTRACTS: dict[str, dict[str, list[str]]] = {
    SOURCE_OPEN_GO_KR: {
        # 실사(2026-07-07, 15~16건 샘플)로 확인: 이 필드들은 항상 실제 값이 있었다.
        # 비어있으면 어댑터 파싱 버그로 간주한다. doc_type은 2026-07-13 제목 키워드
        # 기반 분류(_infer_doc_type) 추가 후 항상 값이 있음(매칭 안 되면 공문으로 폴백).
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "content_summary",
            "doc_type",
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
    SOURCE_PRISM: {
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
    SOURCE_MOHW: {
        # 최초 실사(2026-07-08, 목록 15건 + 상세 3건 샘플)로는 start_date/end_date도
        # always_filled로 보였으나, 실제 전수 크롤링(같은 날, 733건 샘플)으로 확인해보니
        # 92~93%가 비어있었다 — start_date/end_date는 always_filled에서 뺐다(아래
        # "never_from_source에도 안 넣는 이유" 참고, adapters/mohw.py의 _parse_period
        # 독스트링에 원인 기록).
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
        ],
        # 이 게시판엔 단위업무/분류체계/목차/수행기관/비공개근거 개념 자체가 없다.
        # start_date/end_date는 여기 넣지 않는다 — "전혀 없는 개념"이 아니라 최근
        # 게시물(2021년 이후)엔 실제로 채워지는 조건부 필드라서(2012~2020년 게시물만
        # 사이트가 빈 값으로 등록해둠), never_from_source(이 소스는 원천적으로 이
        # 필드가 없다는 뜻)에 넣으면 오해를 유발한다. always_filled/never_from_source
        # 둘 다에 없는 필드는 조건부로만 채워지는 필드라는 모듈 독스트링 규칙 그대로
        # 조건부로 둔다.
        "never_from_source": [
            "unit_task",
            "subject_category",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
        ],
    },
    SOURCE_MOLIT: {
        # 실사(2026-07-09, 상세 10건 샘플, id=4891~4901)로 확인: 이 필드들은
        # 항상 실제 값이 있었다. subject_category(분류)는 mohw와 달리 이 게시판엔
        # 항상 존재했다.
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
            "subject_category",
        ],
        # 이 게시판엔 단위업무/목차/수행기관/비공개근거/시작·종료일 개념 자체가 없다
        # (mohw와 동일한 성격의 공지형 게시판).
        "never_from_source": [
            "unit_task",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
            "start_date",
            "end_date",
        ],
    },
    SOURCE_ALIO: {
        # 실사(2026-07-09, 검색 API 직접 호출)로 확인: 검색 결과 한 행이 첨부파일
        # 한 건과 1:1이라 이 필드들은 항상 채워진다 — body_file_path도 예외 없이
        # 다운로드 대상이 있는 행만 검색 결과로 나온다(section=attach 자체가
        # "첨부파일 탭"이므로). title/department/body_text/table_of_contents는
        # 검색 API가 아니라 게시글 상세 조각(doc.html/toc.html)에서 채워진다
        # (2026-07-09 사용자 지적으로 추가 — 상세 조회 없이는 title이 기관 불문
        # 전부 "내부·외부 감사결과"로 뭉개져 있었다). 상세 조각 8건 실사 샘플로
        # 전부 채워짐을 확인(parse_detail이 404 등으로 못 가져오면 검색 API
        # 필드로 폴백하되, 그 경우도 title/production_date 자체는 채워짐 —
        # department/body_text/table_of_contents만 비게 된다).
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
            "body_file_path",
            "body_text",
            "table_of_contents",
            "start_date",
            "end_date",
        ],
        # doc.html의 기준일→start_date, 제출일→end_date로 매핑한다(2026-07-09
        # 사용자 결정) — 감사가 다루는 시점과 실제 공개된 시점 사이의 간격을
        # "문서 공개 판단에 걸린 시일"로 본다. 이 소스엔 단위업무/분류체계/
        # 수행기관/비공개사유 개념 자체가 없다.
        "never_from_source": [
            "unit_task",
            "subject_category",
            "content_summary",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
            "start_date",
            "end_date",
        ],
    },
    SOURCE_MOE: {
        # 최초 실사(2026-07-13, 목록 113건 + 상세 6건 샘플)로는 department도
        # always_filled로 보였으나, 실제 전수 크롤링(같은 날, 113건 전체)으로 확인해보니
        # boardSeq=52286(2014년도 예산 및 기금운용계획 개요 게시물)은 담당부서 <td>가
        # 원본 HTML 자체에서 빈 채로 등록돼 있었다(어댑터 파싱 버그 아님, 원문 확인
        # 완료) — mohw의 start_date/end_date와 같은 성격이라 department는
        # always_filled에서 뺐다. molit과 달리 이 게시판엔 "분류" 개념이 없다.
        "always_filled": [
            "title",
            "ordering_agency",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
        ],
        # mohw와 같은 성격의 공지형 게시판 — 단위업무/분류체계/목차/수행기관/비공개근거/
        # 시작·종료일 개념 자체가 없다. department는 여기 넣지 않는다 — "전혀 없는
        # 개념"이 아니라 대부분(56건 중 55건) 채워지는 조건부 필드라서(위 참고),
        # never_from_source(원천적으로 개념이 없다는 뜻)에 넣으면 오해를 유발한다.
        "never_from_source": [
            "unit_task",
            "subject_category",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
            "start_date",
            "end_date",
        ],
    },
    SOURCE_KOREA_KR: {
        # 실사(2026-07-13, korea.kr 정책브리핑 보도자료)로 확인: 목록 페이지에
        # title/body_text/date/agency가 전부 항상 존재한다(정부가 능동 배포하는
        # 보도자료라 moe와 같은 성격 — 빈 값이 있으면 파싱 버그로 간주). department는
        # 신뢰할 수 있는 정적 소스가 없어(상세페이지 "담당자안내"가 팝업 JS) 아예
        # 수집 대상에서 뺐다 — moe의 "가끔 원본이 비워둠" 조건부 필드와 다른 성격.
        "always_filled": [
            "title",
            "ordering_agency",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
            "body_text",
        ],
        # 보도자료 게시판이라 단위업무/분류체계/목차/수행기관/비공개근거/
        # 시작·종료일 개념 자체가 없다(moe와 동일한 성격). department도 이 어댑터는
        # 애초에 채우지 않기로 설계했으므로 여기 포함한다.
        "never_from_source": [
            "department",
            "unit_task",
            "subject_category",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
            "start_date",
            "end_date",
        ],
    },
    SOURCE_ME: {
        # 실사(2026-07-14, me.go.kr 행정규칙 고시·훈령·예규)로 확인: 목록+상세
        # 페이지에 title/department/date/doc_type이 전부 항상 존재한다(정부가
        # 공식 제정·공포한 행정규칙이라 빈 값이면 파싱 버그로 간주).
        "always_filled": [
            "title",
            "ordering_agency",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
        ],
        # 이 어댑터는 open_go_kr의 official_document와 같은 성격의 메타데이터
        # 전용 어댑터다 — 본문 전문은 law.go.kr의 인증 API(OC 키 필요)로만 열람
        # 가능해 body_text/body_file_path를 아예 수집 대상에서 뺐다(2026-07-14
        # 결정). department는 목록의 "소관부서명"을 ordering_agency로 이미
        # 쓰고 있어 별도 부서 필드가 없다.
        "never_from_source": [
            "department",
            "unit_task",
            "subject_category",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
            "start_date",
            "end_date",
            "body_text",
            "body_file_path",
        ],
    },
    SOURCE_MOEL: {
        # 실사(2026-07-14, moel.go.kr 훈령·예규·고시)로 확인: title/department/
        # date/doc_type/body_text가 상세페이지에 전부 항상 존재한다(정부가 공식
        # 제정·공포한 행정규칙이라 빈 값이면 파싱 버그로 간주). me.py와 달리 본문
        # 전문을 실제로 제공하는 소스라 body_text를 always_filled에 넣는다.
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
            "body_text",
        ],
        # me.py와 동일한 성격의 행정규칙 게시판 — 단위업무/분류체계/목차/수행기관/
        # 비공개근거/시작·종료일 개념 자체가 없다. body_file_path는 첨부파일이
        # 없는 문서도 있어(조건부 필드) 여기 넣지 않는다.
        "never_from_source": [
            "unit_task",
            "subject_category",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
            "start_date",
            "end_date",
        ],
    },
    SOURCE_MOEL_POLICY: {
        # 실사(2026-07-14, moel.go.kr 정책자료실)로 확인: title/department/date/
        # doc_type/body_text가 상세페이지에 전부 항상 존재한다.
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "cso_classification",
            "doc_type",
            "body_text",
        ],
        # moel과 동일한 성격의 정책자료 게시판(행정규칙 아님) — 단위업무/분류체계/
        # 목차/수행기관/비공개근거/시작·종료일 개념 자체가 없다. body_file_path는
        # 첨부파일이 없는 문서도 있어(조건부 필드) 여기 넣지 않는다.
        "never_from_source": [
            "unit_task",
            "subject_category",
            "table_of_contents",
            "performing_agency",
            "non_disclosure_reason",
            "cso_sub_clause",
            "start_date",
            "end_date",
        ],
    },
    SOURCE_ORGINL_INFO: {
        # 실사(2026-07-13, wonmun 다운로드 체인 리버스엔지니어링 중 5건 실제
        # 다운로드 성공 — 공개 3건 + 부분공개 1건)로 확인. fetch_list()가
        # ORGNAL_YN != "Y"인 항목을 이미 걸러내므로(이 게시판에 들어온 이상
        # 첨부파일이 있다는 뜻) body_file_path는 여기선 always_filled로 봐도
        # 되지만, 다운로드 자체가 실패(quarantine)할 수 있는 소스라 일부러
        # 조건부로 남겨둔다 — 다운로드 실패는 파싱 버그가 아니라 정상적인
        # 운영 실패 모드라서 always_filled 위반으로 시끄럽게 만들고 싶지 않음.
        "always_filled": [
            "title",
            "ordering_agency",
            "department",
            "production_date",
            "disclosure_status",
            "doc_type",
        ],
        # 정보목록(open_go_kr)과 동일한 성격의 게시판 — 단위업무/목차/수행기관/
        # 비공개근거 조항 개념 자체가 없다.
        "never_from_source": [
            "table_of_contents",
            "performing_agency",
            "cso_sub_clause",
            "start_date",
            "end_date",
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
