# 조항×세부조항×문서유형별 내용 민감 포인트 매트릭스

Companion to: `docs/design-coverage-matrix-diversity-audit-20260723.md`,
`docs/design-evidence-first-confidentiality-pipeline-20260720.md`

## 목적

`template_matrix.py`의 `_GROUPS`(61개 `(clause_no, subclause_key, doc_type)` leaf)는
"이 셀이 어떤 세부조항·문서유형에 속하는가"만 정의하고, 그 안에 실제로 어떤
내용을 채울지는 정의하지 않는다. diversity-audit 설계의
`semantic_scope_key = classification|subclause_key|doc_type`가 정확히 이 leaf
단위로 유사도를 비교하므로, leaf마다 내용이 구별되지 않으면 그 스코프 안
수십~수백 건이 이름·날짜만 다른 사실상 동일 문서가 되어 semantic duplicate
cluster로 잡힌다.

## 구조 — doc_type별 공통 증강 포인트 (Part A) + leaf별 핵심 지정 (Part B)

leaf마다 독립된 서사 시나리오를 쓰는 방식은 처음 요청("공문 → 내부 검토의견,
사업일정, 담당자 연락처" 같은 장르→포인트 목록)과 맞지 않았다. `personnel`
doc_type을 "시나리오"가 아니라 "양식에 포함된 필드"로 다시 쓴 교훈이 전체에
적용됐어야 한다.

**Part A**는 doc_type 16개 각각에 공통으로 등장하는 증강 포인트를 정의한다 —
명사구 단위이고, subclause와 무관하게 그 문서유형이면 자연스럽게 나타나는
화제/필드다. `meeting_minutes`를 쓰는 leaf가 1~8호에 걸쳐 9번 나오는데, 이
leaf들 전부 같은 포인트 목록을 공유하고 subclause는 그중 "어떤 포인트가 이
문서를 비공개로 만드는 핵심 사유인가"만 지정한다(Part B).

제보자(whistleblower) 신원보호는 의도적으로 어느 doc_type의 공통 포인트에도
넣지 않았다 — 조사 결과 실제 법적 근거가 공익신고자보호법이라 정보공개법상
인용은 1호이고, 5호 감사·6호 개인정보의 "공통 포인트"로 일반화하면 안 된다.
1호 leaf에서만 전용 포인트로 다룬다.

### Part A — doc_type별 공통 증강 포인트

| doc_type | 공통 포인트 |
|---|---|
| official_document | 협조요청 내용, 처분 근거, 첨부자료 유무, 수신·발신 기관, 비공개 처리 표시 |
| policy_material | 정책방향, 시행일정, 예산배정 규모, 이해관계자 의견수렴 결과 |
| report | 조사·점검 경위, 원인분석(미확정), 피해·실적 규모, 향후계획, 참고자료 첨부 |
| meeting_minutes | 참석자 의견, 의사결정 과정, 미확정 안건, 표결·보류 결과, 반대의견 |
| press_release | 배포 전 초안, 내부 Q&A 대응, 표현 수위 조율, 배포 시점 |
| plan | 추진일정, 예산계획, 대상지·대상자 선정기준, 시행 전 협의사항 |
| audit_result | 조사대상, 지적사항, 시정요구, 증빙자료 목록, 처분 검토의견 |
| bid_notice | 예정가격, 입찰조건, 참여제한 사유, 규격사양, 낙찰기준 |
| approval | 검토의견, 결재라인 이견, 승인절차, 계정정보·접근권한 부여, 예산집행 승인 |
| bid_renotice | 재공고 사유, 유찰원인, 조건변경 내역 |
| public_offering | 심사기준, 심사위원 명단, 평가점수, 선정결과 이의 |
| pre_spec_notice | 규격사양, 업계 의견수렴, 특정업체 유불리 |
| personnel | 인사평가 등급, 승진후보자 명단, 징계검토 내역, 채용전형 결과, 연봉·성과급 등급, 발령사항 |
| notice | 시행일정, 이해관계자 반발 예상, 공표범위 |
| interpretation_compilation | 해석사례, 상충되는 유권해석, 소급적용 여부 |
| reply_notification | 민원인 정보, 처리결과 요지, 근거법령, 사실관계 확인내용 |

`audit_result`에 "제보자"를 넣지 않은 것과 별개로, 감사 문서 본문에 제보자가
**부수적으로 언급되는 것 자체는 허용**한다 — 다만 "제보자라서 비공개"가
그 문서의 핵심 사유로 앵커링되면 안 되고, 핵심 사유는 항상 위 표의
`audit_result` 포인트(조사대상/지적사항/시정요구/증빙자료) 중 하나여야 한다.

### Part B — leaf별 핵심 포인트 지정

doc_type별로 묶었다(Part A와 대조하기 쉽도록). "핵심"은 그 leaf가 비공개인
**대표 이유**로 앵커링할 포인트, "부수"는 같은 문서에 곁들여 나와도 되지만
대표 이유로 앵커링하면 안 되는 포인트다. leaf 하나에 핵심이 여러 개면(예:
personnel의 5호/6호 쌍) 같은 필드를 서로 다른 관점(절차적 공정성 vs 신원)으로
쓴다는 뜻이다.

**official_document** (9 leaf)
- 1호 legal_secret — 핵심: 비공개 처리 표시(타 법령상 비밀 지정 근거) · 부수: 첨부자료 유무
- 2호 security_defense — 핵심: 협조요청 내용(안보 협조) · 부수: 수신·발신 기관(보안등급)
- 3호 life_body — 핵심: 처분 근거(안전조치명령) · 부수: 협조요청 내용
- 3호 property — 핵심: 처분 근거(재해보상 처분) · 부수: 첨부자료 유무(손해사정서)
- 4호 trial_investigation — 핵심: 협조요청 내용(수사협조, 사건번호) · 부수: 비공개 처리 표시
- 5호 audit_inspection — 핵심: 처분 근거(감사결과 통보) · 부수: 첨부자료 유무
- 6호 welfare_pii — 핵심: 첨부자료 유무(수급자격 심사 증빙, 개인정보 포함) · 부수: 수신·발신 기관
- 7호 technology_patent — 핵심: 협조요청 내용(기술이전 협상) · 부수: 비공개 처리 표시(영업비밀)
- 8호 cornering — 핵심: 협조요청 내용(물자수급 관계기관 협조) · 부수: 처분 근거(가격정책)

**policy_material** (5 leaf)
- 1호 legal_secret — 핵심: 정책방향(법령상 비밀정보 활용) · 부수: 이해관계자 의견수렴
- 2호 security_defense — 핵심: 정책방향(안보정책, 전력배치) · 부수: 시행일정(미도래)
- 3호 life_body — 핵심: 정책방향(시설 취약점 포함 안전정책) · 부수: 예산배정 규모
- 7호 business_strategy — 핵심: 정책방향(경영전략, 시장점유 목표) · 부수: 이해관계자 의견수렴
- 8호 real_estate_speculation — 핵심: 시행일정(용도변경 시행 전) · 부수: 정책방향

**report** (13 leaf)
- 1호 legal_secret — 핵심: 참고자료 첨부(법령상 비밀 원자료) · 부수: 조사·점검 경위
- 2호 unification_diplomacy — 핵심: 조사·점검 경위(협상 동향) · 부수: 향후계획
- 3호 life_body — 핵심: 원인분석(미확정, 사고원인) · 부수: 피해·실적 규모
- 3호 property — 핵심: 피해·실적 규모(재산피해 산정) · 부수: 원인분석
- 4호 trial_investigation — 핵심: 조사·점검 경위(수사 진행상황) · 부수: 원인분석
- 4호 correction_security — 핵심: 조사·점검 경위(형집행 실태점검) · 부수: 향후계획
- 5호 audit_inspection — 핵심: 원인분석(위반사항 초안, 미확정) · 부수: 조사·점검 경위
- 5호 technology_development — 핵심: 향후계획(R&D 진행상황) · 부수: 참고자료 첨부
- 6호 personnel_pii — 핵심: 조사·점검 경위(인사조사, 개인식별정보 포함) · 부수: 원인분석
- 7호 technology_patent — 핵심: 참고자료 첨부(특허출원 전 기술내용) · 부수: 향후계획
- 7호 security_diagnosis — 핵심: 원인분석(보안취약점) · 부수: 피해·실적 규모(피해 시뮬레이션)
- 8호 real_estate_speculation — 핵심: 원인분석(부지선정 미확정 타당성조사) · 부수: 피해·실적 규모(지가변동 예측)
- 8호 cornering — 핵심: 피해·실적 규모(가격변동 예측) · 부수: 조사·점검 경위(매점매석 의심)

**meeting_minutes** (9 leaf)
- 1호 legal_secret — 핵심: 제보자 신원보호(**공익신고자보호법 인용**), 징계위원회 회의록·위원명단 비공개(**공무원징계령 제20·21조 인용**, 2026-07-23 검색 검증) 또는 미확정 안건(비밀정보 접근권한 심의) — 셋 다 1호 전용, Part A 공통목록엔 없음 · 부수: 표결·보류 결과
- 2호 unification_diplomacy — 핵심: 의사결정 과정(협상전략 논의) · 부수: 참석자 의견
- 3호 life_body — 핵심: 참석자 의견(책임소재 논쟁) · 부수: 표결·보류 결과
- 4호 trial_investigation — 핵심: 의사결정 과정(수사대책 논의) · 부수: 미확정 안건
- 5호 audit_inspection — 핵심: 표결·보류 결과(지적사항 확정 전) · 부수: 반대의견
- 5호 decision_review — 핵심: 미확정 안건(의사결정 과정 자체 — 이 doc_type의 정의상 표준형) · 부수: 참석자 의견, 반대의견
- 6호 subject_pii — 핵심: 참석자 의견(조사대상자 신원 언급, 제보자 아님) · 부수: 의사결정 과정
- 7호 ma_terms — 핵심: 의사결정 과정(협상전략) · 부수: 반대의견(상대방 요구조건 이견)
- 8호 real_estate_speculation — 핵심: 미확정 안건(용도변경 확정 전) · 부수: 반대의견

**press_release** (3 leaf)
- 2호 security_defense — 핵심: 표현 수위 조율(안보 사안 표현) · 부수: 배포 시점
- 3호 life_body — 핵심: 배포 전 초안(피해규모 확정 전) · 부수: 배포 시점
- 5호 decision_review — 핵심: 내부 Q&A 대응(발표 대응문안 — 이 doc_type의 표준형) · 부수: 배포 전 초안

**plan** (4 leaf)
- 2호 unification_diplomacy — 핵심: 시행 전 협의사항(비공개 협상 일정) · 부수: 추진일정
- 3호 property — 핵심: 예산계획(보상 예산 미확정) · 부수: 대상지·대상자 선정기준
- 4호 correction_security — 핵심: 대상지·대상자 선정기준(특별계호 대상자) · 부수: 시행 전 협의사항
- 8호 real_estate_speculation — 핵심: 대상지·대상자 선정기준(부지 미확정) · 부수: 추진일정

**audit_result** (1 leaf — 유일 사용처라 5개 포인트 대부분 복합사유로 조합 가능)
- 5호 audit_inspection — 핵심: 지적사항(확정 전 초안) · 부수: 증빙자료 목록, 처분 검토의견

**bid_notice** (2 leaf)
- 5호 bid_contract — 핵심: 예정가격(입찰 사전정보) · 부수: 참여제한 사유
- 7호 unit_cost — 핵심: 규격사양(원가·납품단가 산출근거와 결합) · 부수: 낙찰기준

**approval** (6 leaf)
- 4호 prosecution — 핵심: 검토의견(공소제기 여부) · 부수: 승인절차
- 5호 bid_contract — 핵심: 승인절차(계약체결 결재) · 부수: 검토의견(업체평가)
- 5호 decision_review — 핵심: 결재라인 이견(예산·정책 이견), 계정정보·접근권한 부여(이 doc_type의 표준형, 7호 아님) · 부수: 승인절차
- 6호 welfare_pii — 핵심: 승인절차(복지급여 지급 승인, 수급자 신상정보) · 부수: 검토의견
- 7호 ma_terms — 핵심: 검토의견(M&A 조건) · 부수: 결재라인 이견
- 8호 cornering — 핵심: 예산집행 승인(가격정책 승인) · 부수: 승인절차

**bid_renotice** (1 leaf — 유일 사용처)
- 5호 bid_contract — 핵심: 재공고 사유(특정업체 문제) · 부수: 유찰원인, 조건변경 내역

**public_offering** (1 leaf)
- 5호 bid_contract — 핵심: 심사기준·심사위원 명단(확정 전) · 부수: 평가점수, 선정결과 이의

**pre_spec_notice** (1 leaf)
- 5호 bid_contract — 핵심: 규격사양(경쟁사 대비 민감 항목) · 부수: 특정업체 유불리

**personnel** (2 leaf — 물리적으로 같은 양식, 관점만 다름)
- 5호 personnel_management — 핵심: 인사평가 등급·승진후보자 명단(절차적 공정성 관점, 확정 전 — 징계검토는 1호로 이동, 아래 참고) · 부수: 채용전형 결과, 연봉·성과급 등급
- 6호 personnel_pii — 핵심: 같은 필드에 담긴 개인식별정보(성명·주민등록번호, 신원 관점) · 부수: 발령사항의 성명

**notice** (1 leaf)
- 5호 decision_review — 핵심: 시행일정(공표 시점 미확정) · 부수: 이해관계자 반발 예상

**interpretation_compilation** (1 leaf)
- 5호 decision_review — 핵심: 상충되는 유권해석(미확정 조율) · 부수: 소급적용 여부

**reply_notification** (2 leaf)
- 4호 prosecution — 핵심: 처리결과 요지(수사결과 요지, 불기소 이유) · 부수: 근거법령
- 6호 petitioner_pii — 핵심: 민원인 정보(성명·연락처·주소) · 부수: 사실관계 확인내용

이걸로 61개 leaf 전부 Part A 포인트 풀에서 핵심/부수가 배선됐다. 같은
doc_type을 공유하는 leaf끼리 비교하면 핵심이 겹치지 않는다(단, `personnel`은
의도적으로 같은 필드를 다른 관점에서 재사용, `audit_result`/`bid_renotice`
등 단일 사용처 doc_type은 여러 포인트를 한 leaf 안에서 복합사유로 조합).

## 다음 단계

이 매트릭스를 `src/rd2/generators/doc_templates.py`(`DocTemplateSpec` —
body_format, forbidden_phrases, approval_state)와 `pdf_render.py`(실제
body_format 렌더링 분기)에 반영한다. 기존 템플릿(T5-4, T6-1 등)은 실물 원본
근거가 없어 의도적으로 좁게 설계돼 있으므로, 이 매트릭스의 포인트를 그대로
새 body_format으로 추가하기 전에 evidence-first 설계 문서의
`provisional`/`source_backed`/`approved` 게이트를 따를지부터 정한다.
