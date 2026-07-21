"""candidate/annotated 문서에서 실제 기관명(ordering_agency)을 해석한다.

R3(2026-07-20 사용자 결정): 완전 가상 폴백 문서라도 기관명만은 실제 존재하는
값이어야 한다 — "가상기관(합성)" 같은 가짜 이름을 쓰지 않는다. 이 모듈은 그
실제 기관명을 어디서 가져올지 결정한다.

- moel/moe/mohw/molit 어댑터는 ordering_agency가 소스코드에 하드코딩된
  상수라서(예: moel -> "고용노동부") source 값만으로 즉시 알 수 있다. DB 조회가
  필요 없다.
- alio/korea_kr/open_go_kr/orginl_info/prism/seoul_opengov(+me, 문서별로
  달라질 수 있어 고정 테이블 제외)는 문서마다 실제 기관명이 달라서 rd2 DB
  조회가 필요하다. documents 테이블에는 candidate의 doc_id와 직접 매칭되는
  PK/hash 컬럼이 없고, body_file_path만 있다 — 포맷이
  "{source}/{doc_type}/{bucket}/{doc_id}_{filename}"이라 doc_id는 파일명
  접두사로만 안다. annotated JSON의 source_pdf_path는 버킷 폴더가 없는 별도
  포맷이라 바이트 일치 조회(DocumentStore.get_by_body_file_path)가 안 통해서
  LIKE 퍼지 매칭이 필요하다.
- DocumentStore는 생성 시점에 스키마 마이그레이션 DDL+commit을 실행하므로
  읽기 전용 파일럿 스크립트에서 인스턴스화하면 안 된다(generate_cs_pilot.py가
  이미 DocumentStore를 피하고 raw pymysql 연결만 쓰는 이유와 동일) — 이 모듈도
  raw pymysql 커넥션을 그대로 받아 쓴다.
"""

from __future__ import annotations

import datetime
import random

FIXED_AGENCY_BY_SOURCE: dict[str, str] = {
    "moel": "고용노동부",
    "moe": "교육부",
    "mohw": "보건복지부",
    "molit": "국토교통부",
}

# 1~4호(C트랙) 폴백 생성용 실존 기관 화이트리스트 (2026-07-20 plan-eng-review, Approach D).
# rd2 DB의 실제 기관 풀에는 안보/외교/수사 계열 기관이 0건이라
# sample_real_agency_and_date_for_fallback()로는 이 조항들에 맞는 기관을 뽑을 수
# 없다 — 대신 저장소 루트 logo/ 폴더에 이미 준비된 실존 기관 로고 인벤토리를
# 화이트리스트 소스로 쓴다(법령 조사 불필요, R3를 시각 자료까지 포함해 충족).
# 조항 안에 성격이 다른 시나리오가 섞여 있어도(예: 1호는 사이버안보/국정원/군사기밀/
# 수사비밀 시나리오가 혼재) 화이트리스트 자체를 조항 단위로 넉넉히 잡아 랜덤 선택만으로
# "완전히 무관하지는 않은" 수준의 그럴싸함을 확보한다 — 시나리오별로 정교하게 매칭하는
# 것은 이번 스코프에서 과함으로 판단됨(사용자 확인).
MARKING_SPEC_AGENCY_WHITELIST: dict[str, list[str]] = {
    "1": ["국가정보원", "국방부", "검찰청", "고위공직자범죄수사처"],
    "2": ["국방부", "국가정보원"],
    "3": ["정부부처"],  # 원자력안전위원회/소방청 등 전용 로고가 아직 없어 generic 폴백
    "4": ["검찰청", "고위공직자범죄수사처"],
}

# logo/ 폴더의 실제 파일명 매핑. "정부부처"는 화이트리스트에 없는 조항이나 미매칭
# 기관의 generic 폴백으로도 쓰인다.
AGENCY_LOGO_FILENAMES: dict[str, str] = {
    "감사원": "감사원.png",
    "검찰청": "검찰.png",
    "고위공직자범죄수사처": "고위공직자범죄수사처.png",
    "국방부": "국방부.png",
    "국가정보원": "국정원.png",
    "대통령경호처": "대통령경호처.png",
    "대통령실": "대통령실.svg",
    "정부부처": "정부부처.png",
}

_GENERIC_WHITELIST_AGENCY = "정부부처"


def select_whitelisted_agency(clause_no: str, rng: random.Random) -> tuple[str, str]:
    """1~4호 폴백 생성용 실존 기관을 화이트리스트에서 고르고 로고 파일명과 함께 반환한다.

    화이트리스트에 없는 clause_no는 generic(정부부처)으로 폴백한다 — Approach D는
    법정근거/화이트리스트 미확보를 이유로 생성을 막지 않는다(on_hold 폐기, 2026-07-20).
    """
    pool = MARKING_SPEC_AGENCY_WHITELIST.get(clause_no) or [_GENERIC_WHITELIST_AGENCY]
    agency = rng.choice(pool)
    logo_filename = AGENCY_LOGO_FILENAMES.get(agency, AGENCY_LOGO_FILENAMES[_GENERIC_WHITELIST_AGENCY])
    return agency, logo_filename


def synthesize_plausible_date(rng: random.Random, *, years_back: int = 3) -> str:
    """화이트리스트 기관용 개연성 있는 생산일자를 ISO 문자열로 생성한다.

    화이트리스트 기관은 rd2 DB에 실수집 이력이 없어 실제 행과 날짜를 짝지을 수
    없다(sample_real_agency_and_date_for_fallback와 다른 점) — 호출자는
    non_disclosure_reason 등에 "화이트리스트 기반 합성"임을 명시해 RD-1에
    투명하게 전달해야 한다(Outside Voice 지적, 2026-07-20).
    """
    today = datetime.date.today()
    days_back = rng.randrange(1, years_back * 365)
    return (today - datetime.timedelta(days=days_back)).isoformat()

# me는 어댑터 기본값("기후에너지환경부")이 있지만 문서별로 오버라이드될 수 있어
# 고정 테이블에서 제외하고 문서별 DB 조회 대상으로 둔다.
PER_DOC_AGENCY_SOURCES = frozenset(
    {"alio", "korea_kr", "open_go_kr", "orginl_info", "prism", "seoul_opengov", "me"}
)


def _fetch_agency_by_body_file_path(
    conn, *, source: str, doc_type: str, doc_id: str
) -> str | None:
    """body_file_path LIKE '{source}/{doc_type}/%/{doc_id}_%'로 퍼지 조회한다.

    여러 건이 매치되면 첫 행만 쓴다(같은 doc_id가 같은 source/doc_type 안에서
    중복될 일은 게시글 ID 특성상 거의 없다).
    """
    pattern = f"{source}/{doc_type}/%/{doc_id}_%"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ordering_agency FROM documents WHERE body_file_path LIKE %s LIMIT 1",
            (pattern,),
        )
        row = cur.fetchone()
    if not row:
        return None
    agency = row["ordering_agency"] if isinstance(row, dict) else row[0]
    return agency or None


def resolve_agency_for_candidate(candidate: dict, conn=None) -> str | None:
    """candidate(find_candidates.py/candidates.py가 만든 dict)에서 실제 기관명을 구한다.

    source가 고정기관 테이블에 있으면 DB 없이 즉시 반환한다. 아니면 conn(raw
    pymysql 커넥션)으로 문서별 조회를 시도한다. 못 찾으면 None을 반환하고,
    호출자는 그 행을 스킵해야 한다 — 가짜 기관명으로 채우지 않는다(R3).
    """
    source = candidate.get("source")
    if not source:
        return None

    fixed = FIXED_AGENCY_BY_SOURCE.get(source)
    if fixed is not None:
        return fixed

    if source not in PER_DOC_AGENCY_SOURCES or conn is None:
        return None

    doc_type = candidate.get("doc_type")
    doc_id = candidate.get("doc_id")
    if not doc_type or not doc_id:
        return None

    return _fetch_agency_by_body_file_path(conn, source=source, doc_type=doc_type, doc_id=str(doc_id))


def fetch_real_agency_date_samples(conn) -> list[tuple[str, str]]:
    """rd2 DB에 실제로 존재하는 (ordering_agency, production_date) 쌍 목록을 반환한다.

    조항 1~4 폴백 생성용 — 실제 기관명+생산일자 페어의 유일한 출처다. 기관명과
    날짜는 같은 실제 문서 행에서 함께 나와야 한다(서로 무관한 행끼리 조합하면
    "그 기관이 그 날짜에 실제로 존재했다"는 근거가 사라진다).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ordering_agency, production_date FROM documents "
            "WHERE ordering_agency IS NOT NULL AND ordering_agency != '' "
            "AND production_date IS NOT NULL"
        )
        rows = cur.fetchall()

    samples: list[tuple[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            agency, prod_date = row["ordering_agency"], row["production_date"]
        else:
            agency, prod_date = row[0], row[1]
        if agency and prod_date:
            samples.append((agency, str(prod_date)))
    return samples


def sample_real_agency_and_date_for_fallback(
    rng: random.Random, samples: list[tuple[str, str]]
) -> tuple[str, str]:
    """조항 1~4 폴백 문서용으로 실제 (기관명, 생산일자) 쌍 목록에서 하나를 뽑는다.

    목록이 비어 있으면 가짜 값을 지어내는 대신 실패시킨다(R3 강제) — 이 경로가
    조용히 "가상기관(합성)"이나 임의의 날짜로 되돌아가면 안 된다.
    """
    if not samples:
        raise RuntimeError(
            "실제 (ordering_agency, production_date) 쌍이 비어 있어 폴백 값을 "
            "고를 수 없습니다 — rd2 DB에 최소 1건 이상의 실제 값이 있어야 합니다."
        )
    return rng.choice(samples)
