# PRISM(정책연구관리시스템) 수집 현황

작성일: 2026-07-07

## 스키마

| 구분 | 필드 | 비고 |
|---|---|---|
| Document 공통 | title, ordering_agency, department, disclosure_status, subject_category, body_text, body_file_path, other_file_paths, non_disclosure_reason, cso_classification, cso_sub_clause, performing_agency, start_date, end_date, source, source_url, doc_type | RD-2 16필드 + source/doc_type/other_file_paths |
| PRISM 고유값 | `doc_type="연구보고서"` 고정(어댑터 전체), `cso_sub_clause`는 숫자만("5" 등, 1~4호=C·5~8호=S가 disclosure_status보다 항상 우선) | 정보공개포털은 `doc_type="공문"` 고정 |
| 파일 API(실사로 확인) | `POST api.prism.go.kr/prism-be-asmt/v1/entire/info`(파일목록: fileSn/fileTypeCd/fileNm/fileWkky/pdfTrsfYn) → `POST .../v1/progress/download-file`(파일 바이트) | **공개(OPEN) 문서에만 호출** — 비공개 프로젝트도 실제 파일이 그대로 응답되는 걸 실사로 확인해, RD-2 스코프("C/S는 합성 문서만, 실제 기밀문서 원문 추출 금지")를 지키기 위해 코드 레벨에서 차단 |

## 예상 건수

설계 문서엔 **PRISM 단독 목표치가 없다** — O(공개) 20,000건 / C 16,000건 / S 15,000건이 **8개 출처 전체 합산 목표**고, PRISM은 그중 O트랙 "최상위 4개" 소스 중 하나 + C/S 시드 우선순위 2위로만 지정돼 있다. 소스별 배분은 아직 미정(Open Question으로 남아있음).

## 실제 수집 건수 (rd2.db 기준, 10건)

| 분류 | 건수 | 세부 |
|---|---|---|
| O (공개) | 6건 | 본문파일 다운로드 완료 |
| S (비공개, 5호) | 3건 | 조항 근거 있음, 원문 다운로드 안 함(정책상 차단) |
| S (부분공개) | 1건 | 조항 근거 없음(근사 매핑) |

## 카테고리 (subject_category = 연구분야)

일반공공행정 2건, 통일·외교 1건, 환경보호 1건, 미기재 6건(목록 페이지에 이 필드가 항상 채워지는 건 아님).

## 실제 용량 (공개 6건 기준 실측)

| 프로젝트 | 파일 수 | 용량 |
|---|---|---|
| 학교수영장 안전관리 연구 | 3 | 20.2MB |
| 적정 경찰관기동대 | 13 | 12.3MB |
| 해운대 모래축제 | 3 | 11.5MB |
| 김포시 고객만족도 | 5 | 6.9MB |
| 성과관리 지표 고도화 | 3 | 3.7MB |
| 한반도 평화 데이터 | 4 | 2.3MB |
| **합계** | **31개 파일** | **56.8MB / 평균 9.5MB/프로젝트** |

이 평균(공개 문서당 ~9.5MB)으로 단순 추정하면, PRISM이 O트랙 20,000건 중 몇 %를 맡을지에 따라 용량이 크게 갈린다 — 예: 10%(2,000건)만 맡아도 약 19GB, 25%(5,000건)면 약 47GB 규모다. 소스별 배분이 확정돼야 정확한 추정이 가능하다.
