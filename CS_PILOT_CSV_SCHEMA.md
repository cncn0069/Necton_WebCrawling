# `generate_cs_pilot.py` 출력 CSV 컬럼 정리

`scripts/generate_cs_pilot.py`가 만드는 CSV(`CSV_FIELDNAMES`, [generate_cs_pilot.py:108](scripts/generate_cs_pilot.py:108))의 42개 컬럼을 순서대로 정리한다. `--target-matrix` 없이 `--per-clause`(기본) 경로로 생성하면 마지막 3개 컬럼(40~42번)은 항상 빈 값이다.

## 시드/근거 추적 (1~8)

| 컬럼 | 역할 |
|---|---|
| `row_id` | 행 고유 ID(`{clause}-span-{n}` / `{clause}-fallback-{n}` 형태) |
| `seed_type` | 이 행이 어떻게 만들어졌는지 — `span_seeded`(실제 문서 근거) / `synthetic_fallback`(완전 폴백) / `administrative_status`(행정상태 샘플) |
| `seed_candidate_id` | span 근거가 된 candidate의 ID(`find_candidates.py` 산출물) |
| `seed_candidate_run_id` | 그 candidate가 나온 run_id — 다른 run 후보 섞임 방지 |
| `seed_line_ids` | 원문에서 근거 span의 줄 ID 목록 |
| `seed_extraction_id` | 원문 추출본(canonical v2) ID |
| `seed_text_sha256` | 근거 span 원문 해시 — 원문 변경 시 stale 판정용 |
| `seed_source_path` | 근거 span이 나온 원본 파일 경로 |

## 조항/분류 (9~11)

| 컬럼 | 역할 |
|---|---|
| `clause_no` | 정보공개법 제9조 몇 호(1~8) |
| `cso_subclause_key` | 세부조항 키(`legal_secret`, `decision_review` 등 — `content_points.py` Part B에서 쓰는 값과 동일) |
| `cso_classification` | C(기밀)/S(민감) |

## 문서 내용 (12~20)

| 컬럼 | 역할 |
|---|---|
| `title` | 문서 제목 |
| `ordering_agency` | 발주/작성 기관명(실제 DB에 존재하는 값만 사용, 가상기관 금지 — R3 규칙) |
| `department` | 담당부서 |
| `unit_task` | 단위업무명 |
| `production_date` | 생산일자 |
| `subject_category` | 주제 분류(현재는 조항 title로 채움) |
| `matched_span_text` | 왜 이 조항인지의 근거 원문 문구(`span_seeded`일 때만 값 있음) |
| `non_disclosure_reason` | 비공개 사유 설명 문구(`제N호 — 제목` 또는 행정상태 사유) |
| `body_text` | LLM이 생성한 실제 본문 |

## 공개/행정 상태 (21~23)

| 컬럼 | 역할 |
|---|---|
| `disclosure_status` | 비공개/부분공개/공개 |
| `document_status` | `doc_templates.py`의 `AdminStatus` 값(예: "첨부미등록") — 없으면 빈 문자열 |
| `release_due_date` | 공개 예정 일시(ISO 날짜). 정보공개법 제9조1항5호(의사결정 과정·내부검토)에 따라 비공개 시 공개 여부를 다시 판단할 시점을 정해야 하므로 `clause_no == "5"` 행에만 채운다 — 그 외 조항(내부검토 사유가 아닌 비공개)은 빈 문자열. PDF의 하단 공개구분 표기(`disclosure_label`)에도 "· 공개예정일 YYYY-MM-DD"로 반영된다. |

## 출처/합성 여부 (24~29)

| 컬럼 | 역할 |
|---|---|
| `source` | `synthetic-llm`(LLM 생성) / `synthetic-template`(템플릿 고정 샘플) |
| `source_url` | 원본 URL(합성 데이터라 보통 빈 값) |
| `doc_type` | 문서유형(`content_points.py` Part A/B의 그 doc_type) |
| `is_synthetic` | 항상 True(합성 데이터 트랙 표시) |
| `field_source` | 각 필드가 어디서 왔는지(원문 복사/LLM 생성 등) 기록한 JSON |
| `status` | `ok` / `llm_error` / `empty_body` / `template_violation` |

## 생성 메타데이터 (30~39)

| 컬럼 | 역할 |
|---|---|
| `model` | 사용한 LLM 모델명(기본 gpt-4o-mini) |
| `tokens_in` / `tokens_out` | 토큰 사용량(비용 추적) |
| `gen_time_s` | 생성 소요 시간(초) |
| `sampling_seed` | 후보/폴백 샘플링 고정 시드 |
| `prompt_version` | 프롬프트 버전 태그(재현성) |
| `template_id` | 적용된 `doc_templates.py` template_id(예: T5-4) |
| `template_violations` | `validate_row()`가 잡은 모순 문구 목록(비어있으면 통과) |
| `military_secret_grade` | 군사기밀 등급(대상 기관일 때만) |
| `agency_logo_filename` | 기관 레터헤드/로고 파일명 |

## `--target-matrix` 전용 (40~42)

`--per-clause`(기본) 경로에서는 항상 빈 값. `--target-matrix`로 돌릴 때만 `_apply_cell_metadata()`가 채운다([generate_cs_pilot.py:980](scripts/generate_cs_pilot.py:980)).

| 컬럼 | 역할 |
|---|---|
| `cell_key` | coverage-matrix 셀 키(조항×세부조항×문서유형×행정상태) |
| `cell_fallback_ratio` | 그 셀에서 폴백으로 채운 비율 |
| `cell_zero_candidate_exception` | 실측 후보 0건 셀이라 축소 목표(`--zero-candidate-target`)가 적용됐는지(true/false) — 설계 문서 Phase B 원칙("조용히 묻히면 안 된다")에 따라 명시적으로 남긴다 |
