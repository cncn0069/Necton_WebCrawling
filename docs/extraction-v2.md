# Canonical extraction v2

## 목적

원본 PDF/HWP/HWPX 하나당 재생성 가능한 canonical 추출물 하나만 저장한다. 현재
후보 탐지와 LLM에는 텍스트만 사용한다. PDF의 물리 줄 좌표와 스타일은 지금 모델에
전달하기 위한 값이 아니라, 향후 `text-only`와 `text+layout` OCR/판정 실험을 같은
원본에서 재현하기 위한 스냅샷이다.

## 저장 위치

```text
data/{source}/{doc_type}/sample.pdf
  -> data/extracted/{source}/{doc_type}/sample.pdf.json.gz

data/{source}/{doc_type}/sample.hwp
  -> data/extracted/{source}/{doc_type}/sample.hwp.json.gz
```

원본 확장자를 파일명에 남겨 같은 stem의 PDF/HWP/HWPX가 충돌하지 않게 한다. JSON은
compact UTF-8 gzip이며 같은 디렉터리의 임시 파일을 닫고 `fsync`한 다음
`os.replace`로 교체한다.

## 문서 계약

```json
{
  "schema_version": 2,
  "extraction_id": "sha256...",
  "source_sha256": "sha256...",
  "source_path": "data/moe/report/42_sample.pdf",
  "source": "moe",
  "doc_type": "report",
  "doc_id": "42",
  "source_format": "pdf",
  "extraction": {
    "profile": "layout-lite-v1",
    "extractor": "pymupdf",
    "extractor_version": "...",
    "config": {}
  },
  "status": "ok",
  "error": null,
  "quality": {
    "has_text_layer": true,
    "needs_ocr": false,
    "needs_quarantine": false,
    "pages_needing_ocr": [],
    "avg_chars_per_page": 123.4,
    "warnings": []
  },
  "pages": []
}
```

`extraction_id`는 source hash, schema/profile, extractor 이름·버전·설정의 canonical
표현에서 계산한다. 원본이나 추출 설정이 달라지면 기존 artifact를 current로 보지
않고 다시 추출한다.

## 페이지와 줄

PDF는 의미 문단으로 미리 합치지 않고 PyMuPDF의 물리 줄을 저장한다.

```json
{
  "page": 1,
  "width_pt": 595.3,
  "height_pt": 841.9,
  "rotation": 0,
  "lines": [
    {
      "line_id": 0,
      "block_id": 0,
      "order": 0,
      "text": "본문",
      "bbox_pt": [72.1, 90.2, 104.3, 102.4],
      "style_runs": [
        {
          "start": 0,
          "end": 2,
          "font": "Example",
          "size_pt": 10.0,
          "bold": false,
          "italic": false,
          "color": 0
        }
      ]
    }
  ]
}
```

좌표와 글자 크기는 0.1pt로 반올림한다. 인접한 동일 스타일 span은 하나의
`style_run`으로 합친다. `start`/`end`는 해당 line text의 문자 offset이다.

HWP/HWPX 파서는 신뢰할 수 있는 물리 페이지·좌표를 제공하지 않으므로 논리 page 1,
`width_pt`/`height_pt`/`rotation`/`bbox_pt = null`, `style_runs = []`로 저장한다.
향후 OCR 결과는 이 값을 꾸며내지 않고 `extraction_id`에 연결된 별도 결과로 추가한다.

`length`, `cleaned_text`, `is_boilerplate`, 후보 점수·순위처럼 원본과 규칙에서 다시
계산할 수 있는 값은 canonical artifact에 저장하지 않는다.

## 하위 파이프라인

```text
extract_documents.py
  -> data/extracted/*.json.gz + _run_manifest.json.gz
  -> find_candidates.py (한 번 load, in-memory annotate, 한 번 traversal)
  -> data/candidates/*.jsonl + _manifest.json
  -> run_llm_augment.py / generate_cs_pilot.py
```

후보는 물리 줄을 삭제하거나 바꾸지 않고 필요할 때만 같은 block의 wrapped line을
메모리에서 파생 segment로 합친다. 후보 레코드는 `candidate_id`, `extraction_id`,
`source_path`, `line_ids`, `text`, `text_sha256`, `page`를 가진다. bbox/style은 후보에
복제하지 않는다.

후보와 생성 단계는 manifest status/run ID, extraction ID, line text/hash를 검증한다.
부분 run은 기본 차단되며 명시적인 진단용 override가 있어야 소비할 수 있다. 현재 LLM
프롬프트에는 `candidate_id`와 `text`만 포함하며 좌표·스타일·해시는 보내지 않는다.

후보 JSONL은 전역 산출물이므로 extraction manifest의 `artifacts + failures`가 현재
전체 원본 코퍼스를 설명할 때만 발행한다. `--source moe` 같은 범위 제한 추출은
artifact 확인용이며, 후보 발행 전에는 `extract_documents.py --source all`을 다시
실행해야 한다. 진단용 partial override도 이 범위 검증은 우회하지 않는다.

`data/annotated/` 사본과 `data/structured/` 단계는 기본 파이프라인에 없다.
`extract_structured_documents.py`는 표 추출 조사용 선택 도구로만 남긴다. 운영용 표
결과가 필요해지면 본문을 다시 저장하지 않는 `extraction_id` 기반 sidecar로 만든다.
