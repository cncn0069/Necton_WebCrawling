"""RD-2 C/S 트랙 실제 문서 근거 기반 종단간(end-to-end) 파일럿.

조항(제1~8호)당 소수 건씩 메타데이터+본문을 생성해 CSV로 출력한다. 목적은
물량 확보가 아니라 "진짜 병목이 어디인지 확인"이다 — 운영 DB(rd2 MariaDB)에는
바로 쓰지 않는다. 사람이 CSV를 검토한 뒤 승인된 건만 별도 스텝에서 반영한다.

2026-07-20 재설계(사용자 결정 R1~R3):
- R1: EC2에서 rd2 DB에 .env의 MARIADB_* 변수로 접속하는 것은 그대로 유지한다.
  다만 DB 용도가 바뀐다 — 아래 R2.
- R2: 조항 5/6/7/8은 더 이상 DB의 content_summary/non_disclosure_reason(메타
  데이터)을 생성 근거로 쓰지 않는다. 실제 운영에서는 메타데이터 없이 문서
  내용만으로 판단해야 하므로, 학습 데이터 생성도 그 조건을 재현한다 —
  find_candidates.py/candidates.py가 정규식/키워드로 찾아둔 실제 문서 span
  (`data/candidates/clause_{5,6,7,8}.jsonl`)을 근거로 쓴다. 그 span이 가리키는
  annotated 문서 원문 전체를 LLM에 주고, 거기서 템플릿이 요구하는 필드에 맞는
  실제 데이터를 추출·재구성한다 — 매칭 span은 "왜 이 조항인지"의 근거 앵커일
  뿐, 실제 추출은 문서 전체 맥락에서 한다. 조항 1~4는 candidates.py에 span
  탐지 로직 자체가 없어(국가안보/진행중 수사 등은 애초에 공개문서에서 매칭될
  수 없음) 항상 완전 폴백 생성으로 간다.
- R3: 완전 가상 폴백 문서(조항 1~4, 또는 span 후보가 부족한 조항의 나머지분)
  라도 기관명·생산일자는 rd2 DB에 실제로 존재하는 (ordering_agency,
  production_date) 쌍을 그대로 써야 한다 — "가상기관(합성)" 같은 가짜 값은
  쓰지 않는다.

레이아웃(표·결재선·공개구분 등)은 doc_templates.py의 DocTemplateSpec +
pdf_render.py가 전부 결정적으로 그린다 — LLM은 본문 프로즈 몇 슬롯만 채운다
(pdf_render.py 확인 완료, 2026-07-20). validate_row()를 이 파일럿의 실제 생성
경로에 연결해, 렌더링 전에 시나리오 모순 문구를 잡아낸다.

설계 문서: ~/.gstack/projects/cncn0069-Necton_WebCrawling/
안정현-feat-open-go-kr-alternative-sources-design-20260714-115815.md

주의: 저장소 루트의 rd2.db 파일은 2026-07-09 MariaDB 마이그레이션 이전의
stale SQLite 잔재다 — 이 스크립트는 그 파일을 절대 참조하지 않고 .env의
MARIADB_* 변수로 운영 DB에 직접(읽기 전용) 연결한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _common import ensure_src_on_path

ensure_src_on_path()

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from openai import RateLimitError

from rd2.generators.agency_categories import get_agency_category
from rd2.generators.agency_resolver import (
    AGENCY_LOGO_FILENAMES,
    MARKING_SPEC_AGENCY_WHITELIST,
    MILITARY_SECRET_MARK_FILENAMES,
    fetch_real_agency_date_samples,
    is_military_secret_agency,
    resolve_agency_for_candidate,
    sample_diverse_agency_and_date_for_fallback,
    select_military_secret_grade,
    select_whitelisted_agency,
    synthesize_plausible_date,
)
from rd2.generators.clause_data import CLAUSES
from rd2.generators.doc_templates import find_template, validate_row
from rd2.generators.doc_type_inference import infer_doc_type
from rd2.generators.generate import SpanSeedInput, generate_clause_document, generate_span_seeded_body
from rd2.generators.pdf_render import render_document_pdf
from rd2.generators.security_mark import (
    generate_agency_letterhead_mark,
    generate_agency_watermark,
    generate_classification_stamp,
    generate_military_secret_mark,
)
from rd2.generators.template_matrix import TARGET_BY_KEY, infer_subclause_key

load_dotenv()

CSV_FIELDNAMES = [
    "row_id", "seed_type", "seed_span_id", "clause_no", "cso_subclause_key",
    "cso_classification",
    "title", "ordering_agency", "department", "unit_task", "production_date",
    "subject_category", "matched_span_text", "non_disclosure_reason", "body_text",
    "disclosure_status", "document_status", "source", "source_url", "doc_type", "is_synthetic",
    "field_source", "status", "model", "tokens_in", "tokens_out", "gen_time_s",
    "sampling_seed", "prompt_version", "template_id", "template_violations",
    "military_secret_grade", "agency_logo_filename",
    # --target-matrix 모드 전용(2026-07-21 추가) — 기존 --per-clause 경로의 행은
    # 이 세 필드가 빈 문자열로 남는다. "영구 0건 셀" 예외가 적용된 셀은 반드시
    # cell_zero_candidate_exception="true"로 CSV에서 바로 보이게 한다(설계 문서
    # Phase B 예외 규칙 — 조용히 묻히면 안 됨).
    "cell_key", "cell_fallback_ratio", "cell_zero_candidate_exception",
]

SPAN_SEEDED_PROMPT_VERSION = "span-seeded-v1-20260720"
FALLBACK_PROMPT_VERSION = "fallback-v2-20260720"
DEFAULT_PER_CLAUSE = 8
ADMIN_STATUS_PROMPT_VERSION = "administrative-status-v1-20260716"
ADMIN_STATUS_SAMPLE_ROW_ID = "admin-status-attachment-missing-0"

# --target-matrix 모드 기본값(2026-07-21 설계 문서 Phase B). 셀당 기본 목표는
# 2000건이지만 --per-cell-target으로 오버라이드할 수 있다.
DEFAULT_PER_CELL_TARGET = 2000
# "영구 0건 셀" 예외(설계 문서 Phase B 예외 규칙): 실측 후보가 0건인 셀은 2000건을
# 폴백만으로 채우도록 강제하지 않는다 — 대신 이 작은 기본값만큼만 폴백으로 채운다.
# --force-full-target-for-zero-cells를 명시적으로 주면 이 예외를 끄고 원래
# --per-cell-target 그대로 채운다(그래도 100% 폴백이라는 사실 자체는 계속 표시된다).
DEFAULT_ZERO_CANDIDATE_TARGET = 50
DEFAULT_CONCURRENCY = 4  # 설계 문서 "이슈 6" — LLM 호출 동시성, 보수적 기본값

# candidates.py는 조항 5/6/7/8만 span 탐지 로직이 있다 — 1~4호는 항상 폴백.
_SPAN_CLAUSES = ("5", "6", "7", "8")

_MAX_SOURCE_DOCUMENT_CHARS = 4000  # generate.py의 _MAX_ANCHOR_TEXT_CHARS와 동일 절단 관례
# 근거 span을 중심으로 앞/뒤에 배분할 글자 수. 문서 앞부분부터 자르면 근거 span이
# 뒤쪽 페이지에 있을 때 그 앞뒤 문맥이 통째로 잘려나가 LLM이 근거 문구만 보고
# 본문을 얇게 쓰는 문제가 있었다(2026-07-21 사용자 피드백).
_CONTEXT_CHARS_BEFORE = _MAX_SOURCE_DOCUMENT_CHARS // 2
_CONTEXT_CHARS_AFTER = _MAX_SOURCE_DOCUMENT_CHARS - _CONTEXT_CHARS_BEFORE


def connect_mariadb() -> pymysql.connections.Connection:
    """운영 MariaDB에 읽기 전용으로 연결한다.

    DocumentStore(storage/db.py)는 쓰기/마이그레이션 전용 클래스(생성 시점에
    스키마 DDL+commit을 실행함)라 읽기 전용 파일럿에서 인스턴스화하면 안 된다
    — 이 파일 어디에도 connection.commit()/INSERT/UPDATE를 호출하지 않는 것으로
    읽기 전용을 코드 수준에서 보장한다(별도 읽기 전용 DB 계정 발급은 스코프 밖).
    """
    try:
        return pymysql.connect(
            host=os.environ["MARIADB_HOST"],
            port=int(os.environ.get("MARIADB_PORT", 3306)),
            user=os.environ["MARIADB_USER"],
            password=os.environ["MARIADB_PASSWORD"],
            database=os.environ["MARIADB_DATABASE"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except KeyError as exc:
        raise RuntimeError(
            f"MariaDB 접속 정보 누락: {exc} 환경변수가 .env에 없습니다."
        ) from exc
    except pymysql.MySQLError as exc:
        raise RuntimeError(f"MariaDB 접속 실패: {exc}") from exc


def fetch_all_span_candidates(candidates_dir: Path) -> dict[str, list[dict]]:
    """data/candidates/clause_{5,6,7,8}.jsonl을 읽어 clause_no로 버킷팅한다.

    candidate dict 자체에는 clause_no가 들어있지 않다(candidates.py의
    _base_candidate 참고) — 어느 파일에서 나왔는지로만 조항을 구분한다.
    """
    buckets: dict[str, list[dict]] = {}
    for clause_no in _SPAN_CLAUSES:
        path = candidates_dir / f"clause_{clause_no}.jsonl"
        candidates: list[dict] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        candidates.append(json.loads(line))
        buckets[clause_no] = candidates
    return buckets


def _sample_candidates(candidates: list[dict], count: int, rng: random.Random) -> list[dict]:
    if len(candidates) <= count:
        return list(candidates)
    return rng.sample(candidates, count)


def load_annotated_document_text(
    source_pdf_path: str, annotated_root: Path, *, target_span_id: int | None = None
) -> str | None:
    """candidate의 source_pdf_path로 원본 annotated JSON을 찾아 원문 텍스트를 재구성한다.

    is_boilerplate=false span만 페이지 순서대로 이어붙인다. source_pdf_path는
    "data/{source}/{doc_type}/{filename}.pdf" 형태(repo-root 기준, OS에 따라
    구분자가 다를 수 있어 먼저 정규화한다) — 첫 세그먼트(data)를 annotated_root로
    바꾸고 확장자를 .json으로 바꾸면 annotated JSON 경로가 된다.

    target_span_id가 주어지면 그 span을 기준으로 앞/뒤 문맥을 함께 잘라 반환한다
    (근거 span 자체는 항상 창 안에 포함됨) — candidates.py가 찾은 근거 span은
    문서 아무 곳에나 있을 수 있는데, 예전처럼 문서 맨 앞부터 고정 길이로 자르면
    근거 span이 뒤쪽 페이지에 있을 때 그 주변 문맥이 통째로 빠져 LLM이 참고할
    실제 내용이 얇아졌다. target_span_id를 못 찾으면(예: span_id 없음, 예전
    candidate 포맷) 문서 맨 앞부터 자르는 기존 동작으로 폴백한다.
    """
    if not source_pdf_path:
        return None
    normalized = source_pdf_path.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    parts = [p for p in normalized.split("/") if p]
    if (
        not parts
        or parts[0].lower() != "data"
        or any(part in {".", ".."} for part in parts)
    ):
        return None

    root = annotated_root.resolve()
    json_path = root.joinpath(*parts[1:]).with_suffix(".json").resolve()
    try:
        json_path.relative_to(root)
    except ValueError:
        return None
    if not json_path.exists():
        return None

    doc = json.loads(json_path.read_text(encoding="utf-8"))
    entries: list[tuple[object, str]] = []
    for page in doc.get("pages", []):
        for span in page.get("spans", []):
            if span.get("is_boilerplate"):
                continue
            text = (span.get("cleaned_text") or "").strip()
            if text:
                entries.append((span.get("span_id"), text))
    if not entries:
        return None

    target_index = None
    if target_span_id is not None:
        target_index = next(
            (i for i, (span_id, _) in enumerate(entries) if span_id == target_span_id), None
        )
    if target_index is None:
        return "\n".join(text for _, text in entries)[:_MAX_SOURCE_DOCUMENT_CHARS]

    before: list[str] = []
    before_chars = 0
    i = target_index - 1
    while i >= 0 and before_chars < _CONTEXT_CHARS_BEFORE:
        before.append(entries[i][1])
        before_chars += len(entries[i][1]) + 1
        i -= 1
    before.reverse()

    after: list[str] = []
    after_chars = 0
    i = target_index + 1
    while i < len(entries) and after_chars < _CONTEXT_CHARS_AFTER:
        after.append(entries[i][1])
        after_chars += len(entries[i][1]) + 1
        i += 1

    window = [*before, entries[target_index][1], *after]
    return "\n".join(window)[:_MAX_SOURCE_DOCUMENT_CHARS]


def _with_retry(fn, *, max_attempts: int = 2):
    """LLM 호출 1회 재시도. rate limit(429)은 지수 백오프로 별도 처리."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RateLimitError as exc:
            last_exc = exc
            if attempt < max_attempts:
                time.sleep(2**attempt)
        except Exception as exc:  # noqa: BLE001 — 실패는 status 컬럼에 기록, 파이프라인은 계속
            last_exc = exc
            if attempt < max_attempts:
                time.sleep(1.0)
    assert last_exc is not None
    raise last_exc


def generate_span_seeded_row(
    row_id: str,
    clause_no: str,
    candidate: dict,
    *,
    client,
    model: str,
    sampling_seed: int,
    conn,
    annotated_root: Path,
    db_lock: threading.Lock | None = None,
) -> dict | None:
    """candidate(find_candidates.py 결과 1건)로 문서 원문 근거 기반 행을 만든다.

    실제 기관명을 해석하지 못하거나 annotated 원문을 찾지 못하면 None을
    반환한다 — 호출자는 그 candidate를 스킵하고 폴백으로 보충해야 한다
    (가짜 기관명으로 채우지 않는다, R3).

    db_lock: --target-matrix/--concurrency 경로에서 여러 스레드가 같은 pymysql
    커넥션(conn)을 공유할 때만 넘긴다 — pymysql 커넥션 하나를 여러 스레드가
    동시에 쓰면 안 되므로(스레드 안전하지 않음) DB 조회 구간만 잠깐 잠근다. LLM
    호출(느린 부분)은 잠금 밖에서 그대로 동시 실행된다. None(기본값)이면 기존
    --per-clause 경로처럼 잠금 없이 그대로 호출한다(하위호환, 단일 스레드 사용).
    """
    if db_lock is not None:
        with db_lock:
            agency = resolve_agency_for_candidate(candidate, conn)
    else:
        agency = resolve_agency_for_candidate(candidate, conn)
    if agency is None:
        print(f"  [skip] {row_id}: 실제 기관명을 해석할 수 없어 스킵(R3 — 가짜 기관명 금지)")
        return None

    matched_span_text = candidate.get("text") or ""
    doc_type = infer_doc_type(clause_no, keyword_text=matched_span_text)
    subclause_key = infer_subclause_key(
        clause_no, doc_type, keyword_text=matched_span_text
    )
    source_document_text = load_annotated_document_text(
        candidate.get("source_pdf_path") or "", annotated_root,
        target_span_id=candidate.get("span_id"),
    )
    if not source_document_text:
        print(f"  [skip] {row_id}: annotated 원문을 찾을 수 없어 스킵")
        return None

    clause = CLAUSES[clause_no]
    template = find_template(clause_no, doc_type, subclause_key)
    seed = SpanSeedInput(
        ordering_agency=agency,
        clause_no=clause_no,
        classification=clause.classification.value,
        doc_type=doc_type,
        source_document_text=source_document_text,
        matched_span_text=matched_span_text,
        template=template,
    )

    start = time.monotonic()
    status = "ok"
    department = unit_task = production_date = ""
    body_text = ""
    tokens_in = tokens_out = None
    try:
        result = _with_retry(lambda: generate_span_seeded_body(seed, client=client, model=model))
        department, unit_task, production_date, body_text = (
            result.department, result.unit_task, result.production_date, result.body_text,
        )
        tokens_in, tokens_out = result.tokens_in, result.tokens_out
        if not body_text or not body_text.strip():
            status = "empty_body"
    except Exception as exc:  # noqa: BLE001
        status = "llm_error"
        body_text = f"[에러: {exc}]"
    gen_time_s = round(time.monotonic() - start, 2)

    return {
        "row_id": row_id,
        "seed_type": "span_seeded",
        "seed_span_id": candidate.get("span_id") if candidate.get("span_id") is not None else "",
        "clause_no": clause_no,
        "cso_subclause_key": subclause_key or "",
        "cso_classification": clause.classification.value,
        "title": f"[문서 근거] {clause.title} — {agency}",
        "ordering_agency": agency,
        "department": department,
        "unit_task": unit_task,
        "production_date": production_date,
        "subject_category": clause.title,
        "matched_span_text": matched_span_text,
        "non_disclosure_reason": f"제{clause_no}호 — {clause.title}",
        "body_text": body_text,
        "disclosure_status": "비공개",
        "document_status": "",
        "source": "synthetic-llm",
        "source_url": "",
        "doc_type": doc_type,
        "is_synthetic": True,
        "field_source": json.dumps(
            {
                "ordering_agency": "resolved_from_real_document",
                "clause_no": "matched_by_candidates_span",
                "doc_type": (
                    "inferred_from_matched_span; "
                    f"source_doc_type={candidate.get('doc_type') or ''}"
                ),
                "matched_span_text": "copied_from_real_document",
                "department": "synthesized",
                "unit_task": "synthesized",
                "production_date": "synthesized",
                "body_text": "synthesized_from_source_document",
            },
            ensure_ascii=False,
        ),
        "status": status,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "gen_time_s": gen_time_s,
        "sampling_seed": sampling_seed,
        "prompt_version": SPAN_SEEDED_PROMPT_VERSION,
        "military_secret_grade": "",  # 5~8호 span-seeded 경로는 군사기밀 대상 기관이 없음
        "agency_logo_filename": "",  # 5~8호(S)는 마크를 안 그리므로 기관 로고가 필요 없음
        # --target-matrix 모드에서만 _apply_cell_metadata()가 실제 값으로 덮어쓴다.
        "cell_key": "",
        "cell_fallback_ratio": "",
        "cell_zero_candidate_exception": "",
    }


def generate_fallback_row(
    row_id: str,
    clause_no: str,
    *,
    client,
    model: str,
    sampling_seed: int,
    ordering_agency: str,
    production_date: str,
    agency_source: str = "real_db_sample",
    military_secret_grade: str | None = None,
    agency_logo_filename: str = "",
    scenario_index: int | None = None,
) -> dict:
    """span 후보가 없는 조항(1~4호) 또는 span 후보가 부족한 조항의 나머지분을
    D1 4번(완전 독립 시나리오)으로 백필한다.

    ordering_agency/production_date의 출처는 두 가지다: (1) 5~8호는
    agency_resolver.sample_diverse_agency_and_date_for_fallback로 뽑은 rd2 DB의
    실제 값(균등 추출 + 보충 화이트리스트, R3), (2) 1~4호는 agency_resolver.select_whitelisted_agency +
    synthesize_plausible_date로 만든 화이트리스트 기반 값(2026-07-20
    plan-eng-review, Approach D — rd2 DB에 안보/외교/수사 계열 실수집 이력이
    없어 (1) 방식을 쓸 수 없다). agency_source로 어느 쪽인지 구분해 필드
    출처를 정직하게 기록한다. 이 함수 자체는 전달받은 값을 검증하지 않고
    그대로 신뢰한다.

    scenario_index는 호출자가 select_whitelisted_agency(clause_no, rng,
    scenario_index=...)에 넘긴 것과 같은 값이어야 한다 — 그래야 본문이 실제로
    그 시나리오(예: "대북 접경지역 군사대비태세 강화")로 생성되고, ordering_agency도
    같은 시나리오에 맞는 기관(예: 국방부)이 되어 마크·본문·기관이 서로 어긋나지
    않는다(2026-07-21 사용자 지적 — 이전엔 기관과 시나리오가 서로 무관하게
    독립적으로 뽑혔다).

    military_secret_grade("1급"/"2급"/"3급")는 ordering_agency가 국방부/국가정보원일
    때만 호출자가 채워 넘긴다(agency_resolver.is_military_secret_agency) — 본문
    생성 프롬프트에 그 등급에 맞는 심각성으로 쓰라는 지시를 추가하고, CSV에도 같은
    값을 남겨 render_pdfs_for_csv()가 [별표 2] 등급 마크를 고를 수 있게 한다
    (2026-07-21 사용자 결정 — 마크와 본문 내용이 어긋나지 않아야 함).
    """
    clause = CLAUSES[clause_no]
    start = time.monotonic()
    status = "ok"
    title = clause.title
    non_disclosure_reason = f"제{clause_no}호 — {clause.title}"
    disclosure_status = "비공개"
    body_text = ""
    try:
        doc = _with_retry(
            lambda: generate_clause_document(
                clause_no,
                ordering_agency=ordering_agency,
                production_date=production_date,
                client=client,
                model=model,
                military_secret_grade=military_secret_grade,
                scenario_index=scenario_index,
            )
        )
        title = doc.title
        non_disclosure_reason = doc.non_disclosure_reason or non_disclosure_reason
        disclosure_status = doc.disclosure_status.value
        body_text = doc.body_text or ""
        if not body_text.strip():
            status = "empty_body"
    except Exception as exc:  # noqa: BLE001
        status = "llm_error"
        body_text = f"[에러: {exc}]"
    if agency_source == "whitelist_synthetic":
        non_disclosure_reason += " (화이트리스트 기반 합성 — rd2 DB 실수집 이력 없음)"
    gen_time_s = round(time.monotonic() - start, 2)
    doc_type = infer_doc_type(clause_no, keyword_text=title)
    subclause_key = infer_subclause_key(
        clause_no,
        doc_type,
        keyword_text="\n".join((title, non_disclosure_reason, body_text)),
    )

    return {
        "row_id": row_id,
        "seed_type": "synthetic_fallback",
        "seed_span_id": "",
        "clause_no": clause_no,
        "cso_subclause_key": subclause_key or "",
        "cso_classification": clause.classification.value,
        "title": title,
        "ordering_agency": ordering_agency,
        "department": "",
        "unit_task": "",
        "production_date": production_date,
        "subject_category": clause.title,
        "matched_span_text": "",
        "non_disclosure_reason": non_disclosure_reason,
        "body_text": body_text,
        "disclosure_status": disclosure_status,
        "document_status": "",
        "source": "synthetic-llm",
        "source_url": "",
        "doc_type": doc_type,
        "is_synthetic": True,
        "field_source": json.dumps(
            {
                "ordering_agency": (
                    "whitelist_synthetic_no_db_history"
                    if agency_source == "whitelist_synthetic"
                    else "sampled_from_real_db_value"
                ),
                "production_date": (
                    "synthesized_plausible_range"
                    if agency_source == "whitelist_synthetic"
                    else "sampled_from_real_db_value"
                ),
                "body_text": "synthesized",
            },
            ensure_ascii=False,
        ),
        "status": status,
        "model": model,
        "tokens_in": None,
        "tokens_out": None,
        "gen_time_s": gen_time_s,
        "sampling_seed": sampling_seed,
        "prompt_version": FALLBACK_PROMPT_VERSION,
        "military_secret_grade": military_secret_grade or "",
        "agency_logo_filename": agency_logo_filename,
        # --target-matrix 모드에서만 _apply_cell_metadata()가 실제 값으로 덮어쓴다.
        "cell_key": "",
        "cell_fallback_ratio": "",
        "cell_zero_candidate_exception": "",
    }


def generate_admin_status_sample_row(*, sampling_seed: int) -> dict:
    """제9조 조항 후보 없이 행정 처리 상태만으로 보류된 부분공개 샘플을 만든다.

    후보 키워드/LLM/DB 조회를 거치지 않는다. ``cso_sub_clause``가 없는 실제
    운영 상태를 별도 메타데이터로 관리해야 한다는 정책을 파일럿 CSV에서
    검증하기 위한 결정적 샘플이다.
    """
    return {
        "row_id": ADMIN_STATUS_SAMPLE_ROW_ID,
        "seed_type": "administrative_status",
        "seed_span_id": "",
        "clause_no": "",
        "cso_subclause_key": "",
        "cso_classification": "S",
        "title": "지역 생활SOC 조성사업 검토자료 공개 요청 건",
        "ordering_agency": "가상광역시 도시정책과",
        "department": "도시정책과",
        "unit_task": "정보공개 문서 등록",
        "production_date": "2026-07-16",
        "subject_category": "행정 처리 상태",
        "matched_span_text": "",
        "non_disclosure_reason": "행정 처리 상태 — 붙임 참조 문서의 첨부파일 미등록으로 공개 보류",
        "body_text": (
            "지역 생활SOC 조성사업 관련 검토자료의 공개 요청을 접수하였음.\n"
            "본문에서 참조한 붙임 자료가 전자문서시스템에 등록되지 않아 문서 완결성을 확인 중임.\n"
            "붙임 등록 및 내용 확인 후 공개 범위를 재검토할 예정임."
        ),
        "disclosure_status": "부분공개",
        # AdminStatus.ATTACHMENT_MISSING.value와 같은 정규화된 상태값.
        "document_status": "첨부미등록",
        "source": "synthetic-template",
        "source_url": "",
        "doc_type": "official_document",
        "is_synthetic": True,
        "field_source": json.dumps(
            {
                "cso_sub_clause": "not_applicable",
                "document_status": "template",
                "all_other_fields": "template",
            },
            ensure_ascii=False,
        ),
        "status": "ok",
        "model": "",
        "tokens_in": None,
        "tokens_out": None,
        "gen_time_s": 0,
        "sampling_seed": sampling_seed,
        "prompt_version": ADMIN_STATUS_PROMPT_VERSION,
        "military_secret_grade": "",
        "agency_logo_filename": "",
        "cell_key": "",
        "cell_fallback_ratio": "",
        "cell_zero_candidate_exception": "",
    }


# --- --target-matrix 모드: (조항, 세부조항, 문서유형, 행정상태) 4축 셀 ---
#
# 설계 문서(Phase B)는 template_matrix.py에 행정상태 축을 추가하고 candidates.py의
# _ADMIN_STATUS_RULES_BY_DOC_TYPE를 공개 이름으로 바꾸는 작업을 별도 워크스트림
# (이 세션에서는 "Lane B")으로 분리했다. 이 파일 구현 시점에 그 변경이 아직
# 이 브랜치에 들어오지 않아서(template_matrix.TARGET_BY_KEY는 여전히 (clause_no,
# subclause_key, doc_type) 3축뿐), 아래는 그 상태 축을 candidates.py의 문서유형별
# 규칙에서 직접 읽어 이 파일 안에서만 4축으로 합성한다.
#
# TODO(Lane B 병합 후): template_matrix.py가 문서유형-조건부 상태 축을 가진 새
# 구조(예: TARGET_BY_KEY_WITH_STATUS 류)를 노출하면 build_target_cells()를 그
# 구조를 직접 쓰도록 교체하고, 아래 _load_admin_status_rules_by_doc_type()의
# private-name 폴백은 제거해도 된다.
def _load_admin_status_rules_by_doc_type() -> dict[str, tuple]:
    """candidates.py의 문서유형별 행정상태 규칙 딕셔너리를 가져온다.

    공개 이름(ADMIN_STATUS_RULES_BY_DOC_TYPE, Lane B가 붙일 이름)을 먼저 찾고,
    아직 없으면 현재 이름(_ADMIN_STATUS_RULES_BY_DOC_TYPE)으로 폴백한다 — 어느
    쪽이 로드됐는지와 무관하게 이후 로직은 완전히 동일하게 동작한다.
    """
    try:
        from rd2.augmentation.candidates import (
            ADMIN_STATUS_RULES_BY_DOC_TYPE as rules_by_doc_type,  # type: ignore[attr-defined]
        )
    except ImportError:
        from rd2.augmentation.candidates import (
            _ADMIN_STATUS_RULES_BY_DOC_TYPE as rules_by_doc_type,
        )
    return rules_by_doc_type


@dataclass(frozen=True)
class TargetCell:
    """3중쌍(조항,세부조항,문서유형) + 그 문서유형에 등록된 행정상태 하나.

    설계 문서 "조합 규모" 절: 행정상태는 세부조항이 아니라 문서유형에 종속되므로
    모든 세부조항 × 모든 상태의 완전 격자가 아니다 — 이 클래스 자체가 이미
    문서유형-조건부로 만들어진 셀 하나를 표현한다(build_target_cells 참고).
    admin_status가 빈 문자열이면 그 문서유형에 등록된 행정상태 규칙 자체가
    없다는 뜻이다(no_rule_defined) — 그래도 (조항,세부조항,문서유형) 삼중쌍은
    매트릭스에서 빠지지 않도록 상태 축 없는 셀 1개로 남는다.
    """

    clause_no: str
    subclause_key: str
    doc_type: str
    admin_status: str
    template_id: str

    @property
    def cell_key(self) -> str:
        return f"{self.clause_no}|{self.subclause_key}|{self.doc_type}|{self.admin_status or '-'}"


def build_target_cells(clause_nos: tuple[str, ...] = _SPAN_CLAUSES) -> list[TargetCell]:
    """template_matrix.TARGET_BY_KEY(3축)에 문서유형별 행정상태를 곱해 4축 셀을 만든다.

    clause_nos 기본값은 candidates.py가 span 탐지를 지원하는 5~8호뿐이다(1~4호는
    조항 후보 탐지 로직 자체가 없어 이 매트릭스 대상이 아니다 — R2/R3 참고).
    """
    rules_by_doc_type = _load_admin_status_rules_by_doc_type()
    cells: list[TargetCell] = []
    for (clause_no, subclause_key, doc_type), target in TARGET_BY_KEY.items():
        if clause_no not in clause_nos:
            continue
        statuses = [rule.document_status for rule in rules_by_doc_type.get(doc_type, ())]
        if statuses:
            cells.extend(
                TargetCell(clause_no, subclause_key, doc_type, status, target.template_id)
                for status in statuses
            )
        else:
            cells.append(TargetCell(clause_no, subclause_key, doc_type, "", target.template_id))
    return cells


def _bucket_span_candidates_by_subclause_doc_type(
    candidates: list[dict], clause_no: str
) -> dict[tuple[str, str], list[dict]]:
    """조항 단위 span 후보를 (세부조항, 문서유형)로 재버킷팅해 셀별 실측 후보 수를 센다.

    candidates.py가 만드는 clause 후보 dict 자체에는 doc_type/subclause_key가
    없다 — 실제 생성 시점(generate_span_seeded_row)과 똑같이
    infer_doc_type/infer_subclause_key를 candidate 텍스트에 적용해야 어느 셀에
    속하는지 알 수 있다. admin_status는 이 버킷에 포함하지 않는다 — 행정상태
    후보는 candidates.py에서 조항 후보와 완전히 별개 경로(find_administrative_candidates,
    문서 단위)로 탐지되므로, span 하나당 상태를 1:1로 확정할 근거가 아직 없다
    (Phase A가 다뤄야 할 갭 — 이 스코프에서는 실측 신호를 (세부조항,문서유형)
    단위까지만 쓰고, admin_status 축은 목표 매트릭스 쪽에만 존재한다고 명시한다).
    """
    buckets: dict[tuple[str, str], list[dict]] = {}
    for candidate in candidates:
        text = candidate.get("text") or ""
        doc_type = infer_doc_type(clause_no, keyword_text=text)
        subclause_key = infer_subclause_key(clause_no, doc_type, keyword_text=text) or ""
        buckets.setdefault((subclause_key, doc_type), []).append(candidate)
    return buckets


@dataclass(frozen=True)
class CellPlan:
    """셀 하나의 실측 후보 + 그로부터 결정된 유효 목표건수."""

    cell: TargetCell
    real_candidates: list[dict]
    effective_target: int
    is_zero_candidate_exception: bool


def plan_cell(
    cell: TargetCell,
    real_candidates: list[dict],
    *,
    per_cell_target: int,
    zero_candidate_target: int,
    force_full_target_for_zero_cells: bool,
) -> CellPlan:
    """"영구 0건 셀" 예외 규칙(설계 문서 Phase B 예외 규칙)을 적용한다.

    실측 후보가 0건이면 2000건(기본 per_cell_target)을 폴백만으로 채우도록
    강제하지 않고 훨씬 작은 zero_candidate_target으로 목표를 낮춘다 —
    force_full_target_for_zero_cells를 명시적으로 준 경우에만 원래 목표
    그대로 채운다(그래도 is_zero_candidate_exception 플래그는 계속 True다 —
    "실측 근거 0건으로 전량 폴백"이라는 사실 자체는 목표 크기와 무관하게
    항상 리포트/CSV에 표시돼야 한다).
    """
    is_zero_candidate = len(real_candidates) == 0
    if is_zero_candidate and not force_full_target_for_zero_cells:
        effective_target = zero_candidate_target
    else:
        effective_target = per_cell_target
    return CellPlan(
        cell=cell,
        real_candidates=real_candidates,
        effective_target=effective_target,
        is_zero_candidate_exception=is_zero_candidate,
    )


def _run_concurrently(tasks: list, *, concurrency: int) -> list:
    """0-인자 콜러블 목록을 최대 concurrency개까지 동시 실행한다(설계 문서 "이슈 6").

    ThreadPoolExecutor.map은 완료 순서가 아니라 제출 순서로 결과를 yield하므로,
    동시성을 켜고 꺼도 반환되는 리스트의 순서(-> CSV 행 순서)는 그대로다. 각
    태스크 내부의 실제 LLM 호출은 이미 _with_retry로 재시도/지수 백오프를 하고
    있으므로(generate_span_seeded_row/generate_fallback_row 참고) 여기서는 그
    로직을 우회하지 않고 그대로 감싸기만 한다. concurrency<=1이거나 태스크가
    1개 이하면 스레드풀을 아예 만들지 않고 순차 실행한다(기존 --per-clause
    경로의 단일 스레드 동작과 완전히 동일).
    """
    if concurrency <= 1 or len(tasks) <= 1:
        return [task() for task in tasks]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        return list(executor.map(lambda task: task(), tasks))


def _apply_cell_metadata(
    row: dict, cell: TargetCell, *, fallback_ratio: float, is_zero_candidate_exception: bool
) -> None:
    """--target-matrix 모드에서만 호출 — 어느 셀 소속인지와 폴백 비율을 CSV에 남긴다.

    "영구 0건 셀" 예외가 적용된 셀은 cell_zero_candidate_exception이 반드시
    "true"로 찍혀야 한다(요구사항: CSV 출력에서 조용히 묻히면 안 됨).
    """
    row["cell_key"] = cell.cell_key
    row["cell_fallback_ratio"] = f"{fallback_ratio:.4f}"
    row["cell_zero_candidate_exception"] = "true" if is_zero_candidate_exception else "false"


def generate_cell_rows(
    cell_plan: CellPlan,
    *,
    resumed_row_ids: set[str],
    client,
    model: str,
    sampling_seed: int,
    conn,
    annotated_root: Path,
    rng: random.Random,
    fallback_samples: list[tuple[str, str]],
    concurrency: int,
    db_lock: threading.Lock,
) -> tuple[list[dict], dict]:
    """셀 하나(조항×세부조항×문서유형×행정상태)의 유효 목표건수를 span-seeded
    우선 + 폴백 보충으로 채운다. --per-clause 경로와 같은 생성 함수·화이트리스트
    규칙을 그대로 재사용하되, 조항 전체가 아니라 셀 하나 분량만 처리한다.

    반환값: (CSV에 쓸 행 리스트, 셀 리포트 1행 dict). fallback_ratio는 이번 실행에서
    "새로 생성한" 행만 기준으로 계산한다 — --resume으로 여러 번 나눠 돌리면 각
    실행의 비율이 그 실행 몫만 반영하고 셀 누적 비율은 아니다(리포트에 한계로
    명시).
    """
    cell = cell_plan.cell
    clause_no = cell.clause_no
    slug = f"{cell.clause_no}-{cell.subclause_key}-{cell.doc_type}-{cell.admin_status or 'none'}"

    sampled_candidates = _sample_candidates(
        cell_plan.real_candidates, cell_plan.effective_target, rng
    )

    span_task_specs: list[tuple[str, dict]] = []
    resumed_span = 0
    for idx, candidate in enumerate(sampled_candidates):
        row_id = f"cell-{slug}-span-{idx}"
        if row_id in resumed_row_ids:
            resumed_span += 1
            continue
        span_task_specs.append((row_id, candidate))

    def _make_span_task(row_id: str, candidate: dict):
        return lambda: generate_span_seeded_row(
            row_id, clause_no, candidate, client=client, model=model,
            sampling_seed=sampling_seed, conn=conn, annotated_root=annotated_root,
            db_lock=db_lock,
        )

    span_tasks = [_make_span_task(row_id, candidate) for row_id, candidate in span_task_specs]
    span_results = _run_concurrently(span_tasks, concurrency=concurrency)

    rows: list[dict] = []
    produced_span = 0
    for row in span_results:
        if row is None:
            continue  # 스킵된 candidate(기관 해석 실패 등) — 폴백으로 보충
        rows.append(row)
        produced_span += 1

    produced_so_far = resumed_span + produced_span
    n_fallback = max(0, cell_plan.effective_target - produced_so_far)

    fallback_specs: list[tuple[str, str, str, str, str | None]] = []
    for fidx in range(n_fallback):
        row_id = f"cell-{slug}-fallback-{fidx}"
        if row_id in resumed_row_ids:
            continue
        if clause_no in MARKING_SPEC_AGENCY_WHITELIST or clause_no in ("1", "2", "3", "4"):
            agency, _logo_filename = select_whitelisted_agency(clause_no, rng)
            prod_date = synthesize_plausible_date(rng)
            agency_source = "whitelist_synthetic"
        else:
            agency, prod_date, agency_source = sample_diverse_agency_and_date_for_fallback(
                rng, fallback_samples
            )
        military_secret_grade = (
            select_military_secret_grade(rng) if is_military_secret_agency(agency) else None
        )
        fallback_specs.append((row_id, agency, prod_date, agency_source, military_secret_grade))

    def _make_fallback_task(
        row_id: str, agency: str, prod_date: str, agency_source: str, military_secret_grade: str | None
    ):
        return lambda: generate_fallback_row(
            row_id, clause_no, client=client, model=model, sampling_seed=sampling_seed,
            ordering_agency=agency, production_date=prod_date, agency_source=agency_source,
            military_secret_grade=military_secret_grade,
        )

    fallback_tasks = [_make_fallback_task(*spec) for spec in fallback_specs]
    fallback_results = _run_concurrently(fallback_tasks, concurrency=concurrency)
    rows.extend(fallback_results)

    produced_fallback = len(fallback_results)
    denom = produced_span + produced_fallback
    if denom > 0:
        fallback_ratio = produced_fallback / denom
    else:
        # 이번 실행에서 새로 생성한 행이 0건(전부 --resume으로 스킵됨) — 실측
        # 후보가 0건인 셀이면 "0건 셀 예외"라는 사실 자체는 계속 100%로 표시한다.
        fallback_ratio = 1.0 if cell_plan.is_zero_candidate_exception else 0.0

    for row in rows:
        _apply_template_validation(row)
        _apply_cell_metadata(
            row, cell, fallback_ratio=fallback_ratio,
            is_zero_candidate_exception=cell_plan.is_zero_candidate_exception,
        )

    report_row = {
        "cell_key": cell.cell_key,
        "clause_no": cell.clause_no,
        "subclause_key": cell.subclause_key,
        "doc_type": cell.doc_type,
        "admin_status": cell.admin_status,
        "template_id": cell.template_id,
        "real_candidates": len(cell_plan.real_candidates),
        "effective_target": cell_plan.effective_target,
        "produced_span_seeded": produced_span,
        "produced_fallback": produced_fallback,
        "fallback_ratio": f"{fallback_ratio:.4f}",
        "zero_candidate_exception": "true" if cell_plan.is_zero_candidate_exception else "false",
    }
    return rows, report_row


CELL_REPORT_FIELDNAMES = [
    "cell_key", "clause_no", "subclause_key", "doc_type", "admin_status", "template_id",
    "real_candidates", "effective_target", "produced_span_seeded", "produced_fallback",
    "fallback_ratio", "zero_candidate_exception",
]


def write_cell_report(report_path: Path, cell_results: list[dict]) -> None:
    """셀별 실측 후보/유효목표/생성건수/폴백비율을 CSV와 별개인 리포트로 남긴다.

    "영구 0건 셀" 예외가 CSV 안에서도 행별로 보이긴 하지만(cell_zero_candidate_exception),
    셀 단위로 한눈에 몇 개 셀이 예외 적용됐는지 보려면 행 단위 CSV를 그룹핑해야
    한다 — 이 리포트는 그 집계를 미리 해서 별도 파일로 남긴다(요구사항: "CSV
    출력이나 부속 리포트에 명시적으로 드러나야 한다").
    """
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CELL_REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(cell_results)


def _apply_template_validation(row: dict) -> None:
    """TEMPLATE_CHECKLIST.md에 명시된 기존 갭(validate_row가 테스트에서만
    호출되던 문제)을 해소한다 — 렌더링 전에 시나리오 모순 문구를 잡아낸다.

    레이아웃(표·결재선·공개구분)은 pdf_render.py가 DocTemplateSpec만 보고
    결정적으로 그리므로 이 검사와 무관하다 — 여기서 잡는 것은 LLM이 담당하는
    좁은 본문 프로즈가 그 결정적 레이아웃/시나리오와 모순되는 경우뿐이다.
    """
    spec = find_template(
        row["clause_no"], row["doc_type"], row.get("cso_subclause_key") or None
    )
    violations = validate_row(spec, row) if spec else []
    row["template_id"] = spec.template_id if spec else ""
    row["template_violations"] = "; ".join(violations)
    if violations and row["status"] == "ok":
        row["status"] = "template_violation"


def _existing_row_ids(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return {row["row_id"] for row in csv.DictReader(f)}


def _safe_pdf_output_path(pdf_dir: Path, filename: str) -> Path:
    """PDF 파일명(문서 제목 기반, 안전하지 않으면 row_id로 폴백된 값)을 디렉터리
    탈출이 불가능한 경로로 검증한다."""
    normalized = str(filename or "").strip()
    if (
        not normalized
        or len(normalized) > 200
        or normalized in {".", ".."}
        or re.fullmatch(r"[\w.-]+", normalized) is None
    ):
        raise ValueError(f"안전하지 않은 row_id: {filename!r}")

    root = pdf_dir.resolve()
    output_path = (root / f"{normalized}.pdf").resolve()
    try:
        output_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"PDF 출력 경로가 지정 디렉터리를 벗어남: {filename!r}") from exc
    return output_path


def _filename_from_title(title: str, *, fallback: str) -> str:
    """문서 제목(row["title"])을 PDF 파일명으로 정규화한다.

    괄호류는 지우고, 대시류는 하이픈으로, 공백은 밑줄로 바꾼 뒤 나머지 비허용
    문자(쉼표 등)는 제거해 _safe_pdf_output_path가 요구하는 [\\w.-]+ 형식을
    만든다. 제목이 비어 있거나 정규화 후 빈 문자열이 되면(예: 특수문자뿐인
    제목) row_id로 폴백한다 — 파일명이 사람이 알아볼 제목과 다르더라도 최소한
    row마다 달라야 하기 때문이다.
    """
    normalized = str(title or "").strip()
    normalized = re.sub(r"[\[\]()（）]", "", normalized)
    normalized = re.sub(r"[—–]", "-", normalized)
    normalized = re.sub(r"\s+", "_", normalized.strip())
    sanitized = re.sub(r"[^\w.-]", "", normalized).strip("._-")
    sanitized = sanitized[:120].strip("._-")
    return sanitized or fallback


def _unique_pdf_output_path(pdf_dir: Path, row: dict, used_filenames: dict[str, int]) -> Path:
    """row 제목 기반 파일명을 만들되, 같은 파일명이 이미 이번 렌더링에서 쓰였으면
    "_2", "_3"... 을 붙여 겹치지 않게 한다.

    fallback 문서(1~4호 화이트리스트, 5~8호 부족분)는 시나리오 문구를 제목으로
    그대로 쓰는데 조항당 시나리오가 3~5개뿐이라 목표 건수(per_clause 5~10건)를
    채우면 같은 제목이 반복되는 게 정상이다 — 그래도 파일명은 겹치면 안 되므로
    여기서 겹침을 해소한다.
    """
    base = _filename_from_title(row.get("title") or "", fallback=row.get("row_id") or "")
    count = used_filenames.get(base, 0)
    used_filenames[base] = count + 1
    candidate = base if count == 0 else f"{base}_{count + 1}"
    return _safe_pdf_output_path(pdf_dir, candidate)


def render_pdfs_for_csv(csv_path: Path, pdf_dir: Path, *, sampling_seed: int) -> int:
    """CSV 행을 기관유형별로 렌더링하되 템플릿 검증 위반 행은 제외한다.

    대외비 분류 스탬프(박스)는 C(기밀) 문서에 페이지당 1회, 파이프라인
    전체가 공유하는 이미지 하나로 충분하다(문구가 항상 "대외비"로 고정).
    military_secret_grade가 있는 행(국방부/국가정보원, 2026-07-21 추가)은 대신
    [별표 2] 등급 마크를 상단·하단 양쪽에 붙인다 — 등급별 마크는 3종류뿐이라
    등급마다 하나씩만 만들어 재사용한다. 배경 워터마크는 가/나/다/라... 글자 대신
    문서를 발행한 기관의 마크(agency_logo_filename)를 옅게 키워 쓴다(2026-07-21
    사용자 결정) — 같은 기관끼리는 캐시를 재사용한다. 좌상단에는 같은 기관 마크를
    작게 한 번 더 찍는다(레터헤드). agency_logo_filename이 비어 있으면(예: 이 컬럼이
    없던 옛 CSV로 --resume) 정부부처 공용 마크로 폴백한다. S 문서는 마크 없음
    (2026-07-14 결정). LLM을 다시 호출하지 않으므로 --resume과 함께 쓰면 기존
    CSV를 그대로 재사용해 비용 없이 PDF만 새로 만들 수 있다.

    PDF 파일명은 row_id가 아니라 문서 제목(row["title"])에서 만든다(2026-07-21
    사용자 결정, _filename_from_title/_unique_pdf_output_path 참고) — row_id는
    CSV 컬럼으로만 남는다. 같은 제목이 반복되면(조항당 시나리오가 3~5개뿐이라
    fallback 문서에서 흔함) "_2", "_3"... 을 붙여 파일명 충돌을 막는다.
    """
    if not csv_path.exists():
        raise RuntimeError(f"CSV 파일이 없습니다: {csv_path} — 먼저 파일럿을 실행하세요.")

    pdf_dir.mkdir(parents=True, exist_ok=True)
    stamp_path = pdf_dir / "_stamp_confidential.png"
    generate_classification_stamp(stamp_path, seed=sampling_seed)

    military_mark_cache: dict[str, Path] = {}

    def _military_mark_for_grade(grade: str) -> Path:
        cached = military_mark_cache.get(grade)
        if cached is not None:
            return cached
        mark_path = pdf_dir / f"_stamp_military_{grade}.png"
        generate_military_secret_mark(mark_path, grade, seed=sampling_seed)
        military_mark_cache[grade] = mark_path
        return mark_path

    letterhead_cache: dict[str, Path] = {}
    agency_watermark_cache: dict[str, Path] = {}

    def _logo_filename_for_row(row: dict) -> str:
        raw = (row.get("agency_logo_filename") or "").strip()
        if raw:
            return raw
        return AGENCY_LOGO_FILENAMES["정부부처"]

    def _letterhead_for_row(row: dict) -> Path:
        logo_filename = _logo_filename_for_row(row)
        cached = letterhead_cache.get(logo_filename)
        if cached is not None:
            return cached
        mark_path = pdf_dir / f"_letterhead_{Path(logo_filename).stem}.png"
        generate_agency_letterhead_mark(mark_path, logo_filename, seed=sampling_seed)
        letterhead_cache[logo_filename] = mark_path
        return mark_path

    def _agency_watermark_for_row(row: dict) -> Path:
        logo_filename = _logo_filename_for_row(row)
        cached = agency_watermark_cache.get(logo_filename)
        if cached is not None:
            return cached
        wm_path = pdf_dir / f"_watermark_{Path(logo_filename).stem}.png"
        generate_agency_watermark(wm_path, logo_filename, seed=sampling_seed)
        agency_watermark_cache[logo_filename] = wm_path
        return wm_path

    rendered = 0
    used_filenames: dict[str, int] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status") == "template_violation" or row.get("template_violations"):
                print(f"  [skip-pdf] {row.get('row_id')}: 템플릿 검증 위반")
                continue
            category = get_agency_category(row.get("ordering_agency"))
            output_path = _unique_pdf_output_path(pdf_dir, row, used_filenames)
            is_confidential = (row.get("cso_classification") or "").upper() == "C"
            watermark_path = _agency_watermark_for_row(row) if is_confidential else None
            agency_mark_path = _letterhead_for_row(row) if is_confidential else None
            grade = (row.get("military_secret_grade") or "").strip()
            if grade and grade in MILITARY_SECRET_MARK_FILENAMES:
                military_mark = _military_mark_for_grade(grade)
                row_stamp_path, row_stamp_top_path = military_mark, military_mark
                mark = f"군사기밀({grade})" if is_confidential else "마크없음"
            else:
                row_stamp_path, row_stamp_top_path = stamp_path, None
                mark = "C(대외비)" if is_confidential else "마크없음"
            render_document_pdf(
                row, category, output_path,
                watermark_path=watermark_path, stamp_path=row_stamp_path,
                stamp_top_path=row_stamp_top_path, agency_mark_path=agency_mark_path,
            )
            rendered += 1
            print(f"  [pdf] {row['row_id']} -> {category} -> {mark} -> {output_path.name}")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=None, help="출력 CSV 경로 (기본: cs_pilot_output.csv)")
    parser.add_argument(
        "--per-clause", type=int, default=DEFAULT_PER_CLAUSE,
        help=f"조항당 목표 건수 (기본 {DEFAULT_PER_CLAUSE}, 5~10 권장)",
    )
    parser.add_argument("--sampling-seed", type=int, default=42, help="후보/폴백 샘플링 고정 시드값")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument(
        "--resume", action="store_true",
        help="기존 CSV에 이미 있는 row_id는 건너뛰고 이어서 생성",
    )
    parser.add_argument(
        "--clauses", default=None,
        help="쉼표구분 조항 목록만 처리(예: 1,5,6). 기본: 홀드 아닌 전체 조항",
    )
    parser.add_argument(
        "--render-pdf", action="store_true",
        help="CSV 생성 후 기관유형별 레이아웃으로 PDF도 렌더링(LLM 재호출 없음)",
    )
    parser.add_argument(
        "--pdf-dir", default=None,
        help="PDF 출력 디렉터리 (기본: cs_pilot_pdfs/)",
    )
    parser.add_argument(
        "--admin-status-sample-only", action="store_true",
        help="제9조 조항과 무관한 행정상태 부분공개 샘플 1건만 생성(DB/LLM 미사용)",
    )
    parser.add_argument(
        "--run-tag", default="",
        help=(
            "여러 EC2 인스턴스에서 사이트별로 나눠 병렬 실행한 뒤 CSV를 합칠 때 "
            "row_id 충돌을 막는 태그(예: --run-tag moel). row_id 순번(span-{idx}/"
            "fallback-{fidx})은 로컬 data/annotated 커버리지에 따라 인스턴스마다 "
            "달라질 수 있어 태그 없이 합치면 서로 다른 문서가 같은 row_id를 갖게 "
            "된다. 지정하면 row_id 앞에 붙는다(예: 'moel-5-span-0'). [\\w.-]만 허용."
        ),
    )
    parser.add_argument(
        "--target-matrix", action="store_true",
        help=(
            "--per-clause 대신 (조항,세부조항,문서유형,행정상태) 4축 셀 단위로 "
            "5~8호 목표를 채운다(설계 문서 Phase B). --per-clause와 동시에 쓸 수 "
            "없고, 이 플래그를 주지 않으면 기존 --per-clause 경로가 그대로 동작한다."
        ),
    )
    parser.add_argument(
        "--per-cell-target", type=int, default=DEFAULT_PER_CELL_TARGET,
        help=f"--target-matrix 셀당 목표 건수 (기본 {DEFAULT_PER_CELL_TARGET})",
    )
    parser.add_argument(
        "--zero-candidate-target", type=int, default=DEFAULT_ZERO_CANDIDATE_TARGET,
        help=(
            "실측 후보 0건인 셀(영구 0건 셀 예외)에 적용할 축소 목표 건수 "
            f"(기본 {DEFAULT_ZERO_CANDIDATE_TARGET}) — 폴백만으로 채운다."
        ),
    )
    parser.add_argument(
        "--force-full-target-for-zero-cells", action="store_true",
        help=(
            "실측 후보 0건인 셀도 --zero-candidate-target 대신 --per-cell-target "
            "그대로(전량 폴백) 채운다 — 명시적으로 요청했을 때만 켠다."
        ),
    )
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"LLM 생성 호출 동시 실행 수 (기본 {DEFAULT_CONCURRENCY}, 1이면 순차 실행과 동일)",
    )
    parser.add_argument(
        "--cell-report", default=None,
        help="--target-matrix 셀별 리포트 CSV 경로 (기본: <output>_cell_report.csv)",
    )
    args = parser.parse_args()

    run_tag = args.run_tag.strip()
    if run_tag and re.fullmatch(r"[\w.-]+", run_tag) is None:
        parser.error(f"--run-tag는 [\\w.-]+ 형식이어야 합니다: {run_tag!r}")

    def _tag_row_id(row_id: str) -> str:
        return f"{run_tag}-{row_id}" if run_tag else row_id

    repo_root = Path(__file__).parent.parent
    output_path = Path(args.output) if args.output else repo_root / "cs_pilot_output.csv"
    candidates_dir = repo_root / "data" / "candidates"
    annotated_root = repo_root / "data" / "annotated"

    clause_nos = (
        [c.strip() for c in args.clauses.split(",")]
        if args.clauses
        else [no for no, clause in CLAUSES.items() if not clause.on_hold]
    )

    resumed_row_ids: set[str] = set()
    if args.resume:
        resumed_row_ids = _existing_row_ids(output_path)
        if resumed_row_ids:
            print(f"--resume: {len(resumed_row_ids)}건 이미 처리됨, 스킵")

    write_header = not (output_path.exists() and args.resume)
    mode = "a" if (args.resume and output_path.exists()) else "w"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_ok = 0
    total_error = 0
    with output_path.open(mode, encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()

        if args.admin_status_sample_only:
            row = generate_admin_status_sample_row(sampling_seed=args.sampling_seed)
            if row["row_id"] not in resumed_row_ids:
                writer.writerow(row)
                total_ok += 1
                print(f"  [ok] {row['row_id']}: 행정 상태 부분공개 샘플")
        else:
            # OPENAI_API_KEY 확인을 DB 접속보다 먼저 — 실패할 거면 빨리 실패한다.
            from rd2.generators.generate import _default_client

            client = _default_client()
            print("Connecting to MariaDB (read-only)...")
            conn = connect_mariadb()
            db_lock = threading.Lock()  # --target-matrix/--concurrency에서 conn 공유 시에만 쓰임
            try:
                span_buckets = fetch_all_span_candidates(candidates_dir)
                fallback_samples = fetch_real_agency_date_samples(conn)

                for clause_no in clause_nos:
                    n_candidates = len(span_buckets.get(clause_no, []))
                    print(f"clause {clause_no}: span 후보 {n_candidates}건")

                rng = random.Random(args.sampling_seed)

                if args.target_matrix:
                    # 5~8호(S트랙)만 대상 — candidates.py는 그 외 조항에 span 탐지가
                    # 없다(_SPAN_CLAUSES). --clauses를 줬다면 그 교집합만 처리한다.
                    target_clauses = tuple(c for c in clause_nos if c in _SPAN_CLAUSES)
                    cells = build_target_cells(target_clauses)
                    print(f"--target-matrix: {len(cells)}개 셀 (조항 {','.join(target_clauses) or '없음'})")

                    # 참고: --run-tag는 아직 --target-matrix 경로에 연결되지 않았다
                    # (generate_cell_rows()가 자체적으로 row_id를 만듦) — 여러 EC2에서
                    # --target-matrix를 동시에 돌릴 계획이 생기면 그때 이어서 연결한다.
                    cell_buckets_by_clause = {
                        clause_no: _bucket_span_candidates_by_subclause_doc_type(
                            span_buckets.get(clause_no, []), clause_no
                        )
                        for clause_no in target_clauses
                    }

                    cell_reports: list[dict] = []
                    zero_candidate_cells = 0
                    for cell in cells:
                        bucket = cell_buckets_by_clause[cell.clause_no].get(
                            (cell.subclause_key, cell.doc_type), []
                        )
                        cell_plan = plan_cell(
                            cell, bucket,
                            per_cell_target=args.per_cell_target,
                            zero_candidate_target=args.zero_candidate_target,
                            force_full_target_for_zero_cells=args.force_full_target_for_zero_cells,
                        )
                        if cell_plan.is_zero_candidate_exception:
                            zero_candidate_cells += 1

                        rows, report_row = generate_cell_rows(
                            cell_plan, resumed_row_ids=resumed_row_ids,
                            client=client, model=args.model, sampling_seed=args.sampling_seed,
                            conn=conn, annotated_root=annotated_root, rng=rng,
                            fallback_samples=fallback_samples, concurrency=args.concurrency,
                            db_lock=db_lock,
                        )
                        for row in rows:
                            writer.writerow(row)
                            f.flush()
                            total_ok += row["status"] == "ok"
                            total_error += row["status"] != "ok"
                        cell_reports.append(report_row)
                        exception_note = (
                            "  [영구 0건 셀 예외]" if cell_plan.is_zero_candidate_exception else ""
                        )
                        print(
                            f"  [cell] {cell.cell_key}: 실측 {report_row['real_candidates']}건, "
                            f"목표 {report_row['effective_target']}건, "
                            f"span {report_row['produced_span_seeded']}+"
                            f"폴백 {report_row['produced_fallback']}건"
                            f"{exception_note}"
                        )

                    print()
                    print(
                        f"{len(cells)}개 셀 중 {zero_candidate_cells}개가 영구 0건 셀 예외 적용됨"
                    )
                    cell_report_path = (
                        Path(args.cell_report) if args.cell_report
                        else output_path.with_name(output_path.stem + "_cell_report.csv")
                    )
                    write_cell_report(cell_report_path, cell_reports)
                    print(f"셀별 리포트 -> {cell_report_path}")
                else:
                    for clause_no in clause_nos:
                        all_candidates = span_buckets.get(clause_no, [])
                        sampled_candidates = _sample_candidates(all_candidates, args.per_clause, rng)

                        produced = 0
                        for idx, candidate in enumerate(sampled_candidates):
                            row_id = _tag_row_id(f"{clause_no}-span-{idx}")
                            if row_id in resumed_row_ids:
                                produced += 1
                                continue
                            row = generate_span_seeded_row(
                                row_id, clause_no, candidate, client=client, model=args.model,
                                sampling_seed=args.sampling_seed, conn=conn, annotated_root=annotated_root,
                            )
                            if row is None:
                                continue  # 스킵된 candidate — 폴백으로 보충

                            _apply_template_validation(row)
                            writer.writerow(row)
                            f.flush()
                            total_ok += row["status"] == "ok"
                            total_error += row["status"] != "ok"
                            print(f"  [{row['status']}] {row_id}: {row['ordering_agency']}")
                            produced += 1

                        n_fallback = max(0, args.per_clause - produced)
                        if n_fallback:
                            print(
                                f"clause {clause_no}: span 근거 {produced}건 + "
                                f"폴백 {n_fallback}건(후보 부족 또는 span 탐지 미지원 조항)"
                            )

                        for fidx in range(n_fallback):
                            row_id = _tag_row_id(f"{clause_no}-fallback-{fidx}")
                            if row_id in resumed_row_ids:
                                continue
                            # 1~4호(C트랙): rd2 DB에 안보/외교/수사 계열 실수집 이력이
                            # 없어(agency_resolver.py MARKING_SPEC_AGENCY_WHITELIST
                            # 주석 참고) 화이트리스트 기반으로 생성한다(Approach D).
                            # 5~8호는 rd2 DB의 실제 (기관, 날짜) 쌍을 쓰되, 문서 건수
                            # 가중 추출 대신 기관 하나당 한 표로 균등 추출해 소수
                            # 기관(고용노동부 등) 쏠림을 완화한다(2026-07-21 사용자
                            # 결정 — agency_resolver.GENERAL_TRACK_SUPPLEMENTARY_AGENCIES
                            # 주석 참고).
                            if clause_no in MARKING_SPEC_AGENCY_WHITELIST or clause_no in ("1", "2", "3", "4"):
                                # scenario_index를 기관 선택보다 먼저 뽑아 둘 다 같은
                                # 시나리오를 가리키게 한다 — 그래야 select_whitelisted_agency가
                                # 본문과 어울리는 기관 풀로 좁힐 수 있고, 아래
                                # generate_fallback_row에도 같은 값을 넘겨 실제 생성되는
                                # 본문도 그 시나리오가 되게 한다(2026-07-21 사용자 지적:
                                # 예전엔 기관과 시나리오가 서로 무관하게 독립적으로 뽑혀
                                # "외교부가 대북 군사대비태세 문서를 쓴다" 같은 조합이 나왔다).
                                n_scenarios = len(CLAUSES[clause_no].scenario_prompts)
                                scenario_index = rng.randrange(n_scenarios) if n_scenarios else None
                                agency, logo_filename = select_whitelisted_agency(
                                    clause_no, rng, scenario_index=scenario_index
                                )
                                prod_date = synthesize_plausible_date(rng)
                                agency_source = "whitelist_synthetic"
                            else:
                                scenario_index = None
                                agency, prod_date, agency_source = sample_diverse_agency_and_date_for_fallback(
                                    rng, fallback_samples
                                )
                                logo_filename = ""
                            # 국방부/국가정보원 문서만 "대외비" 대신 군사기밀 [별표 2] 등급
                            # 마크 대상이다(2026-07-21 사용자 결정) — 등급은 여기서 한 번만
                            # 뽑아 본문 생성 프롬프트와 render_pdfs_for_csv()의 마크 선택
                            # 양쪽에 그대로 넘긴다(마크·본문 불일치 방지).
                            military_secret_grade = (
                                select_military_secret_grade(rng)
                                if is_military_secret_agency(agency)
                                else None
                            )
                            row = generate_fallback_row(
                                row_id, clause_no, client=client, model=args.model,
                                sampling_seed=args.sampling_seed,
                                ordering_agency=agency, production_date=prod_date,
                                agency_source=agency_source,
                                military_secret_grade=military_secret_grade,
                                agency_logo_filename=logo_filename,
                                scenario_index=scenario_index,
                            )
                            _apply_template_validation(row)
                            writer.writerow(row)
                            f.flush()
                            total_ok += row["status"] == "ok"
                            total_error += row["status"] != "ok"
                            print(f"  [{row['status']}] {row_id}: (fallback, {agency})")
            finally:
                conn.close()

    print()
    print(f"Done. ok={total_ok} error/empty_body/template_violation={total_error} -> {output_path}")

    if args.render_pdf:
        pdf_dir = Path(args.pdf_dir) if args.pdf_dir else repo_root / "cs_pilot_pdfs"
        print()
        print(f"Rendering PDFs (status 무관, 전체 행) -> {pdf_dir}")
        rendered = render_pdfs_for_csv(output_path, pdf_dir, sampling_seed=args.sampling_seed)
        print(f"Rendered {rendered} PDFs -> {pdf_dir}")


if __name__ == "__main__":
    main()
