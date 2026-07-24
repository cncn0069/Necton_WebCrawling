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
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt   # 검증된 고정 버전 (재현성 우선 — EC2 등 새 환경에서 사용)
pip install -e .
python -m playwright install chromium   # HTML 템플릿 PDF 렌더링용 브라우저
```

개발 중 최소 의존성만 설치하려면 `requirements.txt` 대신 `pip install -e .`만
실행해도 된다. HWPX 표 셀 문단 보존 로직이 파서 내부 API에 의존하므로
`hwp-hwpx-parser`는 두 설치 방식 모두 `1.0.0`으로 고정한다.

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
