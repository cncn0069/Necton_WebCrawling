# 생성 문서 PDF 렌더링 사용법

`result.generated_document`가 들어 있는 JSON을 Jinja2 + WeasyPrint
문서 유형별 템플릿으로 렌더링한다. 현재 공문 계열 10종,
`research_report` 전용 3종, `press_release` 전용 3종을 지원한다.

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

분류값이 없거나 `press_release`가 아니면 현재 공문 계열 렌더러를 사용한다.

입력은 JSON 객체 하나, JSON 객체 배열, 또는 한 줄에 JSON 객체 하나가 들어 있는
JSONL이다. 확장자가 `.txt`여도 내용 전체가 완전한 JSON 객체이면 그대로 전달할
수 있다. 일반 원문만 있는 `.txt`는 먼저 `result.generated_document` 계약 JSON으로
감싼 뒤 실행한다.

## 디렉터리 안의 텍스트 파일 전부 실행

아래 예시는 각 `.txt`에 완전한 생성 결과 JSON 객체 하나가 들어 있을 때 사용한다.
파일별 출력 폴더를 분리하므로 manifest가 서로 덮어쓰이지 않는다.

```bash
INPUT_DIR=data/render_inputs
OUTPUT_DIR=output/pdf/generated_documents

find "$INPUT_DIR" -type f -name '*.txt' -print0 |
while IFS= read -r -d '' input_file; do
  output_name="$(basename "$input_file" .txt)"
  python scripts/render_generated_documents.py "$input_file" \
    --output-dir "$OUTPUT_DIR/$output_name" \
    --template 01_classic_municipal
done
```

JSON 파일을 모두 실행할 때는 `-name '*.txt'`를 `-name '*.json'`으로 바꾼다.
해당 문서 유형의 템플릿을 전부 만들려면 `--template` 줄을 제거한다. 같은
템플릿의 변주를 여러 개 만들려면 `--per-template 3`처럼 지정한다.

## 입력 규칙

- 지원 block: `paragraph`, `key_value`, `bullet_list`, `table`,
  `attachment_reference`
- `result.contract_version`과
  `result.generated_document.contract_version`은 같은 `1.x.x` 값이어야 한다.
- `blocks`가 내용의 기준이다.
- `body_text`를 같이 넣으면 blocks를 평탄화한 결과와 같아야 한다.
- 입력의 `failure`가 null이 아니면 기본적으로 거부한다.
- 결재선과 행정 처리 문구는
  `generated_document.document_metadata`에 있을 때만 렌더링한다.
- `pending`, `rejected`, `not_required` 결재 슬롯에는 도장이나 결재일을 넣지 않는다.

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

연구보고서는 표지를 포함해 최대 10쪽까지만 허용한다. 원문을 잘라 10쪽에
맞추지 않으며, 10쪽을 넘으면 해당 출력을 거부하고 manifest에 실제 쪽수를
남긴다.

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
첫 페이지에 있는지, 모든 원문 텍스트가 PDF에 남았는지, 전체가 10페이지
이하인지 확인한다. 렌더 전에 입력 복잡도와 예상 페이지 비용도 검사한다.
검증에 실패하면 해당 문서의 HTML/PDF는 출력하지 않고 `manifest.json`에
거부 상태를 남긴다.

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

## 결과 확인

각 입력의 출력 폴더에 HTML, PDF, `manifest.json`이 생긴다.
`manifest.json`에는 seed, 입력 해시, 기관명 선택, 원문 포함 검증,
합성 도장 파라미터가 기록된다. 배치 입력의 문서별 성공·실패는
`batch_manifest.json`에서 확인한다.

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
- `src/rd2/generators/official_document_rendering.py`
- `src/rd2/generators/research_report_rendering.py`
- `src/rd2/generators/press_release_rendering.py`
- `src/rd2/generators/interpretation_compilation_rendering.py`
- `src/rd2/generators/synthetic_approval_stamps.py`
- `src/rd2/generators/templates/official_variants/`
- `src/rd2/generators/templates/research_report/`
- `src/rd2/generators/templates/press_release/`
- `src/rd2/generators/templates/interpretation_compilation/`
