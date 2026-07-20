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

import random

FIXED_AGENCY_BY_SOURCE: dict[str, str] = {
    "moel": "고용노동부",
    "moe": "교육부",
    "mohw": "보건복지부",
    "molit": "국토교통부",
}

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
