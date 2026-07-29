# 생성 문서 PDF 렌더링 사용법

`result.generated_document`가 들어 있는 JSON을 Jinja2 + WeasyPrint 공문
템플릿으로 렌더링한다.

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
10종 템플릿을 전부 만들려면 `--template` 줄을 제거한다. 같은 템플릿의 변주를
여러 개 만들려면 `--per-template 3`처럼 지정한다.

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

사용 가능한 템플릿 slug:

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
- `src/rd2/generators/synthetic_approval_stamps.py`
- `src/rd2/generators/templates/official_variants/`
