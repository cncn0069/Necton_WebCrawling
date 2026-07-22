# find_candidates.py — v2 후보 탐지

`data/extracted/**/*.json.gz`의 canonical extraction v2를 한 번씩 읽어 정보공개법
제9조 5~8호와 행정상태 후보를 생성한다. 별도의 `data/annotated/` 사본은 만들지
않는다. 반복 머리글·쪽번호 주석, 물리 줄의 문맥용 병합, 후보 탐지는 모두 한 번
로드한 문서 안에서 수행한다.

현재 모델에는 레이아웃을 보내지 않는다. 후보가 참조하는 `line_ids`와 추출본의
`bbox_pt`/`style_runs`는 향후 text-only 대 text+layout 실험과 결과 추적을 위해
보존된다.

## 준비와 실행

먼저 PDF/HWP/HWPX를 통합 추출한다.

```bash
python scripts/extract_documents.py --source all
python scripts/find_candidates.py
```

특정 출처 파일럿은 **추출 결과 확인 단계에서만** 좁힐 수 있다.

```bash
python scripts/extract_documents.py --source moe --limit 8
```

후보 JSONL은 출처별이 아닌 전역 파일이므로, 부분 extraction manifest로는 후보를
발행하지 않는다. 파일럿 확인이 끝난 뒤 `--source all`로 다시 추출해야
`find_candidates.py`를 실행할 수 있다. `--allow-partial`은 전체 범위 안의 개별 파일
실패만 허용하며, 부분 출처 범위 검증은 우회하지 않는다.

후보 파일들은 하나의 원자적 run이므로 `--clause 8`을 주더라도 5/6/7/8과
administrative 파일을 모두 같은 `run_id`로 다시 만든다. `--clause`는 기존 호출과의
호환 옵션이다. 부분 실패가 있으면 manifest는 `partial`이고 기본 종료 코드는 1이다.
원인 조사 목적에만 `--allow-partial`을 사용한다.

후보 탐지는 디렉터리를 임의로 다시 훑지 않고 최신 extraction manifest의
`artifacts` allowlist만 읽으며, manifest의 artifact와 failure 목록이 현재 전체 원본
코퍼스를 빠짐없이 설명하는지도 확인한다. 따라서 부분 source run이 전역 후보 파일을
덮어써 다른 source 후보를 지우는 일을 막는다. 각 artifact의 상대경로,
`source_path`, `extraction_id`, status가 manifest와 다르면 해당 run은 partial이며,
candidate manifest에는 원본 `extraction_run_id`도 기록된다.

DB, `.env`, OpenAI API 키는 필요 없다.

## 출력 계약

다음 파일들을 임시 파일에 먼저 쓴 뒤 교체하고, `_manifest.json`을 마지막에 게시한다.

- `data/candidates/clause_{5,6,7,8}.jsonl`
- `data/candidates/administrative.jsonl`
- `data/candidates/_manifest.json`

후보 레코드 예시:

```json
{
  "candidate_id": "sha256...",
  "extraction_id": "sha256...",
  "source_path": "data/moe/budget_material/77151_sample.pdf",
  "source": "moe",
  "doc_type": "budget_material",
  "doc_id": "77151",
  "line_ids": [93, 94],
  "text": "내부 검토 중인 예정가격 ...",
  "text_sha256": "sha256...",
  "page": 10,
  "candidate_kind": "clause",
  "clause": "5",
  "matched_rules": ["keyword:예정가격"],
  "context_rules": [],
  "match_score": 4,
  "rank": 1,
  "run_id": "...",
  "rule_version": "..."
}
```

`candidate_id`는 `extraction_id + line_ids + 후보 종류/조항`으로 결정된다. 원문이나
추출 설정이 달라지면 ID 연결이 바뀌며, 하위 단계는 `extraction_id`와
`text_sha256`가 현재 추출본과 다르면 stale 후보로 거부한다. 후보에는 bbox를
복제하지 않는다.

각 조항 후보는 문서당 점수 상위 60개로 제한된다. 모든 조항과 행정 규칙은 같은
derived segment 순회에서 평가되므로 코퍼스를 조항 수만큼 다시 읽지 않는다.

## 규칙 변경과 검증

키워드·정규식을 바꾼 뒤에는 후보 전체 run을 다시 생성하고, 새 규칙이 잡은 표본을
직접 확인한다. 일반 행정 문구가 대량 매치되면 키워드를 좁히거나 제거하고 그 이유를
회귀 테스트로 남긴다. 6호는 단독 직책/부서명이 아니라 명확한 개인정보 자리 구조만
인정하며 공무원의 직무 관련 직위 표기는 제외한다.

```bash
python -m pytest -q tests/test_candidates.py tests/test_find_candidates.py
```
