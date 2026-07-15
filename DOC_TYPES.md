# doc_type 도메인 정리

`source`/`doc_type` 값은 세 곳에서 동시에 쓰인다 — `Document.doc_type` 필드(스키마),
`data/{source}/{doc_type}/` 저장 폴더명(파일시스템), `documents` 테이블의 `source`/
`doc_type` 컬럼(DB). 세 곳이 항상 같은 값을 가리켜야 하므로, 실제 값(영어 코드)은
`src/rd2/storage/naming.py` 한 곳에서만 정의한다(2026-07-09 plan-eng-review 결정 —
이전에는 한글 문자열이 어댑터마다 따로 하드코딩돼 있었다).

## source 코드

| 출처(한글) | 코드 | 정의 위치 | 비고 |
|---|---|---|---|
| — | `PRISM` | `prism.py` | 정책연구관리시스템. 원래부터 영문 표기라 변경 없음 |
| 보건복지부 | `mohw` | `mohw.py` | `scripts/collect_mohw.py` 이름과 통일 |
| 정보공개포털 | `open_go_kr` | `open_go_kr.py` | `scripts/collect_open_go_kr.py` 이름과 통일 |
| 교육부 | `moe` | `moe.py` | 재정·예산 정보 게시판 |
| ALIO | `alio` | `alio.py` | 공공기관 경영정보 공개시스템 |
| 국토교통부 | `molit` | `molit.py` | 정책정보 게시판 |
| 대한민국 정책브리핑 | `korea_kr` | `korea_kr.py` | 전 부처 보도자료 통합(open_go_kr 대체 후보로 2026-07-13 추가 — TODOS.md P0 항목 참고) |
| 기후에너지환경부 | `me` | `me.py` | 행정규칙(고시·훈령·예규), 법제처 국가법령정보센터 연계 미러 — **메타데이터 전용, 본문/첨부파일 없음**(2026-07-14 추가) |
| 고용노동부 | `moel` | `moel.py` | 행정규칙(고시·훈령·예규) — `me`와 doc_type 공유, 단 본문 전문+첨부파일 포함(2026-07-14 추가) |
| 고용노동부(정책자료실) | `moel_policy` | `moel_policy.py` | `moel`과 같은 사이트지만 다른 게시판(행정규칙 아님) — 가이드/매뉴얼/현황보고/질의회시집 등 혼재(2026-07-14 추가) |
| 나라장터 | *(미정)* | — | 어댑터 미구현. 실제 구현 시 코드명 결정 필요 — `TODOS.md` 참고 |

## doc_type 코드

| 출처 | doc_type(한글) | 코드 | 판정 방식 |
|---|---|---|---|
| PRISM | 연구보고서 | `research_report` | 고정값(이 출처는 문서유형이 하나뿐) |
| mohw | 입찰공고 | `bid_notice` | 제목 키워드 추론(최종 폴백 — 아래 우선순위 참고) |
| mohw | 사전규격공개 | `pre_spec_notice` | 제목에 "사전규격" 포함 |
| mohw | 입찰재공고 | `bid_renotice` | 제목에 "재공고" 포함 |
| mohw | 공모 | `public_offering` | 제목에 "공모" 포함 |
| mohw | 공고 | `notice` | 위 키워드 전부 불일치 + "입찰"도 없고 "공고"로도 안 끝남 |
| open_go_kr | 공문 | `official_document` | 고정값(메타데이터 전용 — 이 어댑터는 파일을 저장하지 않음) |
| korea_kr | 보도자료 | `press_release` | 고정값(이 출처는 문서유형이 하나뿐) |
| me, moel | 고시 | `notification` | 상세페이지 "행정규칙종류"/"유형" 필드값 기반 분류(두 출처가 이 세 코드를 공유) |
| me, moel | 훈령 | `directive` | 상세페이지 "행정규칙종류"/"유형" 필드값 기반 분류 |
| me, moel | 예규 | `regulation` | 상세페이지 "행정규칙종류"/"유형" 필드값 기반 분류 |
| moel_policy | 현황·통계보고 | `status_report` | 제목에 "현황"/"보고서" 포함 |
| moel_policy | 가이드·매뉴얼·지침 | `guide` | 제목에 "가이드"/"매뉴얼"/"지침"/"길잡이"/"안내서"/"사례집"/"수첩" 포함 |
| moel_policy | 질의회시집 | `interpretation_compilation` | 제목에 "질의회시집" 포함(가장 먼저 검사) |
| moel_policy | 공고/안내 | `notice`(mohw와 공유) | 위 키워드 전부 불일치 — 최종 폴백 |
| synthetic-llm | 합성문서 | `synthetic_document` | 고정값(메타데이터 전용 — LLM 생성, 실제 파일 없음) |
| 공통 | (source/doc_type이 비었거나 금지문자 제거 후 빈 문자열) | `_unclassified` | `files.py`의 폴백 버킷 — 지금까지 실제로 쓰인 적 없음 |

## mohw 문서유형 판정 우선순위

`mohw.py`의 `_infer_doc_type(title)`이 제목 키워드로 판정한다. 이 게시판은
"입찰공고 게시판"이라는 이름과 달리 사전규격공개/공모/재공고가 실제로 섞여
있어(실사 15건 샘플로 확인) 고정값을 쓸 수 없다.

```
제목에 "사전규격" 포함?     → pre_spec_notice   (가장 먼저 검사 —
                                                 "공고"로 끝나도 사전규격이 우선)
제목에 "재공고" 포함?       → bid_renotice
제목에 "공모" 포함?         → public_offering
"입찰" 포함 또는 "공고"로 끝남? → bid_notice
그 외                      → notice (최종 폴백)
```

새 키워드/유형을 추가하려면 `mohw.py`의 `_DOC_TYPE_KEYWORDS`와
`naming.py`의 `DOC_TYPE_*` 상수만 고치면 된다.

## me/moel 문서유형(고시·훈령·예규) 판정 방식

mohw처럼 제목 키워드를 추론하지 않는다 — 두 사이트 모두 상세페이지에
"행정규칙종류"(me) / "유형"(moel)이라는 **명시적 필드**로 고시/훈령/예규를
직접 알려주므로, 그 값을 그대로 `_ADMIN_RULE_TYPE_MAP`(각 어댑터 파일 안에
동일하게 정의)으로 매핑하기만 하면 된다. 키워드 추론보다 신뢰도가 높다 —
제목에 유형이 안 드러나는 문서도 많기 때문(예: "고용노동부와 그 소속기관
위임전결규정"은 제목만 봐선 훈령인지 알 수 없다).

## moel_policy 문서유형 판정 우선순위

`moel_policy.py`의 `_infer_doc_type(title)`이 제목 키워드로 판정한다(mohw와
동일한 이유 — "정책자료실"이라는 게시판 이름과 달리 실제로는 가이드/매뉴얼/
현황보고/질의회시집이 섞여 있어 고정값을 쓸 수 없다, 5페이지 50건 실사로 확인).

```
제목에 "질의회시집" 포함?                        → interpretation_compilation (가장 먼저 검사)
가이드/매뉴얼/지침/길잡이/안내서/사례집/수첩 포함? → guide
현황/보고서 포함?                                → status_report
그 외                                            → notice (최종 폴백 — mohw와 같은 코드 공유)
```

## 새 source/doc_type 추가하는 법

1. `src/rd2/storage/naming.py`에 `SOURCE_*` 또는 `DOC_TYPE_*` 상수 추가.
2. 기존 값을 한글→영어로 바꾸는 경우라면 `LEGACY_SOURCE_MAP`/`LEGACY_DOC_TYPE_MAP`에도
   추가(마이그레이션 스크립트가 이 딕셔너리를 순회함 — `scripts/rename_korean_paths.py` 참고).
3. 어댑터(`source_name` 클래스 속성, `save_body_file()` 호출부)에서 새 상수를 import해서 사용.
4. `conformance.py`의 `ADAPTER_FIELD_CONTRACTS`에 새 source 키로 계약 추가(안 하면
   `assert_conformance()`가 "계약이 없음" 에러를 낸다).
5. `tests/test_naming.py`가 모든 상수에 대해 자동으로 "영어인지"/"파일시스템 금지문자
   없는지" 검사하므로, 상수를 추가하기만 하면 별도 테스트 없이 커버된다.

## 폴더/DB에 실제로 반영하기 (기존 데이터가 있을 때)

코드에서 상수만 바꾸면 **앞으로 저장될 새 문서**부터 적용된다. 이미 디스크/DB에
쌓인 기존 데이터의 폴더명·컬럼값까지 바꾸려면 `scripts/rename_korean_paths.py`를
실행한다(일회성, idempotent, 실행 전 DB 자동 백업, 실행 후 한글 잔존 여부와 파일
실존 여부를 자체 검증). 2026-07-09에 PRISM 1,460건에 대해 이미 실행 완료
(`data/PRISM/연구보고서/` → `data/PRISM/research_report/`).
