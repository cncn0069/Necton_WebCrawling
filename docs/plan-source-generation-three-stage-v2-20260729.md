# Source Generation v2: 유형 판별기 → 생성기 → 정합성 판별기

Date: 2026-07-29  
Branch: `codex/backup-source-sensitive-20260729`  
Status: IMPLEMENTED  
Contract version: `2.0.0`

## 목표

현재 한 번의 P1 응답이 원문 판별, 원문 적합성, 생성 경로·목표 선택, 문서 생성을
동시에 수행한다. 이를 다음 세 역할로 분리한다.

```text
원문 전체 스냅샷
  → 유형 판별기(LLM)
  → 결정론적 계획기(code)
  → 생성기(LLM)
  → 정합성 판별기(다른 LLM, blind)
  → 결정론적 비교
  → 완료 / 사람 검토 / 생성 단계 재시도 / 제외
```

핵심 불변식은 생성기가 판별 결과·경로·목표를 다시 출력하거나 수정하지 않고
`GeneratedDocumentIR`만 반환하는 것이다.

## 확정된 결정

1. 유형 판별기는 원문의 사실만 판정한다.
   - 문서형식
   - 원문 S/O와 제9조 제5~8호·세부조항·근거
   - 원문 적합성
   - 업무 맥락
   - 등장인물 역할
   - 사용할 수 있는 표·key-value 항목
   - 호환 가능한 세부유형
2. 요청 목표와 판별 결과를 조합해 경로·최종 목표를 정하는 일은 결정론적 코드가
   담당한다.
3. 생성기는 잠긴 판별·계획과 원문 전체 스냅샷을 받으며 문서 IR만 반환한다.
4. 정합성 판별기는 생성된 IR만 보고 문서형식·S/O·호·세부조항·민감관계를
   독립 판정한다. 원문 또는 목표는 보지 않는다.
5. 판별기와 생성기는 같은 모델을 사용할 수 있다. 정합성 판별기 모델은 판별기와
   생성기 모델 모두와 달라야 한다.
6. 각 단계는 독립 계약, receipt, 모델 ID, 프롬프트 해시와 content hash를 가진다.
7. 새 계약은 `2.0.0`이다. v1 파일은 삭제하지 않지만 새 실행에서는 재사용하지
   않고 `contract_version_changed`로 무효화한 뒤 다시 실행한다.
8. `run_two_pass`, `execute_pass1`, `execute_pass2`, P1/P2 journal 단계와 공개 출력
   이름은 이번 변경에서 제거한다. 저장소 안의 모든 소비자를 새 API로 전환한다.
9. 실제 LLM 품질 평가는 이번 구현에 포함하지 않는다. 가짜 gateway를 이용한
   결정론적 코드 테스트만 수행한다.
10. 생성기는 원문 전체를 본다. 생성문과 원문을 대조하는 별도 안전검사는 만들지
    않는다.

## 계약

### SourceAssessment

유형 판별기 LLM의 유일한 출력이다.

```text
SourceAssessment
  source_classification
    document_form / other_document_form
    classification / clause_no / subclause_key / evidence_spans
  source_suitability
    assessment_scope / evidence_level / evidence_spans
    rationale / reason_code
  business_context
  subject_roles[]
  available_slots[]
  compatible_subclauses[]
```

자유 설명은 보조 정보이며, 자동 계획에 사용하는 역할·슬롯·호환 세부유형은 enum과
구조화 필드로 저장한다.

### GenerationPlan

LLM이 아니라 코드가 만든다.

```text
GenerationPlan
  requested_target
  final_target
  generation_route
  source_assessment_sha256
  source_sha256
  selection_sha256
  planner_policy_version
  planner_policy_sha256
```

호환되지 않는 원문·목표 조합은 생성기를 호출하기 전에 typed failure로 끝낸다.

### GenerationArtifact

```text
GenerationArtifact
  plan_sha256
  generated_document
  attempt_index
  parent_generation_sha256
  repair_codes[]
  provenance
```

생성기의 structured-output schema는 `GeneratedDocumentIR`이며 판별·계획 필드를
포함하지 않는다. 모델 호출 receipt는 `GenerationStageArtifact`가 별도로 보존한다.

### ValidationArtifact

```text
ValidationArtifact
  generated_document_sha256
  independent_assessment
  comparison
  receipt
```

`comparison`은 독립 판정과 잠긴 계획을 코드로 비교한다. 형식·등급·호·세부조항·
민감 주체 역할을 각각 별도 boolean으로 남긴다.

### RepairCode

최소 오류 코드는 다음과 같이 고정한다.

```text
FORM_MISMATCH
CLASSIFICATION_MISMATCH
CLAUSE_MISMATCH
SUBCLAUSE_MISMATCH
MASK_REMAINS
DIRECT_VALUE_MISSING
ROLE_INCOMPATIBLE
EVIDENCE_INVALID
```

코드와 함께 사람이 읽는 설명을 저장할 수 있지만 재시도 분기는 enum만 사용한다.

## 프롬프트

`prompts.py`의 한 `PromptBundle`에서 다음 정의를 관리한다.

```text
classifier
generator
sensitive_generator
validator
sensitive_validator
```

- `classifier`: 정보공개법 제9조 역할·판정 원칙, 문서형식 설명, 제5~8호 taxonomy,
  역할·슬롯·호환성 판정 규칙
- `generator`: 잠긴 계획을 따르는 문서 작성 규칙과 제5~8호 세부 생성 규칙
- `validator`: 생성 IR만 보는 독립 판정 규칙
- 각 정의는 개별 SHA-256을 가진다. 전체 bundle hash만으로 단계별 캐시를
  무효화하지 않는다.

## Journal과 재시작

```text
classified → planned → generated → validated → audited
                  ↑          │
                  └─ repair ─┘
```

| 변경 | 다시 실행하는 단계 |
|---|---|
| 원문·선택·classifier 모델·classifier 프롬프트 | classified부터 전부 |
| 요청 목표·planner 정책·SourceAssessment | planned부터 전부 |
| generator 모델·generator 프롬프트·GenerationPlan | generated부터 전부 |
| validator 모델·validator 프롬프트·생성문 | validated부터 |
| audit 설정 | audited만 |

검사 불일치 시 판별과 계획은 유지한다. 비교 결과를 `RepairCode[]`로 바꾸어 생성
단계만 다시 실행하고, 다음 검사도 새 생성물만 대상으로 실행한다. 각 시도는
`parent_generation_sha256`으로 앞 시도와 연결한다. 최대 횟수를 넘으면
`excluded_after_retry` 또는 `hard_case_review`로 종료한다.

## API와 소비자 전환

새 진입점은 다음 이름을 사용한다.

```text
execute_classification()
build_generation_plan()
execute_generation()
execute_consistency_validation()
run_three_stage_pipeline()
run_source_sensitive_pipeline()
```

다음 소비자를 같은 변경에서 전환한다.

- journal과 artifact store
- audit bridge와 run manifest
- holdout evaluation
- source-generation batch CLI
- Seoul official batch CLI
- preview 생성기와 렌더링 스크립트
- JSONL/CSV/HTML report 필드
- 패키지 `__init__` export

## 기존에 재사용하는 것

- `resolve_source_target()`의 원문 선판정 흐름은 `execute_classification()`의
  시작점으로 흡수한다.
- `SourceClassification`, `SourceSuitability`, `GenerationTarget`,
  `GeneratedDocumentIR`의 세부 validator를 재사용한다.
- `available_routes()`와 현재 `Pass1Result`의 source/target/route 정합성 규칙을
  결정론적 계획기로 이동한다.
- 현재 blind `execute_pass2()`의 prompt, evidence canonicalization,
  `SensitivePass2Assessment`, `validate_sensitive_assessment()`를 새 validator에
  재사용한다.
- journal의 append-only 기록, artifact hash 검증, 실패 후 resume 구조를
  5단계 상태로 확장한다.
- PromptBundle fingerprint와 typed failure/receipt 구조를 재사용한다.

## NOT in scope

- 실제 모델을 호출하는 제5~8호 품질 평가: `TODOS.md`에 후속 작업으로 기록
- 원문과 생성문을 대조해 실제 개인정보 복사·출처 위반을 찾는 grounded checker:
  명시적으로 제외하며 TODO도 만들지 않음
- 정보공개법 제1~4호 또는 C 생성 경로 확장
- block IR·PDF renderer의 별도 구조 개편
- v1 artifact를 가짜 분리 receipt로 변환하는 마이그레이션
- 저장소 밖 외부 호출자의 `run_two_pass` 호환성
- 새 패키지·서비스·컨테이너 배포

## 실패 모드

| 실패 | 처리 | 테스트 | 사용자에게 보이는 결과 |
|---|---|---|---|
| 원문 선택 또는 판별 호출 실패 | classified 실패 기록 | 단위/통합 | typed failure |
| 판별 evidence가 원문과 불일치 | 생성 미호출 | 단위/통합 | evidence invalid |
| 원문과 목표가 비호환 | 계획 단계에서 중단 | 단위 | incompatible source |
| 생성 호출 또는 IR 계약 실패 | 판별·계획 보존 | 단위/통합 | generation failed |
| 독립 검사 호출·evidence 실패 | 생성물 보존 후 검사 재개 | 단위/통합 | validation failed |
| 판정 불일치 | repair code로 생성만 재시도 | 통합 | retry/excluded |
| 단계 프롬프트·모델 변경 | 해당 단계와 downstream 무효화 | journal 통합 | invalidation reason |
| v1 artifact 발견 | 보존하되 재사용하지 않음 | journal 통합 | version changed |
| 원문 실제 값이 생성문에 복사됨 | 별도 검출 없음 | 없음 | 조용히 통과할 수 있음 |

마지막 항목은 사용자가 수용한 critical risk다.

## 테스트 실행 경로

```text
CODE PATHS                                      BATCH/CONSUMER FLOWS
[GAP] execute_classification                    [GAP] 정상 3단계 배치
  ├─ 성공/evidence 정규화                         [GAP] 비호환 원문 제외
  └─ 호출·schema·evidence 실패                    [GAP] 검사 불일치 후 생성만 재시도
[GAP] build_generation_plan                     [GAP] 중단 후 마지막 성공 단계부터 재개
  ├─ source-aligned/counterfactual routes        [GAP] v1 기록 보존 후 v2 재실행
  └─ incompatible target                        [GAP] preview/audit/holdout v2 소비
[GAP] execute_generation
  ├─ IR only / locked plan
  └─ retry lineage / typed failures
[PARTIAL] independent validator
  ├─ 기존 blind P2 로직 재사용
  └─ form/class/clause/subclause/role 비교
[PARTIAL] journal
  ├─ 기존 P1/P2 resume 재사용
  └─ 5단계 invalidation matrix로 확장
```

필수 테스트:

1. 계약 field 순서·필수값·금지값과 v2 schema
2. classifier prompt에 생성 지침이 없고 generator prompt에 판정 책임이 없는지
3. generator response schema가 `GeneratedDocumentIR`뿐인지
4. classifier → planner → generator → validator 호출 순서
5. classifier 실패 시 1회, generator 실패 시 2회, validator 실패 시 3회 호출
6. classifier와 generator 모델 동일 허용, validator 모델 동일 금지
7. 호환·비호환 문서형식/역할/세부유형 계획
8. 모든 generation route 회귀
9. 각 comparison mismatch의 정확한 repair code
10. 재시도에서 classifier/planner를 다시 호출하지 않는지
11. 단계별 prompt/model/source/target hash 무효화 행렬
12. v1 artifact typed invalidation
13. preview·audit·holdout·batch report의 v2 field
14. 옛 P1/P2 API와 export가 남아 있지 않은지

실제 LLM 호출 테스트와 prompt 품질 baseline 비교는 실행하지 않는다.

## 구현 순서와 병렬화

| 단계 | 모듈 | 의존 |
|---|---|---|
| A. v2 계약·프롬프트 | `source_generation/` | 없음 |
| B. 세 단계 실행·계획기 | `source_generation/` | A |
| C. journal·audit 전환 | `source_generation/` | B |
| D. CLI·preview·report 전환 | `scripts/`, generators | B |
| E. 결정론적 테스트 | `tests/source_generation/` | A, B |
| F. 소비자 회귀 테스트 | `tests/` | C, D |

Lane A: A → B → C (공유 핵심 모듈이라 순차)  
Lane B: B 이후 D (소비자 전환)  
Lane C: B 이후 E, C+D 이후 F

B가 끝난 뒤 Lane A의 C, Lane B의 D, Lane C의 E를 병렬 진행할 수 있다.
`source_generation/__init__.py`와 공통 fixture는 충돌 가능성이 있으므로 최종 통합자가
한 번에 정리한다.

## Implementation Tasks

- [ ] **T1 (P1)** — v2 단계 계약과 개별 프롬프트 정의
  - Files: `contracts.py`, `prompts.py`, `source_target.py`, `__init__.py`
  - Verify: contract/prompt/source-target tests
- [ ] **T2 (P1)** — 판별·계획·생성·독립 검사 오케스트레이션
  - Files: `pipeline.py`, `sensitive_policy.py`
  - Verify: pipeline tests의 호출·실패·repair 행렬
- [ ] **T3 (P1)** — 5단계 journal, 개별 hash, retry lineage, v1 invalidation
  - Files: `contracts.py`, `journal.py`
  - Verify: journal resume/invalidation tests
- [ ] **T4 (P1)** — audit·holdout·batch·preview·report v2 전환
  - Files: `audit_bridge.py`, `holdout_eval.py`, `scripts/`, generators
  - Verify: 관련 소비자 회귀 테스트
- [ ] **T5 (P1)** — P1/P2 API·export·출력 필드 완전 제거
  - Files: 저장소 전체 사용처
  - Verify: `rg` 잔존 검사와 전체 source-generation 테스트

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---:|---|---|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | 이번 변경에 불필요 |
| Codex Review | outside voice | Independent challenge | 1 | issues found | 3 findings, 1 accepted, 2 rejected |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | issues open | 20 issues/gaps, 1 accepted critical risk |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not applicable | backend/prompt pipeline |
| DX Review | `/plan-devex-review` | Developer experience | 0 | not run | 별도 검토 없음 |

**VERDICT:** ENG REVIEW COMPLETE — 구현 가능, 원문 실제 값 복사를 검출하지 않는 위험을 수용함

NO UNRESOLVED DECISIONS
