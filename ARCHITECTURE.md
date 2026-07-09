# RD-2 크롤링 파이프라인 — 코드 흐름

정책연구관리시스템(PRISM)·정보공개포털 등 정부 문서 사이트를 수집해 C(기밀)/S(민감)/O(공개)
분류 학습 데이터를 만드는 파이프라인. 이 문서는 코드를 처음 보는 사람이 20분 안에
전체 흐름을 파악하도록 돕는 용도이며, 세부 구현 결정과 실사로 발견한 버그 이력은
`TODOS.md`에 있다.

## 파이프라인 한눈에

각 출처(정보공개포털=`open_go_kr.py`, PRISM=`prism.py`)는 `SourceAdapter`
(`src/rd2/adapters/base.py`) 인터페이스를 구현한 3단계로 동작한다:

```
fetch_list()  →  parse_detail()  →  to_schema()  →  Document  →  DocumentStore.upsert()
(목록 순회)      (상세페이지 조회)    (통합 스키마로 변환)  (Pydantic 검증)   (SQLite 저장)
```

- **`fetch_list()`**: 목록 페이지(또는 API)를 순회하며 각 아이템의 원시 dict를 yield한다.
- **`parse_detail(raw_item)`**: 필요 시 상세페이지를 조회해 raw_item을 보강한다(본문, 공개
  여부 근거, 첨부파일 등).
- **`to_schema(enriched_item)`**: 보강된 dict를 16개 핵심 필드를 가진 통합 `Document`
  (Pydantic 모델, `src/rd2/schema/models.py`)로 변환한다.
- **`DocumentStore`**(`src/rd2/storage/db.py`)가 `Document`를 SQLite `documents` 테이블에
  upsert한다. `dedup_key`(source+source_url) 유니크 인덱스로 중복을 막고, 스키마 검증에
  실패한 레코드는 버리지 않고 `quarantine` 테이블에 격리한다.

각 소스는 실제 사이트가 헤드리스 브라우저(`gstack browse` 서브프로세스, `src/rd2/adapters/
browse_client.py`)를 통해서만 접근 가능하다 — 정보공개포털은 순수 HTTP 클라이언트를
봇으로 차단하고, PRISM은 React SPA라 행을 클릭해야만 상세페이지 URL을 알 수 있다.

## 가장 위험한 암묵적 계약: PRISM의 브라우저 상태 공유

`prism.py`의 `fetch_list()`는 타입 시그니처(`Iterator[dict]`)만 보면 평범한 제너레이터지만,
실제로는 헤드리스 브라우저 서브프로세스 하나를 원격 조종하며 **한 페이지의 모든 행을
클릭까지 끝낸 뒤에야 yield**해야 한다. 이유: 이 제너레이터가 각 행마다 바로 yield하면,
호출부가 `parse_detail()`로 상세페이지에 이동했다가 제너레이터를 재개할 때 브라우저가
이미 목록 페이지를 벗어나 있어 다음 행 클릭이 실패한다 — `fetch_list`와 `parse_detail`이
같은 브라우저 세션(같은 `browse` 서브프로세스)을 공유하기 때문에 생기는 상호작용
버그이며, 실사 중 실제로 재현·수정된 이력이 있다(`TODOS.md` "PRISM 크롤링 안정화" 항목).

새 어댑터를 추가하거나 이 부분을 고칠 때는 이 계약(페이지 단위 버퍼링)을 깨지 않아야 한다.

## 디렉터리 구조

```
src/rd2/adapters/    출처별 어댑터 (base.py=공통 인터페이스, browse_client.py=브라우저 조작,
                     open_go_kr.py/prism.py=사이트별 구현, conformance.py=필드 완전성 검증,
                     retry.py=재시도 헬퍼)
src/rd2/schema/      Document Pydantic 모델 (16개 핵심 필드 + source/doc_type)
src/rd2/storage/     db.py=SQLite 저장/마이그레이션, files.py=본문파일 저장 규칙
                     (data/{source}/{doc_type}/{id}_{파일명}), naming.py=source/doc_type
                     영어 코드 정의(단일 진실 공급원 — 자세한 내용은 DOC_TYPES.md)
src/rd2/generators/  C/S 트랙 합성 문서 생성(LLM 기반, 실제 수집과 별개 파이프라인)
scripts/             실행 진입점 (collect_prism.py, collect_open_go_kr.py 등)
tests/               34~36개 테스트 — 대부분 실제 사이트 실사로 확인된 HTML 구조를
                     기반으로 한 mock 픽스처 사용 (아래 "알려진 한계" 참고)
```

## 체크포인트/재개

`scripts/collect_prism.py` 등 실행 스크립트는 처리한 건수를 `<db경로>.prism_checkpoint.json`
에 매 건 직후 저장한다. 장시간 크롤링 중 죽어도(sqlite 잠금, 네트워크 오류 등) 다음 실행이
같은 위치에서 이어간다 — `--skip`으로 수동 지정하거나 생략하면 체크포인트를 자동 사용한다.

## 알려진 한계 (Known Limitations)

- **`tests/test_open_go_kr_adapter.py`의 mock이 실제 사이트와 다름**: `SAMPLE_DETAIL_HTML`이
  실제로는 존재하지 않는 필드 값(`dlsrCdNm`/`nstClNm`)을 담고 있다고 이미 확인됨
  (`TODOS.md` 참고). 이 어댑터에 한해서는 테스트 통과가 실제 사이트 동작을 보증하지
  않는다 — 실사 스크립트(`scripts/check_adapter_conformance.py`)로만 신뢰 가능.
  (참고: `tests/test_prism_adapter.py`의 mock은 여러 차례 실사로 검증된 실제 HTML 구조를
  기반으로 하고 있어 같은 위험은 낮다고 판단됨, 2026-07-08 확인.)
- **원문정보(wonmun) 파일 다운로드 체인 미구현**: 정보공개포털의 부분공개 문서 실제 파일
  다운로드는 브라우저 네이티브 폼 제출 방식이라 아직 못 받아온다(`TODOS.md` P2 항목).
- **나라장터 등 향후 출처의 doc_type 판정 로직 미정**: mohw.py는 제목 키워드 기반
  판정 로직(`_infer_doc_type`)이 이미 있지만(한 출처에 여러 문서종류가 섞이는 경우
  대응), 앞으로 추가될 출처마다 같은 설계를 새로 할지, 공통화할지는 아직 결정 안 됨.

doc_type/source 전체 목록과 영어 코드 매핑, 새 코드 추가 방법은 `DOC_TYPES.md` 참고.
전체 이력과 실사 과정은 `TODOS.md`를 참고.
