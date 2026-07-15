# TODOS

### ALIO director_activity(개별 비상임이사 활동내용) downstream 처리 검증 — 채택 확정 후

**What:** `AlioAdapter`에 `doc_type=DOC_TYPE_DIRECTOR_ACTIVITY`를 파라미터화해 추가하는 작업(2026-07-15 office-hours/plan-eng-review, 안정현-feat-open-go-kr-alternative-sources-design-20260715-103445.md)에서 plan-eng-review의 outside voice(Codex)가 지적했으나 이번 소량 검증(10~20건) 스코프 밖으로 명시적으로 미룬 3가지:
1. 첨부파일 확장자가 xlsx인 경우가 섞여 있음(감사결과는 PDF 위주) — downstream 추출/렌더링 흐름(`src/rd2/extractors/`, `src/rd2/generators/pdf_render.py`)이 xlsx를 실제로 처리할 수 있는지 확인 안 됨.
2. `src/rd2/generators/pdf_render.py:208`의 `_build_body_flowables()`는 `DOC_TYPE_AUDIT_RESULT`만 특수 렌더링(감사결과 전용 구조)한다 — 새 `DOC_TYPE_DIRECTOR_ACTIVITY`가 C/S 합성 파이프라인 최종 산출물에 들어간다면 렌더링 스타일 검증이 필요.
3. 본문(회차/개최일/안건내용/활동현황 표)을 `_parse_doc_detail()`의 `soup.get_text()` 방식으로 플래튼해서 넣는 게 이 콘텐츠 유형에도 의미 있는 corpus인지 불명확 — "활동내용"의 핵심 정보가 표 구조 자체에 있을 수 있어, 단순 텍스트화 시 정보 손실 우려.

**Why:** 세 항목 모두 director_activity 소스가 실제로 문민주(스펙 담당자) 확인을 거쳐 전량(3,490건) 수집으로 이어질 때만 의미가 있다 — 아직 확인 안 된 수요(Demand Evidence 참고)에 이 검증까지 먼저 하는 건 과잉 투자. 채택이 확정되면 바로 다뤄야 한다.

**Context:** `src/rd2/adapters/alio.py`, `src/rd2/generators/pdf_render.py`, `tests/test_pdf_render.py`(현재 `DOC_TYPE_AUDIT_RESULT`/`DOC_TYPE_OFFICIAL_DOCUMENT` 두 유형만 테스트).

**Effort:** S~M (xlsx 처리 확인은 S, pdf_render.py 스타일 추가는 M)
**Priority:** P2 — director_activity 전량 수집 채택 확정 시 P1로 상향
**Depends on:** director_activity 소스 채택 여부 확정(문민주 확인)

### [P0/긴급] open_go_kr 크롤링 중단 — robots.txt 전체 차단, 대체 소스 필요

**What:** `open.go.kr`(정보공개포털)의 `robots.txt`가 `Disallow: /` (루트 `/` 딱 하나만 `Allow`)로 **사이트 전체 자동 크롤링을 명시적으로 금지**하고 있음을 2026-07-13 확인(원문: `User-agent: *` / `Disallow: /` / `Allow : /$`). open_go_kr 자동 수집을 **중단**하고, 대체 소스를 찾거나 정책 확인 전까지 재개하지 않기로 결정.

**PRISM은 무혐의로 정정(2026-07-13):** 처음엔 PRISM의 `robots.txt` 요청도 WAF 차단 응답("비정상적인 활동으로 확인되어...")을 반환해 같이 의심했으나, 원인은 `curl`의 기본 User-Agent를 WAF가 걸러낸 것뿐이었다 — 정상 브라우저 User-Agent로 재요청하니 `User-agent: *` / `Disallow:` (빈 값 = 전체 허용)가 정상 반환됨. PRISM은 robots.txt 기준 문제 없고, 로컬에서 이미 수집한 6,680건도 정책상 문제 없었던 것으로 확인. PRISM 수집은 재개 가능(단, EC2에서의 "Target crashed" 렌더링 이슈는 별개로 미해결 — 로컬에서 계속 진행).

**Why:** `TODOS.md`에 이미 "EC2 상시 크롤링 전 robots.txt/이용약관/속도제한 점검"이 미완료로 남아있었는데, 오늘 EC2 배포 중 open_go_kr이 EC2 클라우드 IP를 소프트 차단(그럴듯한 "접속지연" 메시지로 위장)하는 걸 발견했고, 원인을 추적하다 robots.txt 자체가 전체 차단임을 확인했다. `수집 계획v1.1.md`(2026-07-06)에 따르면 나라장터·국가기록원·국가법령정보센터·공공데이터포털·알리오 5개 출처는 "공식 API가 있어 봇탐지 문제가 없다"는 이유로 우선순위를 앞에 뒀고, 정보공개포털은 그 목록에 없다 — 애초부터 공식 API가 없어 브라우저 자동화로 우회해왔다는 뜻. robots.txt 위반은 IP를 EC2에서 로컬로 바꾼다고 해결되지 않는 문제라 인프라 대응이 아니라 대체 소스/정책 판단이 필요하다.

**대체 소스 조사 결과(2026-07-13, WebSearch):**
- `data.go.kr`(공공데이터포털)에 "사전정보공개" API를 등록한 기관은 **국토교통부 딱 하나뿐**. open_go_kr이 제공하던 "전 기관 통합 조회"를 대체할 방법이 data.go.kr엔 없음 — 기관별로 파편화되어 있고 대부분 기관은 API 자체가 없음.
- open_go_kr 자체가 「공공기관의 정보공개에 관한 법률」상 법정 공식 창구이지만, 시민 개별 열람/청구용 웹 포털이지 대량 수집용 API가 아님. "정보공개청구"라는 공식 청구 절차는 있으나 건별 처리라 대량 학습데이터 구축에는 부적합.
- PRISM 쪽은 `행정안전부_정책연구 과제정보` API(data.go.kr, 무료/자동승인/실시간)가 유력한 후보로 발견됨 — 다만 과제명·수행기관·연구기간·연구개요 등 메타데이터만 확인됐고, RD-2가 실제 필요로 하는 공개여부·비공개 법적근거·본문파일 다운로드까지 지원하는지는 Swagger 명세서 직접 확인 필요(미완료).

**Context:** 다음 확인 필요:
1. open_go_kr을 대체할 정보공개 관련 데이터 소스 추가 조사(기관별 개별 정보공개 API, 또는 이 프로젝트의 법적 근거 확인 후 공식 청구 절차 활용 여부)
2. 이 프로젝트(RD-2)가 어떤 법적 근거·계약으로 정부 공개 데이터를 수집하고 있는지(용역/연구 목적인지, 정보공개법상 별도 예외가 있는지) — 프로젝트를 발주한 쪽에 확인 필요
3. `행정안전부_정책연구 과제정보` API의 Swagger 명세서 확인(PRISM 대체/보완 가능성)
4. 위 확인 전까지 open_go_kr 자동 수집 스크립트 실행 금지(PRISM은 재개 가능)

**Effort:** 알 수 없음(정책 확인은 코딩 작업이 아님, 대체 소스 조사는 진행 중)
**Priority:** P0 — 다른 모든 O트랙 작업(Playwright 마이그레이션 등)보다 우선
**Depends on:** 프로젝트 발주자/책임자의 법적·정책 확인, 대체 소스 추가 조사

### gstack browse → Python Playwright 네이티브 마이그레이션

**What:** `src/rd2/adapters/browse_client.py`(695줄, subprocess로 gstack `browse` CLI 호출)를 Python `playwright` 패키지로 직접 브라우저를 조종하는 방식으로 재작성.

**Why:** gstack browse는 Claude Code 대화 세션에서 대화형으로 쓰도록 설계된 도구라, SSH 세션이 끝나면 백그라운드 데몬이 같이 죽는 문제가 있었다(2026-07-13 EC2 배포 중 발견 — PRISM 목록 조회가 매번 `about:blank`로 리셋되며 0건으로 끝남). `loginctl enable-linger rd2`로 임시 우회했지만, 24시간 무인 systemd 서비스에 대화형 도구를 프로덕션 의존성으로 앉힌 근본 구조는 남아있다. Python 네이티브 Playwright를 쓰면 브라우저 컨텍스트를 크롤러 프로세스 자신이 들고 있어 이 문제가 설계상 사라진다(boring by default).

**Context:** `browse_client.py`는 PRISM(행 클릭 시뮬레이션, 페이지네이션, `_install_prism_download_click_hooks` 다운로드 인터셉트 JS 훅)과 정보공개포털/원문정보(AJAX 폴링, POST 파일 다운로드) 두 소스의 봇탐지 우회 로직을 담고 있다 — 전부 실사로 여러 날에 걸쳐 맞춘 코드라 재작성 시 전부 재검증 필요(2026-07-13 plan-eng-review에서 시간 압박 속 즉시 재작성은 보류하기로 결정, linger 우회로 진행).

**Effort:** L (695줄 재작성 + 두 어댑터 전체 재검증)
**Priority:** P2
**Depends on:** 없음 — 별도 PR로 충분한 시간을 들여 진행할 것

### Chromium `--no-sandbox` → AppArmor 프로파일로 전환

**What:** `GSTACK_CHROMIUM_NO_SANDBOX=1`(EC2 `.env`)로 Chromium 샌드박스를 꺼둔 상태 — Ubuntu 24.04의 AppArmor userns 제한 때문에 샌드박스가 즉시 크래시(`No usable sandbox!`)해서 임시로 우회한 것. 이를 AppArmor 프로파일 작성으로 대체해 샌드박스를 켠 채 운영하도록 전환.

**Why:** 방문 대상이 정보공개포털·PRISM 등 신뢰하는 정부 사이트뿐이라 당장 실제 악용 리스크는 낮지만, 보안 기본원칙(심층 방어)을 하나 포기한 채 장기 운영하는 상태다(2026-07-13 plan-eng-review에서 지금은 속도 우선으로 `--no-sandbox` 유지, TODO로 기록하기로 결정).

**Context:** Chromium 공식 문서(https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md)가 AppArmor 프로파일 작성을 권장 해법으로 안내. `deploy/rd2-crawler.service`, `/opt/rd2/.env`의 `GSTACK_CHROMIUM_NO_SANDBOX` 관련.

**Effort:** M
**Priority:** P3
**Depends on:** 위 Playwright 마이그레이션과 함께 처리하면 중복 작업 줄어듦(둘 다 Chromium 실행 방식을 건드림)

### [완료] 정보공개포털 body_text 오염 3번째 변종 발견·수정 ("열람이 제한되어 있습니다")

**What:** `_NO_BODY_MARKER`(단일 문자열 "청구신청")를 `_NO_BODY_MARKERS`(튜플: "청구신청"/"열람이 불가능"/"열람이 제한")로 확장. 기존 rd2.db에 이미 저장된 오염 행 2건(body_text에 "본 문서는 2026.07.07까지 열람이 제한되어 있습니다..." 안내문이 실제 본문처럼 들어가 있던 것)을 NULL로 백필.

**Why:** 보고용 샘플 데이터를 뽑다가 발견 — 정보공개포털 O트랙 문서 전부가 이 시간제한 안내문을 실제 본문으로 저장하고 있었다. "청구신청" 마커로는 못 잡는 세 번째 변종(오늘 이미 두 번 겪은 것과 같은 패턴: 국장급 미만 안내문, 비공개 안내문, 이번엔 열람제한기간 안내문).

**Context:** `src/rd2/adapters/open_go_kr.py` 수정, 전체 테스트 36개 통과.

**Effort:** S
**Priority:** 완료
**Depends on:** 없음

### [완료] DocumentStore sqlite 연결 타임아웃 연장 (database is locked 크래시 수정)

**What:** `storage/db.py`의 `sqlite3.connect()`에 `timeout=60.0` 추가(기본 5초).

**Why:** 8,076건 전체 크롤링 중 298건째에서 `sqlite3.OperationalError: database is locked`로 크래시. 외부 프로세스(VS Code 등)가 db 파일을 잠깐 잠그는 문제는 이 프로젝트에서 이미 한 번 겪은 적 있음(TODOS 상단 "파일 잠금 이슈" 참고) — 이번엔 짧은 트랜잭션 하나가 아니라 장시간 크롤링이라 기본 타임아웃(5초)으로는 부족했다. 타임아웃을 늘려 sqlite3가 내부적으로 재시도하며 기다리게 함.

**검증:** 체크포인트 298에서 재개, 전체 테스트 36개 통과.

**Context:** `src/rd2/storage/db.py` 수정.

**Effort:** S
**Priority:** 완료
**Depends on:** 없음

## RD-2 (학습 데이터 구축)

### [완료] PRISM 크롤링 안정화: 경로 상대화, 목차/초록 컬럼, 체크포인트 재개, 페이지 전환 버그 2차 수정

**What:** 네 가지를 한 번에 반영:
1. `storage/files.save_body_file()`가 절대경로 대신 `files_root` 기준 **상대경로**를 반환하도록 변경 — DB가 다른 머신/체크아웃 위치로 옮겨져도 안 깨짐.
2. `Document`에 `table_of_contents`(목차)·`abstract`(초록) 컬럼 추가. 목차는 비공개 문서에도 실제 값이 나와 무조건 저장, 초록은 body_text와 같은 오염 위험(비공개 시 "본 과제는 비공개 연구입니다..." 안내문)이 있어 공개(OPEN)일 때만 채움.
3. `scripts/collect_prism.py`에 체크포인트 재개 기능 추가(`<db>.prism_checkpoint.json`, 매 건 처리 직후 저장).
4. **2차 페이지 전환 버그 수정(더 근본적인 원인)**: 이전 수정(back() 후 재이동)만으로는 부족했다 — `fetch_list()`가 한 페이지의 행을 전부 클릭해 URL만 알아내고 나면, 그 URL들을 caller(`parse_detail`)가 하나씩 별도로 `goto()`한다. 이건 `fetch_list` 제너레이터가 `yield`로 일시정지된 사이에 일어나는 일이라 `fetch_prism_list_page`는 전혀 모른 채 "아직 목록 페이지에 있다"고 가정했고, 실제로는 마지막 상세페이지에 남아있어 정확히 한 페이지 분량(10건)마다 `NO_PAGE_INPUT`으로 실패했다. `fetch_prism_list_page`가 매번 목록 URL로 새로 `goto`한 뒤 필요하면 페이지 이동을 적용하도록 변경 — 이전 브라우저 상태에 의존하지 않게 됨.

**Why:** 실제 8,076건 전체 크롤링을 시작하자마자 정확히 10건째(페이지 경계)에서 두 번 연속 크래시하며 발견. 체크포인트 덕분에 재시작 시 데이터 손실은 없었지만, 근본 원인(제너레이터 일시정지 중 캐ler가 브라우저를 이동시키는 상호작용 버그)을 못 고쳤으면 전체 크롤링이 매 페이지(10건)마다 죽었을 것.

**검증:** 실사로 재현 스크립트 작성 — fetch_list의 실제 순서(페이지 내 10행 클릭 전부 완료 → 각 URL을 caller가 별도 goto)를 그대로 재현했을 때 수정 전엔 페이지 2 진입 시 실패, 수정 후엔 정상 진행 확인. 전체 테스트 36개 통과(상대경로 테스트 1개, 목차/초록 테스트 포함).

**Context:** `src/rd2/storage/files.py`, `src/rd2/schema/models.py`, `src/rd2/storage/db.py`, `src/rd2/adapters/prism.py`, `src/rd2/adapters/browse_client.py`, `scripts/collect_prism.py`, `tests/test_files.py`, `tests/test_prism_adapter.py` 수정. `rd2_prod.db`를 이 수정 반영 전 상태(10건, 절대경로)로 저장돼 있던 것 초기화 후 재시작.

**Effort:** L
**Priority:** 완료
**Depends on:** PRISM 2페이지 이상 수집 시 제목-URL 조용한 오매칭 버그 수정(완료, 이 항목이 보완)

### [완료] PRISM 2페이지 이상 수집 시 제목-URL 조용한 오매칭 버그 수정

**What:** `browse_client.fetch_prism_detail_url()`이 상세페이지에서 `back()`한 뒤, `page_number > 1`이면 `_paginate_to_prism_page()`로 원래 페이지에 재이동하도록 수정. `fetch_prism_list_page`의 페이지 이동 로직을 `_paginate_to_prism_page()`로 분리해 재사용. `prism.py`의 `fetch_list()`가 클릭 시 현재 `page` 번호를 넘기도록 수정.

**Why — 실사로 발견한 조용한 데이터 오염 버그:** PRISM 페이지네이션은 URL 변경이 없는 클라이언트 상태 전환이라 브라우저 히스토리에 새 항목이 안 남는다. 2페이지 이상에서 상세페이지로 이동한 뒤 `back()`하면 "2페이지"가 아니라 **최초 `goto()`한 1페이지로 돌아가고 있었다.** 이후 row_index 클릭은 "성공"으로 보이지만 실제로는 1페이지의(이미 수집된) 엉뚱한 행을 클릭한 것 — 그 결과 **제목은 2페이지 것인데 detail_url/파일은 1페이지의 다른 문서 것으로 섞이는 버그**였다. dedup 덕분에 지금까지는 저장까지 가지 않고 걸러졌지만(우연히 1페이지 URL과 겹쳐 dup-skip 처리됨), 만약 정말 새 DB에 수집했다면 "제목 A, 실제 내용/파일은 B"인 오염된 레코드가 조용히 저장됐을 것.

**검증:** 11~20번째 항목(2페이지)을 수정 전/후로 비교 수집 — 수정 전엔 9개 항목 전부 제목은 새 것인데 body_file_path가 1페이지의 기존 문서와 겹침(예: "낙동가람..." 제목에 "김포시 출연기관" 파일 경로). 수정 후 재실행 결과 9건 전부 각자 고유한 asmt_id/파일로 정확히 매칭, 신규 저장 성공(`rd2.db` 27건 → 36건), conformance PASS, quarantine 0건. `scripts/collect_prism.py`에 `--db`/`--count`/`--skip` 인자 추가(다음 배치 수집용), stdout UTF-8 강제 설정 추가(cp949 콘솔에서 em-dash 등 출력 시 크래시하던 문제 수정).

**Context:** `src/rd2/adapters/browse_client.py`, `src/rd2/adapters/prism.py`, `tests/test_prism_adapter.py`, `scripts/collect_prism.py` 수정.

**Effort:** M
**Priority:** 완료
**Depends on:** 없음

### [완료] PRISM 파일 다운로드 — disclosure_status 사전 필터를 "실제 클릭 검증"으로 교체

**What:** 이전 구현(`disclosure_status == "공개"`일 때만 `entire/info` API 목록 전체를 다운로드)을 폐기하고, 상세페이지의 실제 "다운로드" `<a>` 링크를 하나씩 진짜로 클릭해보는 방식으로 교체. `browse_client.py`에 `probe_and_download_prism_files(file_list)` 추가 — `window.alert`/`XMLHttpRequest`를 훅해서 각 링크 클릭이 (a) 사이트가 alert로 차단하는지, (b) 실제 네트워크 요청이 나가는지 확인하고, 차단되지 않는 파일만 실제로 받는다. `entire/info` API 목록은 파일명 등 메타데이터 조회 용도로만 쓰고, 다운로드 여부는 클릭 결과만 신뢰한다.

**Why — 이전 구현이 실제로 접근제어를 우회하고 있었다:** 이전 세션에서 "PRISM 다운로드 API는 disclosure_status와 무관하게 실제 파일을 내려준다"고 판단해 "공개 문서만" 필터링했는데, 이번에 실제 UI 클릭으로 재확인한 결과 **그 자체가 틀린 전제**였다 — 사이트는 클릭 시점에 자체적으로 `alert("비공개 연구보고서입니다.")`를 띄우고 네트워크 요청 자체를 막는다(비공개 문서 확인). 이전 구현(API를 직접 호출)은 이 클라이언트 체크를 건너뛰고 있었던 것 — "우연히 뚫린 구멍"이 아니라 의도된 접근제어의 **명백한 우회**였다. 또한 부분공개 문서는 "활용결과보고서"(연구 내용이 아니라 연구비 집행 결과 보고서, 행정 문서)만 링크가 있고 실제로 클릭 허용되며, 요약본/전체보고서는 링크 자체가 없어 애초에 접근 불가 — 즉 사이트는 "프로젝트 단위"가 아니라 "파일 단위"로 공개 여부를 관리하고 있었다.

**검증:** 실제 사이트 3건(공개/부분공개/비공개 각 1건)에 새 로직을 실행 — 공개는 기존과 동일(연구보고서+활용결과보고서 2건), 부분공개는 활용결과보고서 PDF+HWP 2건만 통과(요약본·전체보고서 제외), 비공개도 활용결과보고서 PDF+HWP 2건만 통과(실제 연구내용 보고서는 차단 확인). PDF/HWP 두 버전이 fileTypeCd/fileSn/fileWkky까지 같고 pdfTrsfYn만 달라 매칭 키에 pdfTrsfYn 추가 + id 기반 중복 다운로드 방지 추가. 기존 테스트 2개를 새 구조에 맞게 재작성하고 부분공개 부분통과 테스트 1개 추가, 전체 테스트 35개 통과.

**Context:** `src/rd2/adapters/browse_client.py`, `src/rd2/adapters/prism.py`, `tests/test_prism_adapter.py` 수정.

**참고(미반영):** 기존 `rd2.db`의 공개 6건은 구버전 로직(심의신청서까지 전부 다운로드)으로 수집된 상태라 이 새 규칙과 더 엄격히 맞지 않는 파일(심의신청서 등)이 섞여 있을 수 있음 — 사용자 확인: 이 6건은 검증 이력으로 그대로 두고, 실제 대규모 수집은 별도 신규 DB(`rd2_prod.db`)에서 새 로직으로 처음부터 진행하기로 함.

**Effort:** M
**Priority:** 완료
**Depends on:** PRISM 실제 본문파일 다운로드 구현(완료, 이 항목이 대체)

### [완료] other_file_paths 컬럼 추가 — 대표 파일 이외 나머지 파일 경로 보존

**What:** `Document` 모델에 `other_file_paths: list[str]`(기본값 빈 리스트) 추가. `documents` 테이블에 동일 이름 컬럼(TEXT, JSON 배열 문자열로 인코딩) 추가 — `storage/db.py`에 `_encode_for_sqlite()` 헬퍼를 새로 둬서 list 타입 필드를 SQLite에 바인딩 가능한 JSON 문자열로 변환(payload_json에는 원래 list 그대로 유지). `prism.py`의 `parse_detail()`이 다운로드한 파일 중 대표(`body_file_path`)를 제외한 나머지 전부를 `other_file_paths`에 채운다.

**Why:** 프로젝트당 파일이 여러 개(최대 13개까지 실측)일 때 `body_file_path`엔 대표 파일 하나만 들어가는데, 나머지 파일도 이미 로컬에 저장돼 있으니 그 경로를 DB에서도 조회 가능하게 해달라는 요청(2026-07-07).

**검증:** 기존 `rd2.db`의 PRISM 공개 6건에 대해 `other_file_paths` 백필 완료(각 2~12개, id=57은 13개 파일 중 대표 1개 제외 12개). `tests/test_prism_adapter.py`의 다운로드 테스트에 `other_file_paths` 검증 추가. 전체 테스트 34개 통과.

**Context:** `src/rd2/schema/models.py`, `src/rd2/storage/db.py`, `src/rd2/adapters/prism.py`, `tests/test_prism_adapter.py` 수정.

**Effort:** S
**Priority:** 완료
**Depends on:** PRISM 실제 본문파일 다운로드 구현(완료)

### [완료] PRISM 실제 본문파일 다운로드 구현 + cso_sub_clause 숫자 포맷 통일

**What:** `browse_client.py`에 `fetch_prism_file_list(asmt_id)`(파일 목록 조회, `POST .../v1/entire/info`)와 `fetch_prism_file_bytes(file_meta)`(파일 바이트, `POST .../v1/progress/download-file` — browse CLI가 텍스트 채널이라 브라우저 안에서 base64로 인코딩해 반환) 추가. `prism.py`의 `parse_detail()`이 **disclosure_status가 "공개"인 문서에서만** 파일 목록을 받아 전부 `save_body_file()`로 로컬 저장하고, 제목과 가장 비슷한 파일명(`difflib.SequenceMatcher`)을 `body_file_path`로 선택. 별도로, `cso_sub_clause` 포맷을 "제1호"/"제3호,제5호" 문자열에서 **숫자만("1", "3,5")**으로 통일(`prism.py`, `clause_data.py`, `generate.py`, 스키마 필드 설명, 기존 테스트, 실제 `rd2.db` 4건 모두 반영).

**Why — 중요한 안전 결정:** 실사 중 **PRISM의 다운로드 API가 disclosure_status와 무관하게 실제 파일을 그대로 내려준다**는 걸 발견했다(비공개로 표시된 실제 프로젝트에 대해서도 진짜 PDF 1.2MB가 200 OK로 응답됨, %PDF-1.4 매직넘버로 확인). RD-2는 프로젝트 초반에 "C/S 트랙은 완전히 가공된 LLM 합성 문서이지 실제 정부 기밀문서 원문을 추출·복원하는 게 아니다"라고 스코프를 명시적으로 확정한 바 있어(유일한 예외: 이미 공개 전환된 문서), 이 접근제어 구멍을 이용해 비공개 문서 원문을 수집하는 건 그 스코프를 정면으로 어기는 것 — 그래서 다운로드 자체를 공개(OPEN) 문서로만 코드 레벨에서 게이팅했다(`raw_item.get("_disclosure_text") == "공개"` 체크가 없으면 파일 목록 API 자체를 호출하지 않음). cso_sub_clause 포맷 변경은 사용자 확인: "1~4호=C, 5~8호=S" 조항 번호가 disclosure_status보다 항상 우선하는 현재 로직은 정확하다고 재확인됨(id=55 "비공개+S" 공존은 버그가 아니라 의도된 동작).

**검증:** `scripts/collect_prism.py`(신규)로 실제 PRISM 10건 재수집 — 공개 6건 전부 로컬에 파일 저장 확인(`data/PRISM/연구보고서/`, 44KB~19MB, 프로젝트당 파일 최대 10개까지 있었지만 전부 저장), 비공개/부분공개 4건은 파일 다운로드 API가 호출되지 않음을 테스트로 강제(`test_closed_item_never_calls_file_download` — 호출되면 AssertionError). 제목-파일명 유사도 매칭이 10개 후보 중에서도 실제 최종보고서를 정확히 골라냄 확인(예: "적정 경찰관기동대 보유 수 산출 연구" → "적정 경찰관기동대 연구보고서(최종).pdf", 심의신청서/계약서 등 부수 파일은 배제). 기존 6건은 dedup으로 재삽입 안 됐지만 이미 다운로드된 파일 기준으로 별도 백필 완료. `tests/test_files.py`(5개, 이전 항목에서 추가), `test_prism_adapter.py`에 2개 테스트 추가. 전체 테스트 34개 통과.

**Context:** `src/rd2/adapters/browse_client.py`, `src/rd2/adapters/prism.py`, `src/rd2/adapters/conformance.py`, `src/rd2/schema/models.py`, `src/rd2/generators/clause_data.py`, `src/rd2/generators/generate.py`, `scripts/generate_c_track_sample.py` 수정, `scripts/collect_prism.py`(신규), `tests/test_prism_adapter.py` 수정(+2), `tests/test_schema.py`/`tests/test_storage.py` 포맷 갱신.

**미구현(후속):** 원문정보(wonmun) 다운로드 체인은 별도(아래 항목) — PRISM과 달리 브라우저 네이티브 폼 다운로드라 이번 접근(브라우저 fetch+base64)을 그대로 못 씀.

**Effort:** M
**Priority:** 완료
**Depends on:** documents 테이블 source/doc_type 컬럼 재복원(완료)

### [완료] documents 테이블 source/doc_type 컬럼 재복원 + 본문파일 저장 규칙(save_body_file) 추가

**What:** `src/rd2/storage/db.py`의 `_EXTRA_COLUMNS`에 `source`/`doc_type`을 되돌리고 `_DEPRECATED_COLUMNS`에선 제거(`is_synthetic`/`source_url`은 계속 제외). `src/rd2/storage/files.py` 신규 — `save_body_file(root, source, doc_type, identifier, filename, raw_bytes) -> str`가 `root/{source}/{doc_type}/{identifier}_{filename}` 중첩 폴더에 파일을 저장하고 경로를 반환한다. `doc_type`/`source` 값이 `None`이거나 파일시스템 금지문자(`\/:*?"<>|`) 제거 후 빈 문자열이 되면 `_미분류` 폴더로 폴백. `identifier`(dedup_key 또는 documents.id)를 파일명 접두사로 붙여 같은 폴더 안 파일명 충돌을 방지.

**Why:** 2026-07-07 오전 office-hours에서 "RD-2 v1.1 핵심 16개 필드만 컬럼화"를 결정하며 `source`/`doc_type`을 컬럼에서 제거했었는데, 같은 날 오후 후속 상담에서 "본문파일을 출처별·문서종류별로 로컬 정리해야 한다"는 요구가 나오며 그 결정을 되돌렸다 — 두 필드가 단순 조회 편의가 아니라 파일 저장 경로를 결정하는 입력값이 됐기 때문. `doc_type` **값 자체**를 어떻게 판정할지(AI/규칙 기반 분류)는 이번 스코프 밖 — 지금 있는 2개 어댑터(정보공개포털=`공문` 고정, PRISM=`연구보고서` 고정)는 어댑터 전체가 단일 문서종류만 다뤄 문제 없고, 나라장터처럼 한 출처에 문서종류가 섞이는 경우가 실제로 붙을 때 별도로 설계하기로 함(아래 항목).

**검증:** 기존 `rd2.db`(정보공개포털 15 + PRISM 10 + synthetic 1 = 26건)을 실제로 열어 `_migrate_and_backfill()`이 `source`/`doc_type` 컬럼을 추가하고 payload_json에서 값을 정확히 backfill하는지 확인(null 0건, 값 육안 검증 완료 — 콘솔 출력이 깨져 보였던 건 UTF-8 파일로 재확인해 순수 터미널 코드페이지 이슈였음을 확인). `tests/test_files.py` 5개 테스트 추가(중첩 폴더 생성, None→미분류 폴백, sanitize, 금지문자만 있는 경우도 미분류 폴백, 파일명 충돌 방지). 전체 테스트 32개 통과.

**Context:** `src/rd2/storage/db.py` 수정, `src/rd2/storage/files.py`(신규), `tests/test_files.py`(신규). 설계 문서: `~/.gstack/projects/CODE/안정현-design-20260707-115203.md`. 결정 로그: 2026-07-07 "16개 필드 한정" 결정(id: 2a41a913)을 supersede.

**미구현(후속):** 실제 파일 다운로드 체인(원문정보 어댑터, 아래 "원문정보 파일 다운로드 체인 재현" 항목)이 `save_body_file()`을 호출하는 지점 — 지금은 스켈레톤 함수만 있고 실제 바이트를 가져오는 어댑터 로직은 없음. `doc_type` 판정 로직(문서종류가 섞이는 출처용 AI/규칙 기반 분류)도 별도 후속 작업.

**Effort:** S
**Priority:** 완료
**Depends on:** 없음

### [완료] PRISM(정책연구관리시스템) 어댑터 구축 — 우선순위 2위 소스

**What:** `src/rd2/adapters/prism.py` 신규 — PRISM은 React SPA라 목록 행 링크가 전부 `href="javascript:void(0)"`이고 DOM에 항목 ID가 없어(실사로 확인) 클릭해야만 상세페이지 URL을 알 수 있다. `browse_client.py`에 `fetch_prism_list_page`(페이지네이션은 URL 아니라 클라이언트 상태 — 스핀버튼+이동 버튼 JS 시뮬레이션)와 `fetch_prism_detail_url`(행 클릭 → URL 캡처 → 뒤로가기) 추가.

**핵심 성과:** PRISM 상세페이지는 **실제 법적 근거 조항(공개제한근거, 예: "5호")과 비공개사유 전문을 그대로 제공** — 정보공개포털처럼 근사 매핑(비공개→C, 부분공개→S)할 필요 없이 제1~4호→C, 제5~8호→S로 정확히 분류한다. 단, **부분공개 항목은 PRISM도 공개제한근거를 제공하지 않는다**(실사로 확인, rowheader가 "연구보고서"가 아니라 "부분공개 연구보고서"로 다름) — 이 경우만 정보공개포털과 동일하게 S로 근사한다. 한 문서가 여러 호에 동시 해당하는 경우(예: "5호 6호 7호")도 실사로 발견 — C/S 혼재 시 더 restrictive한 C를 택하도록 처리.

**실사 중 발견·수정한 버그 3개(전부 오늘과 같은 패턴 — 조용히 틀린 채로 넘어갈 뻔함):**
1. `fetch_list`가 각 행마다 바로 yield하면, 호출부의 `parse_detail()`이 상세페이지로 이동한 뒤 제너레이터를 재개할 때 브라우저가 목록을 벗어나 있어 다음 행 클릭이 실패 — 페이지 단위로 전부 클릭까지 끝낸 뒤 한꺼번에 yield하도록 수정.
2. 행 재클릭 대기 조건을 "row_index+1개 행"으로 잘못 계산 — 렌더링 중간 상태(1개만 로드된 상태)도 조건을 만족해버려 실패. 실제 전체 행수(`expected_row_count`)를 기다리도록 수정.
3. `fetch_rendered_detail_html`이 `goto()` 직후 바로 `html`을 캡처해 React의 데이터 API 호출+렌더링 완료를 안 기다림 — 같은 항목인데도 타이밍에 따라 공개제한근거가 비어 보이다 채워지다 했음. `wait --networkidle` 추가(정보공개포털 어댑터에도 안전하게 적용됨).

**검증:** 실제 사이트에서 10건 수집, quarantine 0건, conformance PASS(공개 6/비공개 3/부분공개 1, 비공개 3건 전부 실제 "제5호" 조항 확보). `tests/test_prism_adapter.py` 6개 테스트 추가(다중 조항 처리, 부분공개 근사 처리 포함). 전체 테스트 27개 통과. `rd2.db`에 실제 10건 반영.

**Context:** `src/rd2/adapters/prism.py`(신규), `src/rd2/adapters/browse_client.py` 확장, `src/rd2/adapters/conformance.py`에 PRISM 계약 추가, `scripts/check_adapter_conformance.py`에 PRISM 체크 추가, `tests/test_prism_adapter.py`(신규).

**미구현(후속):** 실제 PDF 파일 다운로드(body_file_path) — 지금은 다운로드 링크 존재 확인만 하고 파일 자체는 안 받음.

**Effort:** L
**Priority:** 완료
**Depends on:** 없음

### [완료] 어댑터별 필드 완전성 계약 + 검증 하니스 추가

**What:** `src/rd2/adapters/conformance.py`에 `ADAPTER_FIELD_CONTRACTS` 선언(어댑터별 "항상 채워져야 하는 필드" vs "이 소스엔 원래 없는 필드")과 `assert_conformance()`를 추가. `scripts/check_adapter_conformance.py`로 실제 사이트에서 수집한 샘플을 검증(mock 아님). 정보공개포털 어댑터로 실행해 15건 샘플 PASS 확인. 새 어댑터(나라장터/PRISM/국가기록원 등) 추가 시 `ADAPTER_FIELD_CONTRACTS`에 계약을 먼저 정의해야 하고, 안 하면 `assert_conformance()`가 즉시 에러를 낸다.

**Why:** "어떤 어댑터가 오더라도 16개 컬럼을 채우도록" 요청에 대한 기계적 안전장치. 8개 소스가 다 생겨도 사람이 매번 기억해서 지키는 대신 테스트가 강제한다.

**한계(중요):** 이 계약은 **필드가 비어있는지**만 검증하지, **값이 맞는지**는 검증 못한다. disclosure_status 버그(아래 P0 항목)처럼 필드에 값은 들어있지만 그 값 자체가 틀린 경우는 이 하니스로 못 잡는다 — 그건 사이트 실사로만 확인 가능. 또한 `tests/test_open_go_kr_adapter.py`의 `SAMPLE_DETAIL_HTML` mock은 dlsrCdNm/nstClNm에 실제로는 없는 가짜 값을 채워 넣고 있어 실제 사이트 구조와 다르다 — 이 conformance 검증은 mock이 아니라 실사 스크립트로만 신뢰할 수 있다.

**Context:** `src/rd2/adapters/conformance.py`(신규), `scripts/check_adapter_conformance.py`(신규), `tests/test_conformance.py`(신규, 4개 테스트), 전체 테스트 19개 통과.

**Effort:** M
**Priority:** 완료
**Depends on:** 없음

### [완료] cso_classification이 disclosure_status와 무관하게 항상 O로 고정되던 버그 수정

**What:** `open_go_kr.py`의 `to_schema()`에서 `cso_classification=CsoClassification.O` 하드코딩을 제거하고, disclosure_status로부터 파생하도록 변경: 공개→O, 비공개→C, 부분공개→S. 동시에 `_NO_BODY_MARKER`를 `"정보공개"`에서 `"청구신청"`으로 바꿔, 비공개 문서의 "열람 불가" 안내 메시지가 `body_text`에 실제 본문처럼 저장되던 2차 버그도 수정.

**Why:** disclosure_status 버그를 고치고 나니 실제로는 12/15건이 비공개, 1/15건이 부분공개였는데 cso_classification은 여전히 전부 "O"로 저장되고 있었다 — "O트랙=실제 공개문서"라는 설계 의도와 정면으로 모순(비공개 문서를 공개로 잘못 표시). 사용자가 직접 지적("cso_classification, disclosure_status 역할 중복 — cso 같은 경우 여전히 O만 있음").

**중요한 제약(구조적 한계):** 이 소스(정보공개포털 사전정보공개 정보목록) 상세페이지는 공개여부(공개/부분공개/비공개)만 제공하고, 어떤 법적 근거 조항(정보공개법 제1~8호)으로 비공개인지는 실사로 확인한 결과 이 페이지 어디에도 없다. 그래서 정확한 조항 기반 C(제1~4호)/S(제5~8호) 분류는 이 어댑터로 불가능하고, disclosure_status 심각도로 근사 매핑했다(비공개→C, 부분공개→S) — 2026-07-07 사용자 결정. 실제 조항 근거가 있는 정확한 분류는 원문정보 어댑터(TODOS.md 아래 P1 항목, `othbcSeCd=popen` 필터)의 몫으로 남겨둔다. `non_disclosure_reason`에 "구체적 법적 근거 조항은 이 소스에서 확인 불가"라고 명시해 이 근사치임을 다운스트림(RD-1)에 전달한다.

**검증:** 15건 재수집 결과 C 12건(비공개, body_text 전부 NULL 확인 — 안내 메시지 오염 없음), S 1건(부분공개), O 2건(공개, body_text 2건 다 실제 내용 있음). `tests/test_open_go_kr_adapter.py`에 비공개→C, 부분공개→S 검증 테스트 2개 추가, 전체 테스트 21개 통과.

**Context:** `src/rd2/adapters/open_go_kr.py` 수정, `tests/test_open_go_kr_adapter.py` 수정(+2 테스트).

**Effort:** M
**Priority:** 완료
**Depends on:** 없음

### [완료] O트랙 disclosure_status가 항상 "공개"로 저장되던 버그 수정

**What:** `browse_client.py`에 `fetch_rendered_detail_html()` 추가 — 상세페이지를 httpx(정적 HTML) 대신 헤드리스 브라우저로 렌더링해서 가져온다(목록 조회와 동일한 방식). `open_go_kr.py`의 `parse_detail()`이 이걸 쓰도록 변경. `to_schema()`의 `enriched_item.get("_disclosure_text") or "공개"` 안전하지 않은 폴백도 제거 — 공개여부를 확인 못하면 예외를 던져 quarantine 처리(공개로 간주하지 않음).

**Why:** `dlsrCdNm`(공개여부)은 페이지 로드 후 JS가 AJAX 응답(`openCateSearchVO.oppSeCd`)으로 채워 넣는 값이라 정적 HTML(httpx)에는 항상 빈 `<td>`로 왔다. 그 결과 실제로는 비공개/부분공개인 문서도 전부 "공개"로 저장되고 있었다 — 사용자가 직접 실사로 발견.

**검증:** 헤드리스 브라우저로 15건 재확인 결과 실제 분포는 공개 2건/부분공개 1건/비공개 12건 — 수정 전엔 15건 전부 "공개"였다. 수정 후 동일 15건 재수집 결과 정확히 이 분포로 저장됨 확인(quarantine 0건). `tests/test_open_go_kr_adapter.py`의 mock도 새 코드 경로(browse_client.fetch_rendered_detail_html)를 패치하도록 갱신, 전체 테스트 19개 통과.

**파일 잠금 이슈(해결됨):** 외부 프로세스(VS Code 파일감시자 등으로 추정)가 `rd2.db-journal`을 잠가 한동안 `rd2.db`에 쓰기/이름변경이 안 됐다. `VACUUM INTO`로 우회한 `rd2_clean.db`에 정상 데이터를 넣어뒀다가, 이후 잠금이 풀려 `rd2_clean.db` → `rd2.db` 교체 완료(cso_classification 파생 수정 반영본으로 재수집 후 교체). 최종 `rd2.db`는 C 13건(정보공개포털 12 + 기존 합성 테스트 1)/O 2건/S 1건.

**Context:** `src/rd2/adapters/browse_client.py`, `src/rd2/adapters/open_go_kr.py`, `tests/test_open_go_kr_adapter.py` 수정.

**Effort:** M
**Priority:** 완료
**Depends on:** 없음

### [완료] documents 테이블 — RD-2 v1.1 핵심 16개 필드만 컬럼화 (수집 메타데이터 컬럼 제거)

**What:** `documents` 테이블 컬럼을 RD-2 v1.1 필수 메타정보 16개 필드(title, ordering_agency, department, unit_task, production_date, disclosure_status, subject_category, content_summary, body_text, body_file_path, non_disclosure_reason, cso_classification, cso_sub_clause, performing_agency, start_date, end_date) + 저장계층 인프라(id, dedup_key, payload_json, created_at)로 한정. 이전에 추가했던 `source`/`is_synthetic`/`source_url`/`doc_type` 컬럼은 제거 — RD-2 v1.1 필수 필드 목록에 없는 수집 메타데이터이기 때문. `Document` 모델 필드로는 그대로 남고 `payload_json`에도 계속 저장되므로 데이터 손실 없음.

**Why:** 2026-07-07 office-hours에서 "이 16개 형식으로만 저장, 컬럼명은 영문" 요청 후, 이외 컬럼은 모두 제거하라는 후속 요청. 스토리지 인프라(id/dedup_key/payload_json/created_at)는 제거 대상에서 제외하기로 확인(dedup·백업 기능 유지 필요).

**Context:** `src/rd2/storage/db.py` 수정, `ALTER TABLE ... DROP COLUMN`으로 기존 `rd2.db`(16건) 마이그레이션 완료 및 데이터 무손실 확인, 전체 테스트 통과(15 passed).

**Effort:** S
**Priority:** 완료
**Depends on:** 없음

### [완료 — 위 항목으로 대체됨] documents 테이블 — 핵심 16개 필드 + source_url/doc_type 컬럼화

**(superseded)** source_url/doc_type 컬럼은 바로 위 "RD-2 v1.1 핵심 16개 필드만 컬럼화" 작업에서 다시 제거됐다. 아래는 원래 기록 보존용.

**What:** `storage/db.py`의 `documents` 테이블에 RD-2 v1.1 필수 메타정보 16개 필드(title, ordering_agency, department, unit_task, production_date, disclosure_status, subject_category, content_summary, body_text, body_file_path, non_disclosure_reason, cso_sub_clause, performing_agency, start_date, end_date) + source_url, doc_type를 실제 컬럼으로 추가. `payload_json`은 전체 백업용으로 유지. 기존 DB(`rd2.db`)는 `_migrate_and_backfill()`로 자동 마이그레이션 + 기존 6건 backfill 완료.

**Why:** 이전에는 이 필드들이 `payload_json` 안에만 있어 `WHERE department = ?`, `ORDER BY production_date` 같은 SQL 필터링/정렬이 불가능했다. Pydantic 모델 필드명이 이미 영문이므로 그대로 컬럼명으로 승격.

**Context:** `src/rd2/storage/db.py` 수정, 전체 테스트 통과(15 passed), 실제 `rd2.db`에 마이그레이션 적용 후 한글 데이터·날짜·enum 값 정상 backfill 확인. 인덱스는 이번 범위에서 제외(추후 필요시 추가).

**Effort:** S
**Priority:** 완료
**Depends on:** 없음

### [완료] is_synthetic ↔ cso_classification 강제 검증기 제거

**What:** `models.py`의 `_enforce_is_synthetic_matches_track` 검증기(O 트랙=is_synthetic False, C/S 트랙=is_synthetic True 강제)를 삭제. `is_synthetic` 필드 자체와 스키마 컬럼은 유지 — 각 수집기/생성기가 자유롭게 설정한다.

**Why:** 다음 P1 TODO(원문정보 어댑터, 부분공개 필터)는 실제로 수집한 문서(is_synthetic=False)를 C/S 조항 근거로 분류(cso_classification=C/S)해야 하는데, 기존 검증기는 이 조합을 무조건 거부했다. 2026-07-07 office-hours 세션에서 확인: "수집 단계 책임은 정직한 라벨링까지, 트랙-합성여부 일치를 강제하는 정책은 다운스트림(RD-1) 몫"이라는 스코프 경계에 따라 검증기만 제거하고 필드는 유지하기로 결정. is_synthetic은 삭제 대상이 아니라 오히려 향후(같은 C/S 분류 안에 실제 앵커 문서와 LLM 합성 문서가 공존할 때) RD-1의 분포혼선 대응에 필요한 유일한 구분 신호가 된다.

**Context:** `src/rd2/schema/models.py`, `tests/test_schema.py` 수정 완료, 전체 테스트 통과(15 passed). "원문정보(orginlInfoList) 어댑터" TODO 구현 시 이 검증기 제거가 선행되어야 했음.

**Effort:** S
**Priority:** 완료
**Depends on:** 없음


### O트랙 최상위 3개 + 후순위 4개 출처 확장

**What:** Assignment에서 최상위 4개 출처(정보공개포털·나라장터·PRISM·국가기록원) 중 1개로 먼저 어댑터를 구현하기로 했다. 나머지 최상위 3개와 후순위 4개 출처(국가법령정보센터, data.go.kr, 알리오, 국책연구기관 등)는 이후 확장 작업이다.

**Why:** O트랙 목표 20,000건을 달성하려면 8개 출처 전체가 필요하다. RD-2 v1.1은 정보공개포털·나라장터·PRISM·국가기록원을 최상위 우선순위로 명시했다.

**Context:** 설계 문서(`~/.gstack/projects/CODE/안정현-design-*.md`)의 "O트랙 출처" 표에 8개 출처별 문서종류·우선순위가 정리되어 있다. 첫 어댑터(Assignment 대상)가 검증되면, 동일한 소스 어댑터 인터페이스(fetch_list/parse_detail/to_schema)로 나머지를 확장하면 된다.

**Effort:** L
**Priority:** P1 (최상위 3개), P2 (후순위 4개)
**Depends on:** Assignment의 1번째 소스 어댑터 검증 완료

### Genalog 시각적 스캔 노이즈 증강 도입

**What:** 지금은 C/S 트랙이 텍스트 생성까지만 진행한다. 실제 스캔 문서처럼 보이는 PDF(흐림·회전·잉크번짐, 보안마크)를 만드는 Genalog 증강 단계는 후속 작업이다.

**Why:** RD-2 PDF가 명시적으로 요구하는 최종 산출물 형태이며, RD-1 분류기가 실제 스캔 문서에서도 작동해야 한다면 결국 필요하다.

**Context:** 설계 문서의 Approach D와 Constraints에 Genalog 사용 방식(HTML/CSS 템플릿, 보안마크 위치, 스캔 왜곡)이 정리되어 있다. 리뷰 중 발견된 이슈: 이미지 처리는 텍스트보다 리소스 집약적이므로 도입 시 병렬도 상한·리소스 측정이 먼저 필요하다.

**Effort:** M
**Priority:** P2
**Depends on:** C/S 텍스트 생성 파이프라인 안정화

### 분포 혼선(distributional confound) RD-1 인계 노트

**What:** O(실제 수집)와 C/S(LLM 합성)의 문체 차이 자체가 분류기 학습 신호를 오염시킬 수 있다는 리스크를, RD-1(분류기 학습) 담당자에게 명시적으로 전달한다.

**Why:** 분류기가 "기밀성"이 아니라 "이 문서를 AI가 썼는지"를 학습해버리면 RD-1 전체가 실패한다. RD-2는 문체 앵커링(few-shot 실제 문서 예시)과 AI 특유 표현 제거까지만 책임지고, 완전한 해결(도메인 적대적 학습, held-out 실제 C/S 평가셋 등)은 RD-1 스코프이므로 인계 시 누락되면 안 된다.

**Context:** plan-eng-review의 Outside Voice(Claude 서브에이전트) 리뷰에서 발견된 가장 중요한 지적. 설계 문서 Constraints의 "분포 혼선 방지" 항목 참고.

**Effort:** S (노트 작성/전달)
**Priority:** P1
**Depends on:** 없음 — 지금 바로 문서화 가능

### schema/models.py에 extra_metadata 필드 추가

**What:** 문서군(공문/계약/회의/인사/예산/연구/민원/감사/행정/보도자료)마다 다른 나머지 필드를 담을 자유 형식 JSON 필드를 Document 모델에 추가.

**Why:** 8개 문서군을 미리 다 설계하지 않고, 공통 핵심 필드(제목·본문·기관·C/S/O라벨·날짜)만 고정하기로 2026-07-06 office-hours 후속 상담에서 결정됨.

**Context:** 설계 문서 Constraints의 "문서군별 스키마 유연성" 항목 참고. storage/db.py는 이미 전체 payload_json을 저장하므로 storage 레이어 변경은 불필요, models.py만 수정.

**Effort:** S
**Priority:** P1
**Depends on:** 없음

### 원문정보(orginlInfoList) 어댑터 — 부분공개 필터, 메타데이터만

**What:** 정보공개포털의 "원문정보"(`/othicInfo/infoList/orginlInfoList.ajax`, 상세페이지 `infoListDetl.do`)를 대상으로 하는 새 어댑터(OrginlInfoAdapter). `othbcSeCd=popen`(부분공개)로 필터링해 실제 C/S 조항 근거가 있는 문서의 메타데이터(FILE_NM, ORGNAL_YN 포함)를 수집. 파일 실제 다운로드는 별도 TODO(아래).

**Why:** 이 목록은 62,713건 규모이고, 부분공개 필터링 시 실제로 특정 C/S 조항에 해당하는 진짜 문서가 나온다 — D1 하이브리드 전략(전환 문서 우선)의 핵심 재료. 순수 공개(O) 문서는 이미 기존 정보목록 어댑터로 충분하므로 원문정보는 부분공개 문서 전용으로 스코프를 좁힘.

**Context:** `안정현-수집계획수립-20260706.md` 1-B절 참고. base.py의 SourceAdapter 인터페이스를 그대로 구현하는 별도 클래스로 추가 (OpenGoKrAdapter와 병렬 구조).

**Effort:** M
**Priority:** P1
**Depends on:** schema extra_metadata 필드 추가

### 원문정보 파일 다운로드 체인 재현

**What:** `wonmunStep1 → wonmunFileFilter.ajax(PII 필터) → wonmunFileDownload.down`의 다단계 다운로드 흐름을 실제로 재현해 PDF/HWP 파일 바이트를 받아온다.

**저장 방식 정정(2026-07-07):** 원래 이 항목은 "SQLite BLOB으로 저장"이라고 적혀 있었는데, `body_file_path` 필드 자체가 "본문 파일 경로"(경로 문자열)로 설계돼 있고 BLOB 저장과 맞지 않았다. 이번에 확정된 저장 규칙(위 "documents 테이블 source/doc_type 컬럼 재복원" 항목 참고)에 맞춰, 받아온 바이트는 `storage/files.py`의 `save_body_file(root, source, doc_type, identifier, filename, raw_bytes)`로 `data/{source}/{doc_type}/` 폴더에 저장하고, 반환된 경로 문자열을 `body_file_path` 컬럼에 기록하는 것으로 정정.

**Why:** 본문파일·붙임파일이 실제로 존재하는 문서(원문정보 목록)의 파일을 받아야 완전한 O/C 데이터가 된다.

**Context:** 실사 결과 이 다운로드는 목록 조회 AJAX와 달리 브라우저 네이티브 다운로드(폼 제출)로 처리되어 페이지 내 JS로 응답 바이트를 캡처할 수 없음이 확인됨 (`plan-eng-review` 세션, 2026-07-06). httpx로 전체 체인(세션 쿠키 포함)을 직접 재현하는 방법을 검증해야 하며, 목록 조회와 동일한 봇 탐지에 막힐 위험이 있다. 필요 파라미터는 상세페이지의 `originDtlVO` 전역 객체에서 추출 가능 (실사로 확인됨). 저장 헬퍼(`save_body_file`)는 이미 구현·테스트됨 — 이 항목이 완료되면 그 헬퍼를 호출하는 자리만 채우면 된다.

**Effort:** L
**Priority:** P2
**Depends on:** 원문정보 어댑터(메타데이터만) 완료, documents 테이블 source/doc_type 컬럼 재복원(완료)

### 부분공개 문서 + 유사 공개문서 앵커링 재구성 (즉시 실행 가능, D1 최우선 소스로 승격)

**What:** (1) 원문정보에서 부분공개(`othbcSeCd=popen`) 문서 수집 — 진짜 메타데이터+비공개사유+일부 콘텐츠 확보. (2) 이미 수집된 O트랙 공개 문서 중 같은 기관·단위업무·분류체계의 가장 유사한 문서를 유사도로 매칭. (3) 그 유사 공개문서의 실제 완전한 내용을 앵커로 LLM에 "이 부분공개 문서가 완전 공개됐다면 이런 내용"을 생성시킴.

**Why:** 아래 "전환 이력 감지"와 달리 **재크롤링 이력이 쌓이길 기다릴 필요가 없다** — 부분공개 문서와 공개 문서 둘 다 지금 바로 수집 가능하므로 즉시 시작 가능. 2026-07-06 사용자 제안으로 D1 전략의 최우선 소스로 재정의됨 (기존 "유사문서 앵커링" 아이디어를 구체적 파이프라인으로 확정).

**Context:** 설계 문서 "C/S 트랙" 섹션 1번 항목 참고. 필요 인프라: (a) 원문정보 어댑터(별도 TODO), (b) 문서 간 유사도 검색(기관+단위업무+분류체계 매칭, 아직 없음 — 간단한 규칙 기반 매칭으로 시작 가능, 임베딩 기반은 과설계).

**Effort:** M (유사도 매칭은 임베딩 없이 필드 매칭으로 시작 가능해 기존 예상보다 가벼움)
**Priority:** P1
**Depends on:** 원문정보 어댑터(부분공개 필터) + O트랙 공개 문서가 어느 정도 쌓여 있을 것

### 비공개→공개 전환 이력 감지 (자체 스냅샷 비교, 장기·병행)

**What:** O트랙 재크롤링 시 동일 문서ID(PRDCTN_INSTT_REGIST_NO 등)의 공개여부 필드가 이전 수집 시점과 달라졌는지 비교해, 비공개/부분공개 → 공개로 전환된 문서를 자동 감지하는 로직.

**Why:** 위 "부분공개+유사도 앵커링"보다도 신뢰도가 높은 소스(완전한 실제 본문 확보)이지만, 8개 출처 중 어디도 전환 이력을 직접 제공하지 않아(알리오/알리오플러스 포함, 실사로 확인됨) 자체 스냅샷 비교만이 유일한 방법 — 즉시 결과가 안 나오므로 우선순위를 위 항목보다 낮춤.

**Context:** `안정현-수집계획수립-20260706.md` 종합 우선순위 섹션 참고. 크롤러를 먼저 지속적으로 가동해야 이력이 쌓이므로, 위 P1 항목과 별도로 지금부터 크롤링만 켜두면 병행 축적됨.

**Effort:** M
**Priority:** P2
**Depends on:** 원문정보/정보목록 어댑터가 안정적으로 반복 실행되고 있을 것

### 나머지 O트랙 출처 실사 상세 조사

**What:** 나라장터·PRISM·국가기록원의 실제 API 응답 스키마, 국가기록원의 일일 1,000건 한도 영향, 국책연구기관 개별 사이트 구조를 정보공개포털 수준으로 깊이 조사.

**Why:** `안정현-수집계획수립-20260706.md`는 WebSearch 기반 표면 조사만 마쳤다 — 실제 어댑터 구현 전에는 각 사이트의 실제 요청/응답 구조 확인이 필요하다 (정보공개포털도 실사 전엔 몰랐던 봇탐지·JS렌더링 문제가 있었음).

**Context:** `안정현-수집계획수립-20260706.md`의 "다음 확인 필요 사항" 절 참고.

**Effort:** M (사이트당 시간 소요)
**Priority:** P2
**Depends on:** 없음, 병렬 진행 가능

### EC2 상시 크롤링 전 robots.txt/이용약관/속도제한 점검

**What:** PRISM·정보공개포털 등 각 출처의 robots.txt와 이용약관을 실제로 확인하고, 요청 간격/레이트리미팅 정책을 크롤러에 반영한다.

**Why:** 지금까지는 로컬 PC에서 사람이 지켜보며 간헐적으로 실행했지만, AWS EC2에서 상시 실행으로 전환하면 클라우드 IP 대역에서 지속적으로 정부 사이트에 접근하게 되어 봇 차단·법적/정책 리스크가 로컬 수동 실행 때보다 커진다. `안정현-design-20260708-092004.md`(RD-2 GitHub 업로드 준비) plan-eng-review에서 Codex outside voice가 지적.

**Context:** 이번 GitHub 업로드 준비 세션의 스코프 밖 — AWS EC2 계정이 발급되어 실제로 상시 실행을 구성할 때 반드시 선행되어야 한다. `docs/future-ec2-runbook.md`(향후 작성 예정)에도 이 항목을 명시적으로 포함시킬 것.

**Effort:** S (사이트 2곳 확인 + 크롤러 요청 간격 설정)
**Priority:** P2
**Depends on:** AWS EC2 계정 발급

### 런타임 파일(DB/로그/checkpoint) `runtime/` 디렉토리로 정리

**What:** `rb.db`, `rd2.db`, `rd2_prod.db`, `*.log`, `*.prism_checkpoint.json`이 현재 저장소 루트에 흩어져 있는 것을 `runtime/` 같은 전용 디렉토리로 모으고 코드의 경로 참조를 수정한다.

**Why:** `.gitignore` 보강으로 이 파일들이 git에 커밋되는 실수는 막았지만(2026-07-08 GitHub 업로드 준비 작업), 루트에 흩어진 구조 자체는 고쳐지지 않았다 — 새 런타임 파일이 생길 때마다 `.gitignore`에 패턴을 추가로 챙겨야 하는 위험이 계속 남는다. `안정현-design-20260708-092004.md` plan-eng-review에서 Codex outside voice가 지적.

**Context:** GitHub 업로드 준비 세션 스코프 밖으로 명시적으로 미룬 리팩터링. 경로 참조가 있는 파일: `src/rd2/storage/db.py`(DB 경로), `scripts/collect_prism.py` 등 실행 스크립트(체크포인트 경로), `ARCHITECTURE.md`의 디렉터리 구조 설명도 함께 갱신 필요.

**Effort:** M (경로 참조 여러 곳 수정 + 기존 로컬 DB/체크포인트 이동)
**Priority:** P3
**Depends on:** 없음, 다만 GitHub 업로드 이후 진행 권장(업로드 전에 하면 오늘 작업과 충돌 위험)

### browse_client.py 전역변수 `_NAVIGATED`를 인스턴스 변수로 이동

**What:** `_ensure_navigated()`가 참조하는 모듈레벨 전역 `_NAVIGATED` 플래그를 `OpenGoKrAdapter` 인스턴스 변수로 옮긴다.

**Why:** 지금은 스크립트가 어댑터 하나만 프로세스당 실행해서 문제가 안 되지만, 한 프로세스에서 여러 어댑터(정보공개포털+PRISM 등)를 같이 돌리는 스크립트가 생기면 이 전역 상태가 실제로는 "다른 어댑터가 브라우저를 이동시켰는데도 목록 페이지에 있다고 착각"하는 조용한 버그로 이어질 수 있다(2026-07-08 plan-eng-review 지적).

**Context:** `src/rd2/adapters/browse_client.py:53` 부근. PRISM 쪽은 이미 매번 목록 URL로 재-goto하는 방식으로 이 문제를 피해가고 있다(`fetch_prism_list_page` 참고) — 같은 패턴을 정보공개포털에도 적용하거나, 최소한 전역 대신 인스턴스 상태로 캡슐화한다.

**Effort:** S
**Priority:** P3
**Depends on:** 없음

### generate.py — OPENAI_API_KEY 누락 시 명확한 에러 메시지

**What:** `os.environ["OPENAI_API_KEY"]` 직접 인덱싱을 `os.environ.get("OPENAI_API_KEY")` + 명시적 `RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다...")`로 교체.

**Why:** 지금은 키가 없으면 `KeyError: 'OPENAI_API_KEY'`라는 맥락 없는 에러만 뜬다. 본인만 쓰는 로컬 스크립트라 우선순위는 낮지만, 나중에 다른 사람이 같은 스크립트를 돌릴 때 원인 파악 시간을 아낀다(2026-07-08 plan-eng-review 지적).

**Context:** `src/rd2/generators/generate.py:59`.

**Effort:** S
**Priority:** P3
**Depends on:** 없음

### 합성 C/S 문서 시나리오 다양성 부족 — 25개 시나리오로 31,000건 목표

**What:** `clause_data.py`의 조항별 `scenario_prompts`(현재 조항 8개 합쳐 총 25개)를 늘리거나, LLM이 매 생성마다 시나리오 자체를 변형(장소·산업·구체적 상황 치환 등)하도록 프롬프트를 개선한다.

**Why:** C(16,000건)+S(15,000건) 총 31,000건 목표 대비 시나리오 25개는 조항당 재사용률이 극단적으로 높다(2026-07-08 plan-eng-review의 Claude 서브에이전트 outside voice 발견). LLM 생성 자체는 매번 다른 문장을 만들어내지만, 바탕이 되는 "상황" 자체가 25가지뿐이면 분류기가 진짜 기밀성 신호가 아니라 이 25개 템플릿의 표면적 패턴을 학습할 위험이 있다 — 기존 TODOS의 "분포 혼선(O vs C/S 문체 차이)" 항목보다 더 구체적인, C/S 트랙 **내부** 다양성 문제.

**Context:** `src/rd2/generators/clause_data.py`(시나리오 정의), `src/rd2/generators/generate.py`(생성 로직). RD-1(분류기 학습) 인계 시 이 리스크도 "분포 혼선 RD-1 인계 노트" 항목과 함께 명시적으로 전달해야 한다.

**Effort:** M (시나리오 작성은 사람 검토가 필요한 콘텐츠 작업 — 조항당 몇 개를 몇 개로 늘릴지부터 결정 필요)
**Priority:** P2
**Depends on:** 없음, "분포 혼선 RD-1 인계 노트" 항목과 함께 다루는 게 자연스러움

### with_retry — 밴/타임아웃 구분 없는 재시도(안티블로킹 전략 부재)

**What:** `with_retry`가 지금은 모든 `RETRYABLE_EXCEPTIONS`(RuntimeError 등)를 동일하게 취급해 재시도한다. 사이트가 실제로 접근을 차단(예: 지속적인 403, CAPTCHA, IP 차단)한 경우와 일시적 타임아웃을 구분해, 차단 상황에서는 더 긴 백오프나 알림/중단을 하도록 정책을 분리한다.

**Why:** 대규모(수만 건) 상시 크롤링(EC2 배포 예정)에서 실제로 차단이 발생하면 지금 로직은 그걸 일반 타임아웃과 똑같이 짧게 재시도만 반복해, 차단 상태를 눈치채지 못한 채 계속 실패할 위험이 있다(2026-07-08 plan-eng-review의 Claude 서브에이전트 outside voice 발견). 아직 실제로 차단을 겪은 이력은 없다 — 실제 문제가 되면 그때 대응해도 되는 성격.

**Context:** `src/rd2/adapters/retry.py`. "EC2 상시 크롤링 전 robots.txt/이용약관/속도제한 점검" 항목과 밀접하게 연관 — 그 점검에서 요청 간격을 지키면 애초에 차단당할 가능성 자체가 줄어든다.

**Effort:** M (차단 신호를 어떻게 감지할지부터 설계 필요 — browse CLI가 HTTP 상태 코드를 노출하는지 확인부터)
**Priority:** P3
**Depends on:** "EC2 상시 크롤링 전 robots.txt/이용약관/속도제한 점검" 항목 이후 실제로 필요한지 재평가

### 나라장터(G2B) 어댑터용 영어 source 코드 미리 결정

**What:** 나라장터 어댑터가 실제로 구현될 때 쓸 영어 source 코드(예: `g2b`)를 `src/rd2/storage/naming.py`에 미리 정의해둔다.

**Why:** 2026-07-09 plan-eng-review에서 data/ 폴더명·doc_type·DB path를 한글→영어로 정리하며 PRISM/mohw/open_go_kr의 네이밍은 확정했지만, 나라장터는 아직 어댑터 자체가 없어(테스트 픽스처의 placeholder일 뿐) 이번 범위에서 제외했다. 나중에 어댑터를 만들 때 이 결정을 다시 논의하지 않도록 미리 정해두면 좋다.

**Context:** `src/rd2/storage/naming.py`(2026-07-09 신설 예정). "O트랙 최상위 3개 + 후순위 4개 출처 확장" 항목(위) 참고 — 나라장터는 최상위 3개 중 하나.

**Effort:** S
**Priority:** P3
**Depends on:** 나라장터 어댑터 구현 착수 시점

### conformance에 "한글 리터럴 재유입 방지" 가드 추가

**What:** `ADAPTER_FIELD_CONTRACTS`나 별도 검증 스크립트에, source/doc_type 값으로 한글 문자열이 들어오면 실패하는 가드를 추가한다(예: 정규식으로 한글 유니코드 범위 검사).

**Why:** 2026-07-09 plan-eng-review에서 data/ 폴더명·doc_type을 한글→영어로 정리하고 `src/rd2/storage/naming.py`로 중앙화했지만, 강제력 없이는 나중에 새 소스/doc_type을 추가하는 사람이 다시 한글 리터럴을 쓸 수 있다 — 그러면 이번 리팩토링이 다시 필요해지는 상황이 재발한다.

**Context:** `src/rd2/adapters/conformance.py`, `src/rd2/storage/naming.py`(2026-07-09 신설 예정).

**Effort:** S
**Priority:** P3
**Depends on:** naming.py 신설(위 리팩토링) 완료
