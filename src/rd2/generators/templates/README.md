# PDF templates

문서 골격과 본문 형식은 Jinja2 HTML partial, 인쇄 레이아웃은
`document.css`에서 편집한다. 브라우저에서 생성된 HTML을 열면 같은 CSS로
미리볼 수 있으며, PDF 변환은 Playwright Chromium을 사용한다.

`forms/`는 실제 원본에서 확인한 문서 외곽 형식(표준 시행공문, 정책 참고자료,
회의록)을 나누고, `bodies/`는 공문·보고·계획·품의·통보 등 문서유형별 본문
계층을 나눈다. 각 템플릿의 원본 경로와 참조 범위는
`TEMPLATE_SOURCE_MAP.md` 및 샘플 manifest에 기록한다.

개발/배포 환경에서 프로젝트 의존성을 설치한 뒤 Chromium을 한 번 설치한다.

```text
python -m playwright install chromium
```

`NotoSansKR-Regular.ttf`와 `NotoSansKR-Bold.ttf`는 PDF와
`security_mark.py`가 함께 쓰는 번들 폰트다. 실제 기관 로고나 관인 이미지는
템플릿에 추가하지 않는다.
