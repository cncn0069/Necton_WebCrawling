# RD-2: C/S/O 문서 자동 분류기 학습 데이터 구축 파이프라인

정책연구관리시스템(PRISM)·정보공개포털 등 정부 문서 사이트를 수집해 C(기밀)/S(민감)/O(공개)
분류 학습 데이터를 만드는 파이프라인. 내부 사용(RD-1 분류기 학습 파이프라인의 입력 데이터).

코드 흐름을 20분 안에 파악하려면 [`ARCHITECTURE.md`](./ARCHITECTURE.md)를 먼저 읽어라.
AWS EC2 배포 절차는 [`deploy/README.md`](./deploy/README.md)를 참고.
제9조 1~8호별 C/S 문서 템플릿의 적용 범위와 생성·검수 기준은
[`TEMPLATE_CHECKLIST.md`](./TEMPLATE_CHECKLIST.md)를 참고.
전용 템플릿별 검수 PDF와 실제 원본 참조 문서는
[`TEMPLATE_SOURCE_MAP.md`](./TEMPLATE_SOURCE_MAP.md)에 기록한다.

## 설치

Python 3.12 이상 필요 (`pyproject.toml`의 `requires-python`). `requirements.txt`가
고정한 `numpy==2.5.1`이 3.12+ 전용이라, 이보다 낮은 버전에서는 설치 자체가 실패한다.

```bash
# macOS 최초 1회 — WeasyPrint 네이티브 라이브러리
brew install pango
# Apple Silicon에서 Homebrew 라이브러리를 못 찾을 때 필요한 공식 권장 경로
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH

python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt   # 검증된 고정 버전 (재현성 우선 — EC2 등 새 환경에서 사용)
pip install -e .
python -m playwright install chromium   # HTML 템플릿 PDF 렌더링용 브라우저
python -m weasyprint --info             # WeasyPrint + Pango 로딩 확인
```

기존 공공문서 템플릿 PDF는 Playwright Chromium을 계속 사용한다. 새 문서 렌더링
실험을 위해 Jinja2와 WeasyPrint 69.x를 함께 설치하며, 둘은 기존 렌더러를
자동으로 대체하지 않는다. Ubuntu/EC2의 Pango 설치 패키지는
[`deploy/README.md`](./deploy/README.md)를 따른다. macOS에서
`cannot load library 'libgobject-2.0-0'`가 나오면 위
`DYLD_FALLBACK_LIBRARY_PATH`가 현재 셸에 설정됐는지 확인한다
([WeasyPrint 공식 문제 해결 문서](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#missing-library)).

개발 중 최소 의존성만 설치하려면 `requirements.txt` 대신 `pip install -e .`만
실행해도 된다. HWPX 표 셀 문단 보존 로직이 파서 내부 API에 의존하므로
`hwp-hwpx-parser`는 두 설치 방식 모두 `1.0.0`으로 고정한다.

### 구조화 생성 문서 렌더링

`result.generated_document`에 `paragraph`, `key_value`, `bullet_list`,
`table` blocks가 들어 있는 계약 JSON은 공문 템플릿 10종으로 바로 렌더링할 수 있다.
JSON 배열과 JSONL 배치 입력도 지원한다.

단일 파일과 디렉터리 일괄 실행, 입력 규칙, 템플릿 선택 방법은
[`docs/generated-document-pdf-pipeline.md`](./docs/generated-document-pdf-pipeline.md)에
정리했다.

```bash
python scripts/render_generated_documents.py input.json \
  --output-dir output/pdf/generated_documents \
  --per-template 3
```

기본값은 10종 전체이며 `--template 01_classic_municipal`처럼 일부 템플릿만
반복 지정할 수 있다. 입력의 `failure`가 `null`이 아니면 렌더링하지 않는다.
실패 결과를 조사 목적으로 출력할 때만 `--allow-failed-input`을 명시한다.

파이프라인은 `blocks`를 내용의 기준으로 사용하고, `body_text`가 함께 있으면
blocks를 평탄화한 결과와 같은지 먼저 검사한다. 두 값이 다르거나 PDF에서 원문
문장·키·값·표 셀이 하나라도 누락되면 해당 출력을 거부한다. 각 문서 폴더의
`manifest.json`에는 입력 해시, 요청/응답 ID, 변주 seed와 PDF 경로가 기록된다.

`generated_document.agency_name`이 있으면 입력 기관명을 그대로 보존하고 실제
로고를 추측하지 않는다. 기관명이 없으면 경찰서·소방서처럼 본문과 의미 충돌이
생길 수 있는 기관을 전체 300개 풀에서 무작위로 고르지 않고, 범용 공공기관
25개 하위 풀에서만 seed 기반으로 선택한다. 한 문서의 여러 레이아웃은 같은
기관명을 유지하며 템플릿은 기관 선택에 영향을 주지 않는다.
`manifest.json`의 `identity.agency_pool_index`, `organization_category`,
`selection_category`, `agency_seed`로 분포를 감사할 수 있다.

결재선과 행정 처리 문구는 입력에 있을 때만 렌더링한다. 다음처럼
`generated_document.document_metadata`에 명시하며, `pending` 슬롯에는
`stamp`나 `approved_at`을 넣을 수 없다.

```json
{
  "document_metadata": {
    "approval_line": {
      "slots": [
        {
          "role": "담당",
          "name": "김가온",
          "status": "approved",
          "approved_at": "2025-01-07",
          "stamp": {
            "mode": "synthetic",
            "stamp_text": "김가온인",
            "seed": 10000,
            "profile": "dry_ink",
            "shape": "square"
          }
        },
        {
          "role": "기관장",
          "name": null,
          "status": "pending",
          "approved_at": null,
          "stamp": null
        }
      ]
    },
    "administrative_events": [
      {
        "type": "review_deadline",
        "date": "2025-01-15",
        "text": "2025년 1월 15일까지 심사할 예정입니다."
      }
    ]
  }
}
```

합성 도장은 실제 기관 이미지를 사용하지 않고 `stamp_text`만으로 새로 그린다.
`profile`은 `normal`, `light_ink`, `uneven_pressure`, `damaged`,
`dry_ink`, `wet_blur`, `shape`은 `round`, `square`, `oval` 중 하나다.
`profile`, `shape`, `seed`를 생략하면 문서 seed로 재현 가능한 값을 고른다.
짧은 결재자명은 원형·사각형, 긴 기관명은 원형·타원형을 우선한다. 날인 위치는
일반 55%, 경계선 걸침 25%, 이름/일자 부분 겹침 20%로 선택되며 실제 선택값은
`manifest.json`의 `approval[].stamp.parameters`와 `placement`에 기록된다.

10종 템플릿은 실제 공문 예시처럼 흑백·회색 중심의 인쇄 톤을 공유한다. 색상
테마 대신 여백, 구획선, 제목 정렬, 표와 본문 배치로 레이아웃을 변주한다.

### gstack browse 바이너리 (별도 설치 — pip으로 설치되지 않음)

`src/rd2/adapters/browse_client.py`가 서브프로세스로 호출하는 헤드리스 브라우저 CLI.
이게 없으면 정보공개포털/PRISM 어댑터가 전혀 동작하지 않는다(`RuntimeError`로 즉시 실패).
gstack의 browse 스킬 setup 스크립트로 설치하고 `~/.claude/skills/gstack/browse/dist/browse`
(또는 `PATH`)에 위치시킬 것.

### 로컬 MariaDB 설치 (2026-07-09 SQLite → MariaDB 전환)

저장 계층이 SQLite 파일이 아니라 MariaDB를 본다(`src/rd2/storage/db.py`). 로컬에
없다면 한 번만 설치:

```bash
brew install mariadb
brew services start mariadb

mysql -u $(whoami) <<'SQL'
CREATE DATABASE rd2_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE rd2_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'rd2'@'localhost' IDENTIFIED BY '원하는_로컬_비밀번호';
GRANT ALL PRIVILEGES ON rd2_dev.* TO 'rd2'@'localhost';
GRANT ALL PRIVILEGES ON rd2_test.* TO 'rd2'@'localhost';
FLUSH PRIVILEGES;
SQL
```

`rd2_test`는 `pytest`가 쓰는 별도 DB — 실제 수집 데이터(`rd2_dev`)와 섞이지 않게 분리했다.

VSCode에서 데이터를 직접 보고 싶으면 "Database Client"(게시자 `cweijan`) 익스텐션을
설치해 host=`localhost`, port=`3306`, user=`rd2`, database=`rd2_dev`로 연결하면 된다.

### 환경변수 (.env)

```bash
cp .env.example .env
```

`.env`를 열어 채워넣는다 (`.env`는 git에 커밋되지 않음):
- `OPENAI_API_KEY` — C/S 트랙 합성 문서 생성에 사용
- `MARIADB_HOST=localhost`, `MARIADB_PORT=3306`, `MARIADB_USER=rd2`,
  `MARIADB_PASSWORD=`(위에서 만든 비밀번호), `MARIADB_DATABASE=rd2_dev`

## 실행

```bash
python scripts/collect_prism.py 10
```

DB 스키마는 `storage/db.py`의 `CREATE TABLE IF NOT EXISTS`로 첫 실행 시 MariaDB에
자동 생성된다 — 별도 마이그레이션 명령이 필요 없다. `data/`는 `.gitignore`로
제외되어 있으므로 clone 직후에는 빈 상태다: 위 명령을 실행하면
`data/PRISM/research_report/`에 다운로드된 문서 파일이 쌓이기 시작한다.

체크포인트(`rd2.db.prism_checkpoint.json`, 고정 파일명)가 매 건 직후 저장되므로,
중간에 죽어도 다음 실행이 같은 위치에서 이어간다 — `--skip`으로 수동 지정하거나
생략하면 자동 사용.

**이전에 로컬 SQLite(`rd2.db`)로 이미 모아둔 데이터가 있다면**, 위 MariaDB 설치를
끝낸 뒤 아래 한 번만 실행해서 그대로 옮겨온다(원본 `rd2.db`는 읽기 전용으로만 열어
건드리지 않으므로 재실행해도 안전):

```bash
python scripts/migrate_sqlite_to_mariadb.py
```

## 테스트

```bash
pytest
```

## 프로젝트 구조

`ARCHITECTURE.md` 참고.
# Necton_WebCrawling
