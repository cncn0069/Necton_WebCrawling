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
import time
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
from rd2.generators.template_matrix import infer_subclause_key

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
]

SPAN_SEEDED_PROMPT_VERSION = "span-seeded-v1-20260720"
FALLBACK_PROMPT_VERSION = "fallback-v2-20260720"
DEFAULT_PER_CLAUSE = 8
ADMIN_STATUS_PROMPT_VERSION = "administrative-status-v1-20260716"
ADMIN_STATUS_SAMPLE_ROW_ID = "admin-status-attachment-missing-0"

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
) -> dict | None:
    """candidate(find_candidates.py 결과 1건)로 문서 원문 근거 기반 행을 만든다.

    실제 기관명을 해석하지 못하거나 annotated 원문을 찾지 못하면 None을
    반환한다 — 호출자는 그 candidate를 스킵하고 폴백으로 보충해야 한다
    (가짜 기관명으로 채우지 않는다, R3).
    """
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
    }


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
            try:
                span_buckets = fetch_all_span_candidates(candidates_dir)
                fallback_samples = fetch_real_agency_date_samples(conn)

                for clause_no in clause_nos:
                    n_candidates = len(span_buckets.get(clause_no, []))
                    print(f"clause {clause_no}: span 후보 {n_candidates}건")

                rng = random.Random(args.sampling_seed)

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
