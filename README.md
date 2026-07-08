# RD-2: C/S/O 문서 자동 분류기 학습 데이터 구축 파이프라인

정책연구관리시스템(PRISM)·정보공개포털 등 정부 문서 사이트를 수집해 C(기밀)/S(민감)/O(공개)
분류 학습 데이터를 만드는 파이프라인. 내부 사용(RD-1 분류기 학습 파이프라인의 입력 데이터).

코드 흐름을 20분 안에 파악하려면 [`ARCHITECTURE.md`](./ARCHITECTURE.md)를 먼저 읽어라.
AWS EC2 배포 절차는 [`deploy/README.md`](./deploy/README.md)를 참고.

## 설치

Python 3.11 이상 필요 (`pyproject.toml`의 `requires-python`).

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt   # 검증된 고정 버전 (재현성 우선 — EC2 등 새 환경에서 사용)
pip install -e .
```

개발 중 최신 호환 버전으로 작업하려면 `requirements.txt` 대신 `pip install -e .`만
실행해도 된다 (`pyproject.toml`은 `>=` 범위 지정).

### gstack browse 바이너리 (별도 설치 — pip으로 설치되지 않음)

`src/rd2/adapters/browse_client.py`가 서브프로세스로 호출하는 헤드리스 브라우저 CLI.
이게 없으면 정보공개포털/PRISM 어댑터가 전혀 동작하지 않는다(`RuntimeError`로 즉시 실패).
gstack의 browse 스킬 setup 스크립트로 설치하고 `~/.claude/skills/gstack/browse/dist/browse`
(또는 `PATH`)에 위치시킬 것.

### API 키

```bash
cp .env.example .env
# .env를 열어 OPENAI_API_KEY를 채워넣는다 (.env는 git에 커밋되지 않음)
```

## 실행

```bash
python scripts/collect_prism.py --db rd2.db --count 10
```

DB 스키마는 `storage/db.py`의 `CREATE TABLE IF NOT EXISTS`로 첫 실행 시 자동 생성된다 —
별도 마이그레이션 명령이 필요 없다. `data/`와 `*.db`는 `.gitignore`로 제외되어 있으므로
clone 직후에는 빈 상태다: 위 명령을 실행하면 프로젝트 루트에 `rd2.db`가 새로 생기고,
`data/PRISM/연구보고서/`에 다운로드된 문서 파일이 쌓이기 시작한다.

체크포인트(`<db경로>.prism_checkpoint.json`)가 매 건 직후 저장되므로, 중간에 죽어도
다음 실행이 같은 위치에서 이어간다 — `--skip`으로 수동 지정하거나 생략하면 자동 사용.

## 테스트

```bash
pytest
```

## 프로젝트 구조

`ARCHITECTURE.md` 참고.
# Necton_WebCrawling
