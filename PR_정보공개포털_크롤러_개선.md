# PR: 정보공개포털 및 PRISM 크롤러 안정화

## 📋 설명
정보공개포털(open.go.kr) 및 PRISM 정책연구보고서 크롤러의 데이터 품질 향상 및 안정성 개선 작업입니다.

**주요 개선사항:**
- 정보공개포털 body_text 오염 문제 3번째 변종 발견 및 수정
- SQLite 타임아웃 문제로 인한 크래시 해결  
- PRISM 크롤러 상대경로 변환, 체크포인트 재개 기능 추가
- 페이지 전환 시 제너레이터 상태 불일치 버그 2차 수정

## 🔗 관련 이슈
- Closes #1 (정보공개포털 크롤러 개발)

## 🎯 변경사항

### 1️⃣ 정보공개포털 Body Text 오염 3번째 변종 처리
- **파일:** `src/rd2/adapters/open_go_kr.py`
- **변경:** `_NO_BODY_MARKER` → `_NO_BODY_MARKERS` (단일 문자열 → 튜플)
- **대상:** "청구신청", "열람이 불가능", "열람이 제한되어 있습니다" 세 가지 변종
- **백필:** 기존 DB에 저장된 오염 행 2건을 NULL로 초기화

### 2️⃣ SQLite 연결 타임아웃 연장
- **파일:** `src/rd2/storage/db.py`
- **변경:** `sqlite3.connect(timeout=60.0)` 추가 (기본값 5초 → 60초)
- **이유:** 8,076건 전체 크롤링 중 298건째에서 `database is locked` 크래시 방지
- **효과:** 외부 프로세스로 인한 DB 잠금 시 자동 재시도

### 3️⃣ PRISM 크롤러 안정화 (4가지 개선)
- **파일:** `src/rd2/storage/files.py`, `src/rd2/schema/models.py`, `src/rd2/adapters/prism.py`, `scripts/collect_prism.py`

**3-1) 절대경로 → 상대경로 변환**
- `save_body_file()` 반환값을 `files_root` 기준 상대경로로 변경
- DB 이식성 향상 (다른 머신/체크아웃 위치에서도 파일 경로 유효)

**3-2) 목차/초록 컬럼 추가**
- `Document` 스키마에 `table_of_contents`, `abstract` 컬럼 추가
- 목차: 비공개 문서에도 실제 값 저장
- 초록: 공개(OPEN) 문서만 저장 (비공개 시 안내문 오염 방지)

**3-3) 체크포인트 재개 기능**
- `scripts/collect_prism.py`에 `<db>.prism_checkpoint.json` 체크포인트 저장
- 크래시 후 중단된 지점부터 재시작 가능
- 검증: 298건째에서 재개 후 전체 크롤링 정상 완료

**3-4) 페이지 전환 버그 2차 수정 (근본 원인 해결)**
- **문제:** `fetch_list()` 제너레이터가 일시정지된 사이에 caller가 브라우저 상태를 변경 → 정확히 한 페이지(10건)마다 실패
- **원인:** 제너레이터가 URL 목록 수집 후 일시정지되는 동안, caller가 각 URL을 별도로 방문 → 제너레이터 재개 시 브라우저 위치가 예상과 다름
- **해결:** `fetch_prism_list_page()` 매 호출 시 목록 URL로 새로 `goto()` → 이전 브라우저 상태에 의존 제거

## 🧪 테스트 방법

```bash
# 전체 테스트 실행 (36개 통과)
pytest tests/ -v

# 특정 테스트 실행
pytest tests/test_open_go_kr_adapter.py -v  # 정보공개포털 어댑터
pytest tests/test_prism_adapter.py -v       # PRISM 어댑터
pytest tests/test_storage.py -v             # 스토리지/DB
pytest tests/test_files.py -v               # 상대경로 테스트

# 실사 검증 (PRISM 2페이지 이상 수집)
python scripts/collect_prism.py
# → 첫 페이지 통과 후 페이지 2 진입 시 정상 동작 확인
```

## ✅ 체크리스트
- [x] 코드 스타일 검토 (PEP 8 준수)
- [x] 테스트 작성/수정 완료 (36개 통과)
- [x] 기존 테스트 모두 통과
- [x] 문서 업데이트 (TODOS.md 반영)
- [x] 주석 추가 (버그 수정 부분)
- [x] 불필요한 파일 제거

## 📊 테스트 결과
```
============================== 36 passed in X.XXs ==============================
```

**새로 추가된 테스트:**
- 상대경로 변환 검증 (test_files.py)
- 목차/초록 필드 검증 (test_prism_adapter.py)
- 페이지 전환 시나리오 (실사 검증)

## 🚀 배포 영향도
- **데이터베이스 마이그레이션:** ✅ 필요 (모델 스키마 변경)
  - `table_of_contents`, `abstract` 컬럼 추가
  - 기존 데이터는 NULL로 유지
- **환경변수 추가/변경:** ❌ 없음
- **의존성 추가/업데이트:** ❌ 없음

## 📝 추가 사항

### 변경 사항 상세
- `src/rd2/schema/models.py`: `Document` 모델에 `table_of_contents`, `abstract` 필드 추가
- `src/rd2/adapters/open_go_kr.py`: 오염 마커 3번째 변종 추가
- `src/rd2/storage/db.py`: SQLite 타임아웃 60초로 설정
- `src/rd2/storage/files.py`: 절대경로 → 상대경로 변환
- `src/rd2/adapters/prism.py`: 페이지 전환 로직 개선
- `src/rd2/adapters/browse_client.py`: 브라우저 상태 의존 제거
- `scripts/collect_prism.py`: 체크포인트 재개 기능 추가

### 주의사항
- 이 PR 병합 후 PRISM 데이터베이스 초기화 필수 (상대경로 적용)
- 기존 절대경로 저장된 문서는 마이그레이션 스크립트로 변환 필요

### 향후 작업
- [ ] 정보공개포털 O트랙(공개) vs N트랙(비공개) 구분자 통일
- [ ] PRISM 첨부파일 다운로드 로직 추가
- [ ] 학습 데이터 품질 평가 지표 개발
