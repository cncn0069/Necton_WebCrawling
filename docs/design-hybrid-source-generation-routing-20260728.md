# Hybrid source-generation routing

Status: V1 IMPLEMENTED — journal/audit/eval extensions deferred

Generated from `/plan-ceo-review` on 2026-07-28.

## Objective

공개 원문이 존재한다는 이유만으로 정보공개법 제9조 제1~8호의 임의 목표를
counterfactual로 붙이지 않는다. 원문에서 관찰 가능한 근거 수준과 세부조항별
공개 가능성을 기준으로 생성 경로를 선택하고, 실제 원문 기반 데이터와 완전 합성
데이터의 provenance를 끝까지 분리한다.

## Premise

- `O`는 모든 C/S 목표에 사용할 수 있는 중립 시드가 아니다. 원문에서 비공개
  근거를 확인하지 못했다는 source label이다.
- 제1~4호는 기밀·국방·외교·생명·수사·재판 특성상 유용한 공개 원문을
  구조적으로 구하기 어려운 셀이 많다. 이 셀은 기존 완전 생성 경로를 기본으로 한다.
- 제5~8호도 원문 근거가 없으면 억지 counterfactual로 만들지 않는다.
- 실제 조항과 세부조항 근거가 발견되면 조항 번호와 관계없이 source-aligned
  경로를 허용할 수 있다.

## Accepted scope

1. 모든 산출물에 `generation_route`, route별 typed provenance와 `reason_code`를
   기록한다.
2. P1과 blind P2에 같은 세부조항 taxonomy와 경계 규칙을 제공한다.
3. 조항 단위가 아니라 세부조항별 route policy를 둔다.
4. 조항·세부조항·생성 경로별 구성비와 품질을 audit한다.
5. P1 structured output에 source suitability를 포함하고 route policy와 맞지 않으면
   결과를 거부한다.
6. 자주 혼동되는 세부조항 쌍을 위한 held-out 경계 평가셋과 confusion matrix를 둔다.

## Non-goals

- 공개 원문에서 실제 기밀 내용을 복원하거나 추측하지 않는다.
- 이번 정책 변경에서 기존 완전 생성 코드를 즉시 삭제하지 않는다.
- 생성 경로별 목표 비율을 첫 배포부터 hard failure로 고정하지 않는다.
- P2에 P1의 분류, 목표, 이유 또는 선택한 세부조항을 노출하지 않는다.

## Routing model

### Generation routes

| Route | 의미 | 최소 입력 근거 |
|---|---|---|
| `source_aligned` | 실제 원문의 C/S 분류를 유지해 불완전 문서를 생성 | 실제 조항·세부조항을 지지하는 검증 가능한 source evidence span |
| `span_seeded` | 부분공개 문서의 민감 span과 메타데이터를 보존해 생성 | 목표 세부조항과 직접 연결되는 검증 가능한 span |
| `anchored` | 공개 참고문서는 문서 구조·업무 맥락에만 사용하고 민감 사실은 별도 seed에서 사용 | 목표와 관련된 업무 맥락 + 별도의 허용된 목표 seed |
| `fully_synthetic` | 시나리오에서 사건과 본문을 완전 생성 | taxonomy상 유효한 목표와 합성 시나리오 |

`unanchored_counterfactual`은 정식 route가 아니다. 일반 공개문서에 관련 없는 C/S
목표를 붙이는 현재의 O fallback은 허용하지 않는다.

`fully_synthetic`의 **본문 생성 단계**는 source-free 실행 경로다. P1은 원문을
분석해 이 route와 target을 선택하지만, P1이 만든 임시 생성본은 즉시 폐기한다.
후속 합성 실행기에는 최종 target, 별도 scenario ID, 기관명, 생산일자만 전달하며
원문 block, quote, source rationale은 전달하지 않는다.

### Source evidence levels

| Level | 판정 |
|---|---|
| `direct_legal_evidence` | 원문에 조항 또는 명확한 비공개 사유와 세부조항 근거가 있음 |
| `direct_sensitive_span` | 조항 표시는 없지만 목표 세부조항을 직접 지지하는 민감 span이 있음 |
| `contextual_anchor_only` | 문서유형·업무 맥락은 관련되지만 목표 민감정보는 없음 |
| `no_usable_public_source` | 목표와 연결되는 공개 근거가 없음 |

### Executable stage sequence

```text
manifest + 선택된 source + 선택적 seed/anchor
  -> P1: source 분류 + suitability + route/target 선택 + 임시 GeneratedDocumentIR (LLM 1회)
  -> P1 evidence/route/result 검증 (code)
  -> fully_synthetic이면 P1 임시 IR 폐기
     -> target + source-free context로 기존 생성기 호출 (LLM 1회)
     -> legacy title/body를 canonical block IR로 변환
  -> execution provenance attachment (code)
  -> blind P2 (GeneratedDocumentIR만 입력)
```

P1은 현재처럼 한 번의 호출에서 원문 분류와 생성을 함께 수행한다. P1이 입력에
실제로 존재하는 source/seed/anchor와 taxonomy를 보고 route와 최종 target도
선택한다. manifest의 counterfactual target은 강제값이 아니라 제안값이며 P1은
원문에 더 적합한 clause/subclause로 변경할 수 있다. 요청 target과 최종 target을
모두 provenance에 보존한다. 호출 후 코드는 선택 route의 prerequisite와 evidence를
검증한다. 실패 시 다른 route로 조용히 재생성하지 않는다.

`no_usable_public_source`는 원문 전체에 근거가 없다는 뜻이 아니다. 선택 계층이
모델에 제공한 view 안에서 근거를 찾지 못했다는 뜻이다. 분석 결과에는
`assessment_scope`, selection method/hash 및 `truncated`를 기록한다.

### Authoritative route precedence

P1은 세부조항별 허용 route 안에서 다음 우선순위를 사용한다.

```text
1. direct_legal_evidence + source C/S
   + source label과 target 일치 + source_aligned 허용
   -> source_aligned

2. direct_sensitive_span + source O
   + span_seeded 허용
   -> span_seeded

3. contextual_anchor_only + validated seed
   + anchored 허용
   -> anchored

4. fully_synthetic 허용
   -> fully_synthetic

5. 어느 조건도 만족하지 않음
   -> terminal route_not_available
```

이 설계에서 source C/S는 단순히 민감해 보이는 문장이 있다는 뜻이 아니라,
원문에 기록된 조항·비공개 사유 또는 held-out 평가로 검증된 동등 수준의 근거로
법적 분류를 확정할 수 있다는 뜻이다. 그 수준에 못 미치는 민감 span은 source O를
유지하며, 해당 세부조항 policy가 허용할 때만 `span_seeded` 입력으로 사용할 수
있다. `direct_sensitive_span`이 항상 `span_seeded`를 뜻하지 않는다.

## Default policy by subclause

모든 셀에서 근거 없는 counterfactual은 금지한다. 아래 값은 기본 선호 경로이며,
실제 `direct_legal_evidence`가 있으면 어떤 셀에서도 `source_aligned`를 허용한다.

| Clause | Subclause | Allowed routes in priority order |
|---|---|---|
| 1 | `legal_secret` | `source_aligned`, `fully_synthetic` |
| 2 | `security_defense` | `source_aligned`, `fully_synthetic` |
| 2 | `unification_diplomacy` | `source_aligned`, `fully_synthetic` |
| 3 | `life_body` | `source_aligned`, `anchored`*, `fully_synthetic` |
| 3 | `property` | `source_aligned`, `anchored`*, `fully_synthetic` |
| 4 | `trial_investigation` | `source_aligned`, `anchored`*, `fully_synthetic` |
| 4 | `prosecution` | `source_aligned`, `anchored`*, `fully_synthetic` |
| 4 | `correction_security` | `source_aligned`, `anchored`*, `fully_synthetic` |
| 5 | `audit_inspection` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 5 | `bid_contract` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 5 | `personnel_management` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 5 | `decision_review` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 5 | `technology_development` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 6 | `petitioner_pii` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 6 | `personnel_pii` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 6 | `welfare_pii` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 6 | `subject_pii` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 7 | `technology_patent` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 7 | `ma_terms` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 7 | `security_diagnosis` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 7 | `unit_cost` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 7 | `business_strategy` | `source_aligned`, `span_seeded`, `fully_synthetic` |
| 8 | `real_estate_speculation` | `source_aligned`, `fully_synthetic` |
| 8 | `cornering` | `source_aligned`, `fully_synthetic` |

### 범위 결정 (2026-07-29): 제1~4호는 공개 원문에서 파생시키지 않는다

실문서 50건 실행 결과, 공개 원문에 counterfactual C 목표를 주면 P1은 대부분
`no_usable_public_source`로 물러섰다. 기밀·국방·외교·생명·수사·재판 내용은
공개 문서에 애초에 없으므로, `span_seeded`와 `anchored`는 근거 없는
counterfactual이 되거나 원문을 형식적으로만 붙여 놓는 결과가 된다.

따라서 **제1~4호에서는 공개(O) 원문을 재료로 쓰는 경로를 제거하고 완전 생성으로
보낸다.** `source_aligned`는 원문 자체가 진짜 C/S일 때만 도달하므로 남겨둔다.
원문 기반 경로는 **제5~8호와 행정상태**에 집중한다 — 입찰 평가, 원가, 감사
지적, 개인정보, 결재 상태는 공개 문서에 실제로 존재하거나 인접해 있다.

구현은 `pipeline.available_routes()`의 `CONFIDENTIAL_CLAUSES`이며, 아래 표의
제1~4호 행에서 `anchored`는 이 결정에 따라 제외된다.

별표의 `anchored`는 v1 포함 여부가 미결정이다. 표의 앞 route가 우선이지만 각
route의 evidence prerequisite가 충족될 때만 선택한다. 근거가 없으면 다음 허용
route로 내려가며 source 문서를 억지로 목표에 맞추지 않는다.

## Contract changes

단일 `Pass1Result`는 기존 source classification, generation target,
`GeneratedDocumentIR`과 함께 다음 suitability를 반환한다.

```json
{
  "source_suitability": {
    "evidence_level": "direct_sensitive_span",
    "assessment_scope": "selected_view_only",
    "usable_evidence_spans": [
      {
        "block_id": "p3:b2",
        "start": 14,
        "end": 28,
        "quote": "제안서 평가항목 및 배점"
      }
    ],
    "reason_code": "DIRECT_BID_EVALUATION_SPAN",
    "rationale": "입찰 평가 기준을 직접 지지하는 원문 구절이 있다."
  }
}
```

모델 출력에는 `recommended_route`와 `generation_provenance`를 두지 않는다.
P1 결과 검증이 완료된 뒤 코드는 모델이 선택한 route/target과 실제 evidence를
바탕으로 `GenerationProvenance`를 기록한다.

`GenerationProvenance`는 `generation_route`를 discriminator로 갖는 union이다.

- `source_aligned`: selection hash와 검증된 source evidence refs 필수
- `span_seeded`: canonical span/seed artifact ID와 hash 필수
- `anchored`: 구조용 anchor refs와 민감정보 seed refs를 별도 필드로 필수 기록
- `fully_synthetic`: target/scenario manifest refs 필수, source/selection/evidence
  refs 금지, `source_evidence_level`은 `null`

공통 필드는 `reason_code`, policy version/rule ID, executor ID/version, prompt/model
receipt 및 generated IR hash다.

코드는 다음을 검증한다.

- evidence span의 block ID, offset, quote가 실제 선택 원문과 일치한다.
- evidence level과 route 조합이 deterministic routing 규칙에 맞는다.
- 선택된 route가 해당 세부조항 policy에서 허용되고 prerequisite를 충족한다.
- C/S `source_aligned`는 source label과 generation target이 정확히 같다.
- `fully_synthetic`에는 source evidence를 생성 근거처럼 기록하지 않는다.
- `anchored`에는 source anchor와 민감 seed provenance가 분리되어 있다.

모든 LLM 생성 산출물은 route와 관계없이 기존 의미의 `is_synthetic=true`를
유지한다. 원문 근거 사용 여부는 `generation_route`와 `uses_source_evidence`로
별도 표현한다.

### Core contracts

- `SourceSuitability`: evidence level, evidence spans, assessment scope, reason
- `Pass1Result`: source classification, suitability, selected route/target, canonical IR
- `GenerationProvenance`: 시스템이 관찰한 입력·정책·실행기·산출물 hash

모든 route는 동일한 P1 structured-output gateway에서 분류와 route 선택을 한다.
`source_aligned`·`span_seeded`·`anchored`는 검증된 P1 IR을 사용하고,
`fully_synthetic`만 source-free legacy adapter가 IR을 교체한다. 모델이 선택한
route는 검증 전에는 provenance로 인정하지 않는다.

## Prompt taxonomy

P1과 P2에는 `SubclauseKey` enum 이름만 보내지 않고 다음을 versioned prompt
bundle에 포함한다.

- 조항별 허용 세부조항
- 각 세부조항의 한국어 라벨과 판정 정의
- 포함 기준과 제외 기준
- 혼동하기 쉬운 세부조항 간 우선순위 규칙

예:

```text
제5호(S)
- bid_contract: 입찰·계약 절차의 공정한 수행과 직접 관련된 평가기준,
  배점, 예정가격, 협상 내용
- decision_review: 특정 전문업무에 속하지 않는 진행 중 정책결정,
  회의, 결재 전 일반 내부검토

경계 규칙:
- "내부 검토 중"이라는 표현만으로 decision_review를 선택하지 않는다.
- 핵심 업무가 입찰 평가이면 bid_contract를 우선한다.
```

P2에는 동일한 taxonomy 정의만 제공한다. route policy, suitability, target,
seed/anchor ID, source classification 및 P1 rationale은 제공하지 않는다.
taxonomy hash와 route-policy hash도 별도로 관리한다. prompt-capture 테스트에서
금지 필드와 실제 목표값이 P2 입력에 없음을 검증한다.

## Reuse of existing code

- `src/rd2/source_generation/`: source selection, P1/P2, evidence validation,
  journal, audit bridge를 유지한다.
- `src/rd2/source_generation/legacy_synthetic.py`: 민감 5~8호의 모든
  subclause를 기존 시나리오·template target·content point에 연결하고 legacy
  title/body를 block IR로 변환한다.
- `src/rd2/generators/generate.py`: target별 세부 생성 지침을 받을 수 있도록
  확장해 민감 5~8호 `fully_synthetic` 실행기로 재사용한다.
- 기밀 1~4호 adapter 연결은 다음 단계로 미룬다.
- 기존 `is_synthetic`은 제거하지 않고 provenance의 coarse compatibility
  필드로 유지한다.
- 기존 생성 코드는 새 router와 audit가 안정화될 때까지 삭제하지 않는다.

## Audit

다음을 조항·세부조항·문서유형·route별로 집계한다.

- 산출물 수와 비율
- P1 목표와 blind P2 판정의 type/C-S-O/clause/subclause 일치율
- evidence validation 실패율
- route-policy 위반 건수
- 동일 원문 또는 동일 seed에서 파생된 데이터 비율
- 완전 생성 집중도와 source provenance 다양성

초기에는 route 구성비를 경고로만 보고한다. 다음 항목은 즉시 hard failure다.

- 근거 없는 `source_aligned`
- seed span 없는 `span_seeded`
- seed 없는 `anchored`
- 허용되지 않은 route/subclause 조합
- `unanchored_counterfactual`
- P2 evidence offset 불일치

## Held-out boundary evaluation

생성 입력과 겹치지 않는 평가셋으로 최소한 다음 경계를 측정한다.

- `bid_contract` / `decision_review`
- `audit_inspection` / `decision_review`
- `personnel_management` / `personnel_pii`
- `technology_development` / `technology_patent`
- `security_defense` / `security_diagnosis`

각 사례는 정답, 혼동 후보, 판정 근거, 금지 근거를 포함한다. P1과 P2를 별도로
평가하고 subclause confusion matrix를 산출한다. taxonomy 또는 prompt bundle이
바뀌면 평가를 무효화하고 다시 실행한다.

## Rollout

1. taxonomy 정의와 route policy를 versioned artifact로 만든다.
2. route/suitability/provenance 계약과 deterministic validation을 추가한다.
3. P1/P2 prompt에 taxonomy를 추가하고 P1을 단일 호출로 유지한다.
4. route별 unit/integration 테스트와 P2 leakage 회귀 테스트를 실행한다.
5. 후속 작업에서 journal/audit bridge, route 구성비 및 held-out confusion
   matrix를 확장한다.

### Failure and fallback

- P1 또는 evidence validation 실패: 해당 stage 실패로 종료
- route prerequisite 부족: 다음 허용 route를 deterministic하게 평가
- 허용 route 없음: `route_not_available`로 종료
- executor refusal/SDK transient error: 같은 route만 제한 재시도
- executor contract/evidence 실패: 다른 route로 조용히 fallback하지 않고 종료
- `fully_synthetic` 선택도 P1 결과 검증을 통과한 경우에만 허용

테스트 matrix는 모든 evidence level × source label × seed availability × allowed
route 조합, journal resume/invalidation, audit bridge, legacy fixture migration 및
P2 leakage를 포함한다.

## 행정상태 기반 민감 문서

행정상태는 정보공개법 조항과 독립된 축이다. 생성계획이 행정상태를 고정해
P1에 전달하며, P1은 이를 바꾸거나 제거할 수 없다.

- 법적 조항이 없는 행정상태 단독 target은 `classification=S`,
  `clause_no=null`, `subclause_key=null`,
  `generation_route=administrative_augmented`를 사용한다.
- 법적 조항 target에도 행정상태를 함께 지정할 수 있다.
- 상태명만 `key_value` 메타데이터로 덧붙이지 않는다. P1은 상태별
  `writing_instruction`에 따라 `paragraph`의 자연스러운 문장에 상태와 처리
  맥락을 드러낸다.
- 코드가 필수 상태 표현의 paragraph 존재 여부와 완료·확정 문구의 모순을
  검증한다. 누락 또는 모순이면 P2를 호출하지 않는다.
- P2는 법적 C/S/O와 행정상태 finding을 독립 판정한다. 법적 O라도 확인된
  행정상태가 있으면 계산 필드 `effective_classification`은 S다.
- 비교와 audit은 법적 clause/subclause 일치와 행정상태 집합 일치를 각각
  기록한다.

## Deferred decisions

1. 제6호 span 비식별화와 `span_seeded` 활성화는 1차 구현 범위에서 제외하고
   후속 작업에서 허용 필드와 전처리 책임을 결정한다.
2. route 구성비 warning을 hard gate로 승격할 최소 표본 수와 목표 비율은
   dry-run 분포를 본 뒤 정한다.

## What already exists

- `Pass1Result`는 source classification, generation target, canonical block IR을
  한 structured-output 호출로 반환한다.
- `execute_pass2`는 `GeneratedDocumentIR`만 직렬화해 독립 grader에 전달한다.
- source와 generated IR의 evidence span은 block ID와 character offset으로
  검증된다.
- taxonomy는 24개 subclause와 조항별 허용 관계를 이미 갖고 있다.

## V1 implementation tasks

1. taxonomy를 모델이 읽을 수 있는 versioned prompt text로 렌더링한다.
2. `GenerationRoute`, `SourceEvidenceLevel`, `SourceSuitability` 계약을 추가한다.
3. `Pass1Result`가 선택 route와 suitability를 함께 반환하도록 확장한다.
4. P1 prompt에 route 정의, taxonomy 및 경계 규칙을 전달한다.
5. route별 prerequisite를 P1 후 코드에서 검증하고 실패 시 P2를 호출하지 않는다.
6. 요청 target과 P1 최종 target을 분리하며 P2 비교는 최종 target을 사용한다.
7. P2 입력에는 taxonomy만 추가하고 route, suitability, 요청 target 및 source
   classification은 노출하지 않는다.

## Test coverage plan

```text
run_two_pass
├─ P1 source_aligned
│  ├─ C/S source + valid source evidence                 [UNIT]
│  └─ O source 또는 evidence 없음 → P2 전에 실패         [UNIT]
├─ P1 span_seeded
│  ├─ O source + valid sensitive span                    [UNIT]
│  └─ span offset/quote 불일치 → P2 전에 실패            [UNIT]
├─ P1 anchored
│  ├─ contextual anchor + 별도 seed                      [UNIT]
│  └─ anchor 또는 seed 누락 → P2 전에 실패               [UNIT]
├─ P1 fully_synthetic
│  ├─ source evidence를 생성 근거로 사용하지 않음         [UNIT]
│  └─ source evidence 주장 → P2 전에 실패                [UNIT]
├─ P1이 suggested target을 변경
│  ├─ taxonomy상 유효한 target → 허용                    [UNIT]
│  └─ route/subclause 불일치 → P2 전에 실패              [UNIT]
└─ blind P2
   ├─ GeneratedDocumentIR + taxonomy만 전달              [REGRESSION]
   └─ route/suitability/source/requested target 누출 없음 [REGRESSION]
```

Prompt 품질 live eval과 held-out confusion matrix는 후속 작업이다. v1에서는 prompt
snapshot, JSON Schema, route validation 및 leakage 회귀 테스트를 실행한다.

## Performance

`fully_synthetic`이 아닌 route는 문서당 P1 1회 + P2 1회다.
`fully_synthetic`은 source-free 본문 생성을 분리하므로 P1 1회 + 합성 1회 +
P2 1회다. taxonomy text는 prompt bundle에서 재사용한다.

## Failure modes

- P1이 입력에 없는 evidence를 주장: evidence validation 실패, P2 미호출
- P1이 prerequisite 없는 route 선택: route validation 실패, 자동 fallback 없음
- P1이 suggested target 변경: 허용하고 final target을 P2 비교 기준으로 사용
- P2 입력에 P1 메타데이터 누출: regression test 실패
- 장문서 selected view에서 근거 미발견: 전체 원문 부재로 표현하지 않고
  `selected_view_only`로 기록

## NOT in scope

- journal resume/invalidation의 route별 확장
- audit의 route 구성비 리포트와 hard threshold
- held-out confusion matrix 실행 CLI와 live model eval
- 기밀 1~4호의 기존 생성기 adapter 연결
- 제6호 개인정보 span 비식별화 및 `span_seeded` 활성화
- 새 PDF/HTML renderer

## Parallelization

계약·pipeline 변경은 같은 타입을 공유하므로 순차 구현한다. 현재 diff 규모에서는
단일 작업 흐름이 가장 작고 충돌 위험이 낮다.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | hybrid routing과 6개 확장 채택 |
| Codex Review | independent reviewer | Independent 2nd opinion | 2 | CLEAR | route와 target 선택 책임을 사용자 결정으로 해소 |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | CLEAR | scope reduced, single-P1 구조 확정 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | N/A | backend/prompt-only |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | N/A | 해당 없음 |

**VERDICT:** CEO + ENG CLEARED — v1 구현 준비 완료

NO UNRESOLVED DECISIONS
