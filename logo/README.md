# 대외비·군사기밀 표지 자산

최신 `doc_type`별 생성 파이프라인은 템플릿 본문 PDF를 만든 뒤
`src/rd2/generators/document_security_marking.py`에서 대외비 또는 군사기밀
표지를 적용한다.

## 적용 조건

- `generation_target.classification == "C"`이면 단색 대외비 보안 스킨을
  적용한다.
- 여기에 `generation_target.military_secret_grade`가 `1급`, `2급`, `3급` 중
  하나이면 등급별 군사기밀 앞표지와 본문 표시를 더한다.
- 기관명과 정부부처 로고는 적용 여부나 등급을 판단하는 데 사용하지 않는다.
- S/O 문서에는 아무 표지도 추가하지 않는다.
- 외부 입력에서 로고 파일명이나 경로를 받지 않는다.

등급 값은 문서 내용을 보고 추론하지 않는다. 생성 단계에서 정한
`military_secret_grade`를 본문 생성 프롬프트와 PDF 후처리에 동일하게 전달한다.

## PDF 합성 순서와 페이지 수

```text
템플릿 HTML/PDF 본문
  -> 본문 최대 12페이지 절단
  -> C 문서 단색 보안 스킨 또는 군사기밀 중립 프레임 적용
  -> 본문 각 면 상·하단 중앙에 등급표시
  -> 맨 앞에 등급별 표지 1장 추가
  -> 선택적 손글씨·이미지 전용 스캔 후처리
```

등급이 없는 C 문서는 앞표지를 추가하지 않고 본문 페이지 수를 그대로 유지한다.
`selection_seed`로 10종 보안 스킨을 재현 가능하게 선택하며, 여러 결과를 한 번에
렌더링하면 출력 순서대로 다음 스킨을 순환한다.

표지는 본문 페이지 제한에 포함하지 않는다. 본문이 12페이지면 최종 PDF 파일은
표지 1장과 본문 12장으로 총 13장이지만, manifest의 `actual_pages`와
`retained_page_count`는 본문 기준 12를 유지한다. `security_marking`에는
`content_page_count`와 `final_pdf_page_count`를 별도로 기록한다.

## 사용 자산

| 등급 | 앞표지 도안 | 본문 상·하단 표시 | 표지 색 |
|---|---|---|---|
| 1급 | `1급_비밀_표지.png` | `1급_비밀.png` | 적색 |
| 2급 | `2급_비밀_표지.png` | `2급_비밀.png` | 황색 |
| 3급 | `3급_비밀_표지.png` | `3급_비밀.png` | 청색 |

표지 도안은 A4 새 페이지 중앙에 17cm 너비로 배치한다. 원본 흑백 도안을
고해상도로 확대한 뒤 등급 색으로 변환해 삽입한다. 본문 표시 위치는 한 문서의
모든 본문 페이지에서 동일하다. 본문 페이지 전체를 원래 비율 그대로 상·하단
30pt, 좌우 12pt 안전영역 안에 맞춰 넣은 뒤 바깥 여백에 스킨과 등급표시를
배치하므로, 헤더가 빽빽한 템플릿이나 장문 페이지에서도 본문을 가리지 않는다.
표지 페이지에는 작은 상·하단 표시를 중복해 넣지 않는다.

## 가상 영문 스탬프

다음 PNG는 실제 기관 표지가 아닌 단색 합성 자산이다. 이미지와 최종 PDF에
`VIRTUAL SAMPLE` 문구를 남긴다. 다른 등급 문구는 생성하지 않는다.

```text
synthetic_confidential.png
```

## 사용하지 않는 자산

다음 기관 이미지는 현재 생성·PDF 후처리 로직에서 사용하지 않는다. 기관명 매핑,
중앙 워터마크, 좌상단 레터헤드, `agency_marking` manifest 필드는 모두 제거했다.

```text
감사원.png
검찰.png
고위공직자범죄수사처.png
국방부.png
국정원.png
대통령경호처.png
대통령실.svg
청와대.svg
정부부처.png
```

파일 자체는 과거 산출물 재현과 이력 보존을 위해 삭제하지 않는다.

## 결과 메타데이터

```json
{
  "security_marking": {
    "kind": "military_secret",
    "asset": "logo/2급_비밀.png",
    "asset_kind": "military_grade_mark",
    "cover_asset": "logo/2급_비밀_표지.png",
    "military_secret_grade": "2급",
    "security_template": {
      "slug": "military_neutral_frame",
      "name": "군사기밀 중립 프레임",
      "english_label": "",
      "layout": "classic_register",
      "stamp_asset": ""
    },
    "palette": {
      "mode": "monochrome_dark",
      "ink_hex": "#22272C"
    },
    "placement": {
      "strategy": "front_cover_top_bottom_and_neutral_frame",
      "cover": {
        "position": "before_content",
        "page_count": 1,
        "counted_in_page_limit": false,
        "width_cm": 17.0
      },
      "body": {
        "strategy": "reserved_security_frame",
        "safe_top_bottom_pt": 30.0,
        "military_mark_strategy": "top_bottom_center"
      }
    },
    "content_page_count": 12,
    "final_pdf_page_count": 13,
    "overlay": true
  }
}
```
