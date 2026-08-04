# 생성 문서 PDF 렌더링 사용법

`result.generated_document`가 들어 있는 JSON을 Jinja2 + WeasyPrint
문서 유형별 템플릿으로 렌더링한다. 현재 공문 계열 10종,
`research_report` 전용 3종, `press_release` 전용 3종,
회의록·현황자료·가이드·질의회시집 전용 각 4종, 공고 계열 전용 3종을 지원한다.
`directive`(훈령), `regulation`(예규), `notification`(고시)은
행정규칙 전용 서식 4종을 공유하고 입력 분류값에 따라 유형명만 달라진다.

## 준비

```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m weasyprint --info
```

macOS Apple Silicon에서 Pango를 못 찾으면 다음 환경변수를 먼저 설정한다.

```bash
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH
```

EC2 시스템 패키지는 `deploy/README.md`를 참고한다.

## 단일 파일

```bash
python scripts/render_generated_documents.py input.json \
  --output-dir output/pdf/generated_documents/sample \
  --template 01_classic_municipal
```

보도자료는 입력에 다음 분류값이 있어야 전용 서식으로 라우팅된다.

```json
{
  "result": {
    "source_classification": {
      "document_type": "press_release"
    }
  }
}
```

문서 유형별 전용 렌더러가 있는 분류값은 해당 서식을 사용하고, 그 밖의
분류값이나 분류값이 없는 입력은 공문 계열 렌더러를 사용한다.

입력은 JSON 객체 하나, JSON 객체 배열, 또는 한 줄에 JSON 객체 하나가 들어 있는
JSONL이다. 확장자가 `.txt`여도 내용 전체가 완전한 JSON 객체이면 그대로 전달할
수 있다. 일반 원문만 있는 `.txt`는 먼저 `result.generated_document` 계약 JSON으로
감싼 뒤 실행한다.

현재 생성 파이프라인의 v2 결과인 `source_assessment`, `generation_plan`,
`generation_artifact` 구조도 바로 입력할 수 있다. 렌더러가 내부적으로
`result` envelope로 투영하며 원문 내용은 변경하지 않는다.

## 디렉터리 안의 텍스트 파일 전부 실행

디렉터리를 입력하면 `.json`, `.jsonl`, `.txt`를 하위 디렉터리까지 찾아
각 payload에서 PDF 하나만 만든다. `.txt`도 내용 전체가 완전한 생성 계약
JSON이어야 한다.

```bash
python scripts/render_generated_documents.py data/render_inputs \
  --output-dir output/pdf/generated_documents
```

선택 규칙:

- `result.source_classification.document_type`에 맞는 렌더러와 템플릿만
  후보로 사용한다.
- 같은 `doc_type` 안에서는 템플릿 사용 횟수 차이를 최대 1건으로 유지한다.
- 각 템플릿 안에서도 구조 변주 1·2·3의 사용 횟수 차이를 최대 1건으로
  유지한다.
- 후보 순서는 실행 seed로 섞으므로 연속된 파일이 같은 서식에 몰리지 않는다.
- `--seed`를 생략하면 매 실행 새 seed를 만들고
  `batch_manifest.json`에 저장한다.
- `--seed 20260730`처럼 값을 주면 파일 경로와 정렬 순서가 같을 때 선택 결과도
  같아진다.
- `--variation-count 1`, `2`, `3`으로 사용할 구조 변주 수를 제한할 수 있다.

디렉터리 실행에서는 균등 분배를 위해 `--template`과 `--per-template`을
사용하지 않는다. 단일 파일에 기존처럼 후보 전체를 생성할 때만 해당 옵션을
사용한다.

```bash
python scripts/render_generated_documents.py data/render_inputs \
  --output-dir output/pdf/generated_documents \
  --seed 20260730 \
  --variation-count 3
```

루트의 `batch_manifest.json`에는 실행 seed, 성공·거부 건수, 각 입력 파일의
`doc_type`, 선택된 템플릿, 변주 번호, 출력 폴더가 기록된다. 개별 문서의
`manifest.json`에도 같은 `batch_selection` 정보가 저장된다.

## 입력 규칙

- 지원 block: `paragraph`, `key_value`, `bullet_list`, `table`,
  `attachment_reference`
- `result.contract_version`과
  `result.generated_document.contract_version`은 같은 `2.x.x` 값이어야 한다.
- `blocks`가 내용의 기준이다.
- `body_text`를 같이 넣으면 blocks를 평탄화한 결과와 같아야 한다.
- 입력의 `failure`가 null이 아니면 기본적으로 거부한다.
- 결재선과 행정 처리 문구는
  `generated_document.document_metadata`에 있을 때만 렌더링한다.
- `pending`, `rejected`, `not_required` 결재 슬롯에는 도장이나 결재일을 넣지 않는다.

### 장문 배치와 12페이지 제한

본문 PDF는 모든 문서 유형에서 최대 12페이지다. 12페이지를 생성 상한으로
사용하지 않고, 먼저 입력 block 전체를 자연스럽게 페이지에 배치해 원문 보존을
검증한 뒤 첫 12페이지만 남긴다. 13페이지 이후는 의도적으로 폐기하며 이 경우는
`status: ok_truncated`로 구분한다. 군사기밀 표지는 이 절단이 끝난 본문 앞에
별도 한 장으로 붙으며 12페이지 제한에 포함하지 않는다. 따라서 본문이 12페이지인
군사기밀 PDF의 실제 파일 장수는 13장이다.

`manifest.json`에는 최종 `actual_pages`와 함께 다음 값이 기록된다.

- `truncated`: 13페이지 이후를 폐기했는지 여부
- `original_page_count`: 절단 전 자연 배치 페이지 수
- `retained_page_count`: 최종 보존 페이지 수
- `discarded_page_count`: 폐기한 페이지 수
- `source_text_validation_scope: pre_truncation_pdf`: 원문 검증이 절단 전 PDF를
  기준으로 수행됐다는 뜻

고정 1~2페이지 공문 서식에 장문이나 block이 많이 들어오면 표지·요약은 기존
서식으로 유지하고, 원문 block은 페이지 분할 가능한 `본문 연속본`으로 이어진다.
따라서 고정 높이 영역에서 본문이 겹치거나 잘리지 않는다.

### C 문서 보안표지

보안표지는 템플릿 HTML이 아니라 최종 PDF 공통 후처리 단계에서 적용한다.
`generation_target.classification`이 `C`이면 단색 대외비 보안 스킨 10종 중
하나를 seed로 재현 가능하게 선택한다. `military_secret_grade`가 명시되면 같은
스킨에 등급별 앞표지와 본문 상·하단 표시를 더한다. S/O 문서는 변경하지 않는다.
기관명과 정부부처 로고는 보안 스킨 적용 여부나 등급 판단에 사용하지 않는다.
다만 매핑된 기관명은 기존 중앙 워터마크 선택에 계속 사용한다.

```json
{
  "result": {
    "generation_target": {
      "classification": "C",
      "clause_no": "2",
      "subclause_key": "security_defense",
      "generation_mode": "counterfactual",
      "military_secret_grade": "2급"
    }
  }
}
```

등급이 없는 C 문서는 `대외비`, `CONFIDENTIAL`, `TOP SECRET`, `RESTRICTED`,
`NEED TO KNOW` 가상 스탬프를 선택한 보안 스킨의 예약 여백에 표시한다. 모든
가상 영문 스탬프에는 `VIRTUAL SAMPLE`이 포함되며 색상은 `#22272C` 단색이다.
보안 스킨은 공문·연구보고서·회의록 등 본문 렌더러와 독립적이며 기존 payload에
별도 필드를 요구하지 않는다.

허용 군사기밀 등급은 `1급`, `2급`, `3급`이다. 해당
`logo/*급_비밀_표지.png`를 적색·황색·
청색으로 보정해 A4 새 페이지에 배치한 뒤 본문 앞에 붙인다. 본문 모든 면에는
`logo/*급_비밀.png`를 상·하단 중앙에 표시한다. 페이지 번호나 본문이 있으면 모든
본문 페이지를 원래 비율 그대로 상·하단 30pt, 좌우 12pt 안전영역 안에 맞춰
넣고, 등급표시는 그 바깥 여백에 배치한다. 헤더·푸터가 빽빽하거나 장문 block이
많은 템플릿에서도 본문을 가리지 않는다. 잘못된 등급이나 누락된 자산은 해당
출력을 거부한다.

보안 스킨 slug:

```text
01_classic_register
02_report_band
03_minimal_mark
04_restricted_memo
05_strategy_report
06_controlled_sheet
07_official_sensitive
08_registry_control
09_protected_technology
10_need_to_know
```

공문 계열 템플릿 slug:

```text
01_classic_municipal
02_fire_station
03_internal_approval
04_personnel_notice
05_field_report
06_modern_public
07_monochrome_hwp
08_table_first_report
09_long_form
10_checklist_form
```

연구보고서 템플릿 slug:

```text
research_01_classic_flow
research_02_modular_policy
research_03_academic_flow
```

`result.source_classification.document_type`이 `research_report`이면 위
`research_*` 3종만 선택할 수 있다. 연구보고서 렌더러는 입력 block 순서와
여러 개의 표를 그대로 보존하며, 기관명이 없을 때 가상 기관명을 채우지 않는다.
표지와 페이지 번호 외에 입력에 없는 목차·장 제목·날짜·보고서 번호·로고도
추가하지 않는다. 8열 이상 표는 가로 A4 페이지로 전환한다.

연구보고서도 전체 원문을 먼저 자연 배치하고 검증한 뒤 첫 12쪽만 남긴다.
절단 여부와 절단 전·후 쪽수는 공통 manifest 필드에 기록된다.

과대 입력이 PDF 생성 과정의 메모리와 CPU를 소진하지 않도록 렌더 시작 전에
안전 한도를 검사한다. 연구보고서 1건의 한도는 제목 300자, block 160개,
본문·메타데이터 합계 40,000자, 목록 항목 600개, 표 행 500개, 표 셀
2,500개이며 템플릿별 변주는 최대 10개다. 이 값은 텍스트 내용을 고정하는
규칙이 아니라 렌더링 자원을 보호하는 상한이다.

보도자료 템플릿 slug:

```text
press_01_government_standard
press_02_briefing_focus
press_03_joint_modular
```

보도자료는 원문 block 순서를 유지하면서 다음 규칙으로 서식 역할만 정한다.

- 맨 앞 block이 `key_value`이면 보도시점·배포일 등을 놓는 상단
  정보띠가 된다.
- 첫 번째 `paragraph`는 제목 아래 리드 문장이 된다.
- 첫 번째 `bullet_list`는 핵심 요약 영역이 된다.
- 나머지 연속 `paragraph`는 한 단락 그룹으로 묶여 1열 또는 2열로 변주된다.
- 본문에 남은 마지막 `key_value`는 담당 부서·담당자 영역이 된다.
- 8열 이상 `table`은 가독성을 위해 별도 가로 페이지로 렌더링된다.
- `attachment_reference`와 `document_metadata.administrative_events`는
  입력에 있을 때만 마지막 영역에 표시된다.

위 역할 지정은 텍스트를 만들거나 바꾸지 않는다. 기관명도
`generated_document.agency_name`이 있을 때만 표시한다. 서식이 자체적으로
추가하는 문자열은 `보도자료`와 페이지 번호뿐이다.

보도자료 변주는 템플릿별 최대 10개까지 만들 수 있으며, 기본 검증은 제목이
첫 페이지에 있는지와 절단 전 PDF에 모든 원문 텍스트가 남았는지 확인한다.
렌더 전에 입력 복잡도와 예상 페이지 비용도 검사한다.
검증에 실패하면 해당 문서의 HTML/PDF는 출력하지 않고 `manifest.json`에
거부 상태를 남긴다.

디렉터리 균등 배치에서는 원문 텍스트 검증에 실패한 경우에만 최대 두 번 더
시도한다. 먼저 같은 템플릿의 compact 변주를 사용하고, 그래도 실패하면 다음
호환 템플릿의 compact 변주를 사용한다. 계약·입력·보안등급 오류에는 재시도를
적용하지 않으며 각 시도는 `render_attempts`에 기록된다.

공고 계열 템플릿 slug:

```text
notice_01_classic_gazette
notice_02_structured
notice_03_record_rail
```

`bid_notice`, `bid_renotice`, `pre_spec_notice`, `public_offering`,
`notice`는 위 `notice_*` 3종만 선택할 수 있다. 입력 block 순서와
표·붙임을 보존하고 기관명이 없을 때 가상 기관명을 채우지 않는다.
기관·공고번호·담당 부서·공고일도 입력에 없으면 추가하지 않는다. 최종 출력은
공통 규칙에 따라 첫 12쪽만 보존한다.

회의록 템플릿 slug:

```text
meeting_01_registry
meeting_02_sequence
meeting_03_columns
meeting_04_docket
```

`result.source_classification.document_type`이 `meeting_minutes`이면 위
`meeting_*` 4종만 선택할 수 있다. 공문과 같은 5종 block을 입력 순서대로
렌더링하며 회의명·일시·참석자·안건·의결결과를 추론하지 않는다. 입력에 없는
수신란·시행번호·결재선도 추가하지 않는다. 7열 이상 표는 가로 A4 페이지로
전환하고 전체 원문 배치 후 첫 12쪽만 보존한다.

현황·통계자료 템플릿 slug:

```text
status_01_brief
status_02_ledger
status_03_columns
status_04_chapter
```

`result.source_classification.document_type`이 `status_report`이면 위
`status_*` 4종만 선택할 수 있다. 별도 현황 지표나 차트를 추론하지 않고
공문과 같은 5종 block을 입력 순서대로 렌더링한다. 7열 이상 표는 가로 A4
페이지로 전환하며 전체 원문 배치 후 첫 12쪽만 보존한다.

가이드·매뉴얼·지침 템플릿 slug:

```text
guide_01_classic
guide_02_index
guide_03_cards
guide_04_field
```

`source_classification.document_type`이 `guide`이면 가이드·매뉴얼·지침
4종으로 라우팅한다. 별도 장·절·절차 필드는 필요하지 않으며 공문과 같은
5종 block을 입력 순서대로 렌더링한다. 입력에 없는 절차명이나 의미를
추측해서 추가하지 않는다.

질의회시집 템플릿 slug:

```text
interpretation_01_sequence
interpretation_02_index
interpretation_03_cards
interpretation_04_margin
```

`source_classification.document_type`이 `interpretation_compilation`이면
질의회시집 4종으로 라우팅한다. 별도 Q/A 필드는 필요하지 않으며 공문과 같은
5종 block을 입력 순서대로 렌더링한다. `질의요지`, `회시요지` 같은 역할을
추측하거나 입력에 없는 제목을 추가하지 않는다.

행정규칙 템플릿 slug:

```text
rule_01_promulgation
rule_02_article_rail
rule_03_gazette_columns
rule_04_notice_frame
```

## 결과 확인

각 입력의 출력 폴더에 HTML, PDF, `manifest.json`이 생긴다.
`manifest.json`에는 seed, 입력 해시, 기관명 선택, 원문 포함 검증,
합성 도장 파라미터와 `security_marking` 적용 결과가 기록된다. 기관 로고용
`agency_marking`은 기존과 같이 별도 필드에 기록한다. 보안 스킨 선택과 기밀
등급은 기관 로고와 독립적으로 결정된다. 배치 입력의 문서별 성공·실패는
`batch_manifest.json`에서 확인한다.

등급별 표지, 가상 스탬프와 기관 워터마크 자산 목록은
[`logo/README.md`](../logo/README.md)를 따른다.

실패 입력을 조사 목적으로만 렌더링할 때는
`--allow-failed-input`을 명시한다.

```bash
python scripts/render_generated_documents.py input.json \
  --output-dir output/pdf/generated_documents/debug \
  --allow-failed-input
```

관련 코드:

- `scripts/render_generated_documents.py`
- `src/rd2/generators/generated_document_pipeline.py`
- `src/rd2/generators/document_security_marking.py`
- `logo/README.md`
- `src/rd2/generators/official_document_rendering.py`
- `src/rd2/generators/research_report_rendering.py`
- `src/rd2/generators/press_release_rendering.py`
- `src/rd2/generators/notice_rendering.py`
- `src/rd2/generators/administrative_rule_rendering.py`
- `src/rd2/generators/interpretation_compilation_rendering.py`
- `src/rd2/generators/guide_rendering.py`
- `src/rd2/generators/status_report_rendering.py`
- `src/rd2/generators/meeting_minutes_rendering.py`
- `src/rd2/generators/synthetic_approval_stamps.py`
- `src/rd2/generators/templates/official_variants/`
- `src/rd2/generators/templates/research_report/`
- `src/rd2/generators/templates/press_release/`
- `src/rd2/generators/templates/notice/`
- `src/rd2/generators/templates/administrative_rule/`
- `src/rd2/generators/templates/interpretation_compilation/`
- `src/rd2/generators/templates/guide/`
- `src/rd2/generators/templates/status_report/`
- `src/rd2/generators/templates/meeting_minutes/`
