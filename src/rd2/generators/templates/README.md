# PDF templates

문서 골격과 본문 형식은 Jinja2 HTML partial, 인쇄 레이아웃은
`document.css`에서 편집한다. 브라우저에서 생성된 HTML을 열면 같은 CSS로
미리볼 수 있으며, PDF 변환은 Playwright Chromium을 사용한다.

개발/배포 환경에서 프로젝트 의존성을 설치한 뒤 Chromium을 한 번 설치한다.

```text
python -m playwright install chromium
```

`NotoSansKR-Regular.ttf`와 `NotoSansKR-Bold.ttf`는 PDF와
`security_mark.py`가 함께 쓰는 번들 폰트다. 실제 기관 로고나 관인 이미지는
템플릿에 추가하지 않는다.
