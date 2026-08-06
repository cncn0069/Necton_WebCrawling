# RD-2: C/S/O 문서 자동 분류기 학습 데이터 구축 파이프라인

정책연구관리시스템(PRISM)·정보공개포털 등 정부 문서 사이트에서 공개 문서를 수집하고,
거기서 뽑은 원문을 바탕으로 C(기밀)/S(민감) 문서를 생성해 분류기 학습 데이터를 만든다.
내부 사용 — RD-1 분류기 학습 파이프라인의 입력 데이터를 만드는 것이 목적이다.

C/S는 공개된 곳에서 구할 수 없다. 그래서 이 저장소는 두 갈래다: **수집**은 O(공개)만
가져오고, C/S는 그 원문을 **변형**하거나(제5~8호) 사건 프레임에서 **생성**한다(제1~4호).
호(號)는 「공공기관의 정보공개에 관한 법률」 제9조 제1항의 비공개 세부조항을 가리킨다.

```
  수집            추출                  생성                 렌더            되쓰기
adapters/  →  extraction/       →  source_generation/  →  generators/  →  storage/
open.go.kr    PDF·HWP·XLSX         판별 → 변형/생성        HTML 템플릿      documents
PRISM 등      canonical v2 JSON    (OpenAI 호출)           → PDF           (generated_yn='1')
   ↓                ↓                     ↓                   ↓                ↓
documents 행    data/extracted/      <out-dir>/            <out-dir>/       MariaDB
data/{source}/  *.json.gz            documents/*.json      rendered/        (data_origin=O/G)
```

## 설치

Python 3.12 이상. `requirements.txt`가 고정한 `numpy==2.5.1`이 3.12+ 전용이라 그보다
낮은 버전에서는 설치 자체가 실패한다.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # 검증된 고정 버전 (EC2 등 새 환경은 이쪽)
pip install -e .                   # 스크립트가 `import rd2`를 할 수 있는 근거
python -m playwright install chromium
```

`pip install -e .`는 생략할 수 없다. 예전에는 `scripts/_common.py`가 실행 시점에
`sys.path`를 고쳐 넣었는데, 그 방식은 스크립트가 `scripts/` 바로 아래 있을 때만
동작해서 폴더를 나눌 수 없었다. 설치로 대신한다.

개발 중 최소 의존성만 필요하면 `requirements.txt` 대신 `pip install -e .`만 해도 된다.
HWPX 표 셀 문단 보존 로직이 파서 내부 API에 의존하므로 `hwp-hwpx-parser`는 두 방식
모두 `1.0.0`으로 고정한다.

### WeasyPrint 네이티브 라이브러리

PDF 렌더러는 둘이다 — 기존 공문 템플릿은 Playwright Chromium, 생성 문서 템플릿은
WeasyPrint를 쓴다. WeasyPrint는 pip으로 안 들어오는 Pango/HarfBuzz가 따로 필요하다.

| OS | 준비 |
| --- | --- |
| Windows | MSYS2 mingw64 (`libpango`, `libgobject`). 기본 탐색 경로는 `C:\tools\msys64\mingw64\bin`, 다른 곳이면 `WEASYPRINT_DLL_DIRECTORIES`에 지정 |
| Ubuntu/EC2 | `apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0` |
| macOS | `brew install pango` 후 `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH` |

확인: `python -m weasyprint --info`

코드에서는 `weasyprint`를 직접 import하지 않는다. DLL 경로와 `ssl` 확정은
`import weasyprint` **전에** 끝나야 하는데, 준비가 렌더러 한 곳에 얹혀 있으면 어느
렌더러가 먼저 import되느냐에 따라 되기도 하고 안 되기도 한다. 준비와 `HTML`을 같이
들고 있는 `rd2.generators.weasyprint_runtime`에서 가져간다 — 가져가는 행위 자체가
준비를 끝낸다 (`tests/test_layering.py`가 강제한다).

### gstack browse 바이너리 (pip으로 설치되지 않음)

`src/rd2/adapters/browse_client.py`가 서브프로세스로 호출하는 헤드리스 브라우저 CLI.
정보공개포털의 봇 탐지를 우회하는 유일한 경로라 이게 없으면 정보공개포털/PRISM
어댑터가 `RuntimeError`로 즉시 죽는다. gstack의 browse 스킬 setup 스크립트로 설치해
`~/.claude/skills/gstack/browse/dist/browse`(또는 `PATH`)에 둔다.

### MariaDB

저장 계층은 SQLite가 아니라 MariaDB를 본다. 로컬에 없다면 한 번만:

```sql
CREATE DATABASE rd2_dump CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE rd2_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'rd2'@'localhost' IDENTIFIED BY '원하는_로컬_비밀번호';
GRANT ALL PRIVILEGES ON rd2_dump.* TO 'rd2'@'localhost';
GRANT ALL PRIVILEGES ON rd2_test.* TO 'rd2'@'localhost';
FLUSH PRIVILEGES;
```

DB는 둘이다. `rd2_dump`가 실 작업 데이터(운영 덤프)이고 `.env`의 기본값이 여기를
가리킨다. `rd2_test`는 `pytest`와 검증용 실행 전용이다.

**검증용으로 수집·되쓰기를 돌릴 때는 `--database rd2_test`를 명시한다.** 생략하면
`.env` 기본값을 따라가므로 실 작업 데이터에 섞인다 — 그 착각은 산출물만 봐서는
드러나지 않는다.

스키마는 `DocumentStore` 생성자의 `CREATE TABLE IF NOT EXISTS`가 첫 실행 때 만든다.
별도 마이그레이션 명령은 없다.

### 환경변수

```bash
cp .env.example .env
```

- `OPENAI_API_KEY` — C/S 생성에 사용
- `MARIADB_HOST=127.0.0.1`, `MARIADB_PORT=3306`, `MARIADB_USER=rd2`,
  `MARIADB_PASSWORD=…`, `MARIADB_DATABASE=rd2_dump`
- `RD2_FILES_ROOT` (선택) — 첨부파일 저장 루트. 생략하면 저장소 루트의 `data/`를
  사용한다. 기존 EC2 파일을 재사용하는 수집 환경에서는 `/home/ubuntu/data`를
  지정한다.
- `RDS_MARIADB_*` — 운영 RDS. **로컬용과 이름을 나눠 둔다.** 한 벌을 돌려쓰면
  전환이 `.env` 편집으로 일어나고, 그건 되돌리는 걸 잊기 쉽다. 전환은 `--from-rds`
  플래그 하나로만 일어난다.

RDS는 퍼블릭 접근이 막혀 있어 개발 PC에서는 EC2 터널을 거친다:

```bash
ssh -i <키> -L 13306:<RDS 엔드포인트>:3306 <계정>@<EC2 호스트> -N
```

이후 `--from-rds --db-host 127.0.0.1 --db-port 13306`.

## 실행

`data/`, `output/`은 `.gitignore` 대상이라 clone 직후에는 비어 있다.

### 1. 수집

출처마다 스크립트가 하나씩 있다 (`scripts/collect/collect_*.py`).

```bash
python scripts/collect/collect_prism.py 10
```

체크포인트(`rd2.db.prism_checkpoint.json`)가 매 건 직후 저장되므로 중간에 죽어도 다음
실행이 이어간다. 파일 다운로드는 건당 수십 초까지 걸리므로, 메타데이터만 먼저 훑을
때는 `--skip-files`로 건너뛰고 `scripts/collect/backfill_prism_files.py`로 나중에 받는다.

| 스크립트 | 출처 |
| --- | --- |
| `collect_prism.py` | 정책연구관리시스템 (prism.go.kr) |
| `collect_open_go_kr.py` / `collect_orginl_info.py` | 정보공개포털 사전정보공개 / 원문정보 |
| `collect_seoul_opengov.py` | 서울 정보소통광장 결재문서 |
| `collect_alio.py` | ALIO 공공기관 경영정보 |
| `collect_korea_kr.py` | 대한민국 정책브리핑 보도자료 |
| `collect_moe.py` | 교육부 재정·예산 정보 |
| `collect_moel.py` / `collect_moel_policy.py` | 고용노동부 훈령·예규·고시 / 정책자료실 |
| `collect_mohw.py` | 보건복지부 입찰안내 |
| `collect_molit.py` | 국토교통부 정책정보 |
| `collect_me.py` | 기후에너지환경부 행정규칙 |

어댑터 필드 완전성 계약은 mock 픽스처가 아니라 **실제 사이트 샘플**로 본다 — mock은
사이트 구조와 어긋날 수 있다. 현재 걸려 있는 것은 정보공개포털·PRISM·복지부·ALIO
넷이고, 새 어댑터를 붙였다면 여기에도 같이 넣는다.

```bash
python scripts/collect/check_adapter_conformance.py
```

### 2. 추출

수집한 PDF/HWP/HWPX/XLSX를 canonical v2 `.json.gz`로 만든다. 후속 단계가 쓰는
physical line/bbox/style이 여기서 나온다.

```bash
python scripts/extract/extract_documents.py --source all
python scripts/extract/extract_documents.py --source moe --limit 10 --force
```

`extract_structured_documents.py`는 표 추출을 조사할 때만 쓰는 legacy 도구다. 운영
경로는 `data/extracted/`만 본다.

### 3. 생성

**기본값은 실호출을 하지 않는다.** `--execute` 없이 돌리면 프롬프트나 대상 목록을
보여 주는 것까지가 전부이고 과금되지 않는다.

제5~8호는 원문이 있다. 최소 프롬프트 두 개(판별기 → 생성기)로만 변형하며, 큰 프롬프트
경로(판별 → 계획 → 생성 → blind 검사)는 제거됐다. 호출은 문서당 2회다.

```bash
# 원문 하나 (로컬 추출 JSON — doc_type 라벨이 없어 명시가 필요하다)
python scripts/generate/run_minimal_generation.py \
    --source data/extracted/....json --doc-type official_document

# 배치 (DB의 공개 PDF 행을 doc_type마다 N건씩)
python scripts/generate/run_minimal_doc_type_batch.py \
    --out-dir output/minimal_doctype --per-doc-type 5 --execute
```

한 건이 배치를 끊지 않는다 — 추출 실패·계약 위반·호출 실패는 사유를 세어
`summary.json`의 `drops`에 남기고 다음 원문으로 넘어간다.

제1~4호는 원문이 없다. DB에 해당 기관 문서가 0건이라 seed를 뽑을 원문 자체가 없고,
엔트로피는 전부 사건 프레임에서 나온다. 그래서 판별기도 변형 이력도 없고 호출은
문서당 1회다. 출력 계약이 다른 두 경로가 **대조군으로 나란히** 있다.

```bash
python scripts/generate/run_c_track_generation.py --case-index 3   # 단일 출력
python scripts/generate/run_c_cot_generation.py   --case-index 3   # 3단계(분석-작성-기록)
```

### 4. 렌더

생성 계약 JSON을 문서 유형별 PDF로 만든다. 파일 하나도, 디렉터리 일괄도 된다.

```bash
python scripts/report/render_generated_documents.py output/minimal_doctype/documents \
    --output-dir output/minimal_doctype/rendered
```

되쓰기까지 이어갈 거라면 `--output-dir`를 **배치 out-dir 아래 `rendered/`로** 둔다.
5단계의 `--fill-pdf-path`가 `<out-dir>/rendered/batch_manifest.json`을 찾는다.

디렉터리 실행은 `doc_type`별 사용 가능한 템플릿을 균등 배정하고 템플릿 안에서도 구조
변주 3종을 균등하게 섞는다. 실행마다 seed를 만들어 `batch_manifest.json`에 기록하며,
재현이 필요하면 `--seed 20260730`으로 고정한다. 입력의 `failure`가 `null`이 아니면
렌더링하지 않는다(`--allow-failed-input`으로만 강제).

기관명은 입력에 있으면 그대로 보존하고 없으면 지어내지 않는다. 결재선·행정 처리
문구·합성 도장도 `document_metadata`에 있을 때만 그린다.

### 5. 되쓰기

생성물을 학습 코퍼스와 같은 `documents` 테이블에 넣는다.

```bash
# 1) 만들어질 행 확인만 (DB를 건드리지 않는다)
python scripts/generate/writeback_minimal_to_rds.py --records output/minimal_doctype

# 2) 행 삽입 — 렌더 전
python scripts/generate/writeback_minimal_to_rds.py --records output/minimal_doctype \
    --database rd2_test --execute

# 3) 렌더 (4단계) → 4) body_file_path만 UPDATE
python scripts/generate/writeback_minimal_to_rds.py --records output/minimal_doctype \
    --database rd2_test --fill-pdf-path --execute
```

`--records`는 배치 out-dir를 가리킨다 — 그 아래 `documents/`(와 `--fill-pdf-path`면
`rendered/`)를 읽는다. `--database`를 생략하면 `.env`의 `MARIADB_DATABASE`로 간다.
검증용 실행은 운영 코퍼스와 섞이지 않게 `rd2_test`를 명시한다. `body_file_path`는
`--pdf-root`(기본: 저장소 루트) 기준 상대경로로 저장된다.

**행은 PDF보다 먼저 넣는다.** 행에 들어갈 값은 전부 생성 시점에 이미 손에 있고,
렌더는 `body_file_path` 하나만 더한다. 렌더 뒤로 미루면 서식이 깨진 문서는 무엇을
만들었는지조차 DB에 남지 않는다. 렌더가 안 된 문서는 그 자체로 식별된다:

```sql
SELECT id, title FROM documents
 WHERE generated_yn = '1' AND (body_file_path IS NULL OR body_file_path = '');
```

### 그 밖의 경로

| 폴더 | 무엇 |
| --- | --- |
| `augment/` | 후보 span 탐지 → LLM 치환. `run_pipeline.py --clause 5 --limit 3`가 추출부터 이어 부르는 래퍼다. `--dry-run`이면 프롬프트만 본다 |
| `evaluate/` | `plan_cs_generation.py`(LLM 없이 coverage plan만), `explain_cs_provenance.py`(생성 한 행의 원본·span 경로를 Markdown으로 설명) |
| `report/` | 템플릿 미리보기·변주 PDF, 원문↔생성물 좌우 비교 뷰어 |
| `maintenance/` | 깨진 스텁 파일 스캔, 한글 경로 일괄 정리, SQLite→MariaDB 1회성 이관 |
| `legacy/` | 대체된 파일럿 경로. 새 작업의 출발점으로 쓰지 않는다 |

`augment/annotate_documents.py`는 제거된 단계의 호환 안내만 남은 stub이다.

## 프로젝트 구조

```
scripts/     진입점 — 인자를 읽고, 조립하고, 부른다
  collect/ extract/ generate/ report/ augment/ evaluate/ maintenance/ legacy/
src/rd2/     라이브러리
tests/       pytest
deploy/      rd2-crawler.service (EC2 systemd 유닛)
```

`scripts/`는 레이어가 아니라 **진입점**이다. DB 커넥션을 열거나 모델을 호출하는 일은
어댑터의 몫이다 — `pymysql`은 `rd2.storage`, `openai`는 `rd2.source_generation.gateway`
와 `rd2.generators.generate`, `httpx`는 `rd2.adapters`.

취향 때문에 적어 둔 규칙이 아니다. 스크립트가 각자 pymysql을 열면 안전장치도 각자
갖게 되고, 실제로 그랬다 — 세 벌의 `connect_mariadb()` 중 로컬과 운영을 나눠 본 것은
하나뿐이었고 나머지 둘은 `.env`의 `MARIADB_HOST`가 가리키는 곳이면 어디든 조용히
붙었다. 그래서 사람 대신 테스트가 본다.

`src/rd2` 안의 import는 한 방향이다. 아래 표는 설계가 아니라 현재 그래프를 받아 적은
것이고, 그래프가 표를 어기면 코드 쪽이 틀린 것이다. 같은 층끼리도 못 간다.

| 층 | 패키지 |
| --- | --- |
| 0 | `schema` `canonical` `administrative_status` |
| 1 | `storage` `disclosure` `extractors` |
| 2 | `extraction` |
| 3 | `augmentation` |
| 4 | `source_generation` |
| 5 | `generators` `adapters` |
| 6 | `audit` |

층 위반은 대개 **어휘가 행위 안에 살아서** 생긴다. `augmentation`과
`source_generation`이 조문 정의를 쓰려고 `rd2.generators.clause_data`를 import했는데
`generators`가 이미 그 둘을 import하고 있었다. 쓰는 쪽을 옮기지 말고, 두 층이 함께
보는 어휘를 아래로 내려라 — `clause_data`를 `rd2.disclosure`로 내리자 사라졌다.

## 테스트

```bash
pytest
pytest tests/test_layering.py   # 경계만 — DB도 네트워크도 필요 없다
```

저장 계층 테스트는 로컬 MariaDB의 `rd2_test`를 직접 연다. 그 DB가 없으면 해당 파일만
실패하고 나머지는 그대로 돈다.

`tests/test_layering.py`가 위 경계를 강제한다. 예외는 이유와 함께 `ALLOWED`에만 남기고,
고쳐 놓고 예외만 남지 않도록 stale 항목도 같이 검사한다.
