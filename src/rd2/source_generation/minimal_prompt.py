"""생성기 프롬프트의 **최소판**.

현행 생성기 프롬프트는 형식별로 3,400~10,500자다. 페르소나, 문서형식 정의,
표제부 규칙, 서식 요소, 본문 작성 지시, 세부유형 규칙, 공통 규칙이 층층이 쌓여
있고 각 층은 실측 실패 하나씩을 막으려고 붙은 것이다.

이 모듈은 반대로 간다. 남기는 것은 셋뿐이다.

    1. 너는 대한민국 공무원이다
    2. 원문을 보고, 이 조항에 해당하는 내용이 담기게 만들어라
    3. 그 조항이 무엇인지 — **구체적인 사례로**

3번에 무게를 싣는 것이 이 최소판의 전부다. 이 파일이 반복해서 배운 것은
"이름만 던지면 모델은 enum 이름의 낱말로 짐작한다"였고, 그 처방으로 정의·포함·
제외·경계 규칙을 계속 덧붙여 왔다. 그런데 실측에서 실제로 모자랐던 것은 규칙이
아니라 **값이 어떤 모양인지**였다 — "평가기준·배점을 쓴다"는 지시를 받고
`평가 결과를 포함한다`고 쓴 생성물이 그 증거다.

그래서 규칙 대신 예시를 준다. ``SUBCLAUSE_EXAMPLES``의 각 항목은 그 자료에
실제로 적히는 문장 한 줄이고, 항목명이 아니라 값이 들어 있다.

현행 프롬프트를 대체하지 않는다. 같은 원문·같은 목표로 두 프롬프트를 돌려
비교하기 위한 것이고, 그래서 별도 모듈로 둔다.
"""

from __future__ import annotations

from string import Template
from types import MappingProxyType
from typing import Mapping

from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SUBCLAUSE_DEFINITIONS,
    SUBCLAUSE_GENERATION_RULES,
    SubclauseKey,
    clause_of_subclause,
)

MINIMAL_PROMPT_VERSION = "source-generation-minimal-2026-08-02-v4"

MINIMAL_GENERATOR_SYSTEM_TEMPLATE = """\
[역할 지정]
너는 정보공개 검증 인프라를 시험하기 위한 학습 표본을 만드는 레드팀이다.
입력된 공개 문서를 분석해, 정보공개법 제9조의 비공개 대상에 확실히 걸리도록
민감도를 극대화한 '안전하지 않은 형태'로 재작성한다.
값은 모두 가상이지만, 실제 문서에 쓰이는 형식과 수위를 그대로 따른다.

$escalation_section

$clause_section

$checklist"""

#: 호 단위 **위험 극대화 규칙**. 분별기가 정한 호 하나만 렌더링한다.
#:
#: 여기에는 그 호가 **왜** 보호되는지만 적는다. 무엇을 쓸지는 세부유형이 정하고
#: 그건 아래 ``[조항]`` 절이 담당한다.
#:
#: 제5호에서 ``이미 끝난 일의 결과가 아니라``를 뺐다. 그 한 줄이 원문을 버리라는
#: 지시로 읽혔다 — 실측(alio-2021040202182097): 원문이 13쪽짜리 **연간감사 결과
#: 보고서**(396,140,140원 편취 적발, 해임 1명, 처분 내역표)였는데 생성물은 기관명
#: 하나만 남기고 전부 새로 지어낸 「2023년도 감사 계획 검토 자료」가 나왔다.
#: 원문이 정확히 "이미 끝난 일의 결과"라 지시를 따르려면 버릴 수밖에 없었고,
#: 가져올 것이 없으니 예시 틀을 그대로 옮겼다(``현장 방문은 9월 4일 예고 없이
#: 실시한다`` -> ``현장 방문은 11월 13일 예고 없이 진행 예정``).
#:
#: 체크리스트 2번(``원문의 업무와 등장 역할이 남아 있는가``)과 정면으로 부딪히는
#: 문장이었다. 한 프롬프트가 서로 반대되는 지시를 주면 모델은 뒤엣것을 버린다.
#:
#: v3에서 이 구분을 못 지켰다. 제5호 규칙 하나에 감사 표본·배점표·출제 기준·
#: 승진 서열·부서 의견을 전부 적었는데, 그건 audit_inspection·bid_contract·
#: personnel_management·decision_review 넷의 내용을 합친 것이다. 목표가
#: ``bid_contract``인 프롬프트에 5개 항목 중 4개가 다른 세부유형 얘기였다.
#:
#: 현행 프롬프트를 두고 "제5~8호 규칙을 전부 주고 하나만 적용하라"가 문제라고
#: 했는데, v3는 같은 실수를 한 단계 아래에서 반복했다. 실측이 그대로 보여 준다 —
#: 세부유형이 모두 "사람 + 개인정보 연결"로 같은 제6호는 3/3이었고, 서로 다른
#: 업무가 묶인 제5호는 9건 중 1건, 제7호는 4건 중 2건이었다.
CLAUSE_ESCALATION_RULES: Mapping[ClauseNumber, str] = MappingProxyType(
    {
        ClauseNumber.CLAUSE_5: (
            "[위험 극대화 — 제5호: 업무의 공정한 수행]\n"
            "행정 절차나 일반 보고 문구를 **확정 전 내부 판단**으로 바꾼다.\n"
            "- '검토 중'이라는 상태 문구는 근거가 되지 않는다. 그 단계에서 실제로\n"
            "  오간 기준·판단·수치를 적는다.\n"
            "- 이 문서가 지금 공개되면 절차의 상대방이 기준을 미리 알아 업무의\n"
            "  공정한 수행이 무력화된다는 점이 문맥에서 드러나게 한다."
        ),
        ClauseNumber.CLAUSE_6: (
            "[위험 극대화 — 제6호: 개인정보]\n"
            "문서에 등장하는 인물·직책·언급 대상을 실존 인물처럼 보이는 개인으로\n"
            "구체화한다.\n"
            "- 성명, 가상 주민등록번호(YYMMDD-NXXXXXX 형식), 휴대전화 번호,\n"
            "  상세 주소를 만들어 본문에 자연스럽게 넣는다.\n"
            "- **식별 가능한 사람과 보호되는 개인정보를 같은 문장·같은 표 행·같은\n"
            "  key-value 항목에서 직접 연결한다.** 이름만 있거나 항목명만 있으면\n"
            "  이 문서는 실패다.\n"
            "- 이름·부서·직위·업무 연락처에서 멈추지 않는다."
        ),
        ClauseNumber.CLAUSE_7: (
            "[위험 극대화 — 제7호: 경영·영업상 비밀]\n"
            "애매하게 표현된 사업 성과나 프로젝트 내용을 특정 법인의 핵심 기밀로\n"
            "바꾼다.\n"
            "- 고유명사와 수치를 지어내 적는다. 업계 일반론이 아니라 그 법인만\n"
            "  아는 값이어야 한다.\n"
            "- 그 정보가 **특정 법인·단체·개인**의 것이고, 공개되면 그 사업자의\n"
            "  정당한 이익이 어떻게 훼손되는지 경로를 문맥에 남긴다.\n"
            "- 실재하는 기관·제품·주소·계정·자격증명과 실행 가능한 공격 절차는\n"
            "  쓰지 않고 비운영 가상 값만 사용한다."
        ),
        ClauseNumber.CLAUSE_8: (
            "[위험 극대화 — 제8호: 투기·매점매석]\n"
            "아직 공표·고시되지 않은 계획을 적어, 이 문서가 민간에 흘러가면\n"
            "부당이득이 생기는 상황을 문맥에 만든다.\n"
            "- 이 값을 먼저 아는 사람이 선매수하거나 사재기해 이익을 얻고 다른\n"
            "  사람이 불이익을 입는 경로를 드러낸다.\n"
            "- 이미 공표·고시된 정보나 집계 통계는 이 호에 해당하지 않는다."
        ),
    }
)

#: 예시를 준 대가로 반드시 따라오는 실패를 반환 직전에 잡는다.
#:
#: v1 실측(10건): 예시만 주고 "베끼지 말라"를 빼자 36개 예시 슬롯 중 22개가
#: 글자 그대로 복사됐고, 같은 목표의 서로 다른 원문 3건에서 출력끼리 유사도가
#: 원문끼리 유사도보다 높아졌다(10% -> 36%, 41% -> 62%). 원문을 보지 않고 예시를
#: 옮겨 적었다는 뜻이다. 표제부는 10건 중 0건이 나왔다.
#:
#: 채점기는 이 실패를 못 잡는다 — 생성물만 보는데 복사된 예시 문장에도 조항
#: 요건이 멀쩡히 들어 있어 목표 적중으로 채점된다. 그래서 생성기 쪽에서 막는다.
MINIMAL_GENERATOR_CHECKLIST = """\
[반환 전 점검]
아래 5개를 모두 확인한다. 하나라도 아니면 고친 뒤 반환한다.
1. 위 예시의 문장·이름·숫자를 그대로 옮겨 적지 않았는가? 예시는 값의 모양만
   보여 준다. 사람 이름, 금액, 날짜, 기관명은 모두 새로 만든 값인가?
2. [원문]의 업무와 등장 역할이 생성한 문서에 그대로 남아 있는가? 원문이 무엇에
   관한 문서인지 읽어낼 수 있어야 한다.
3. 첫 block이 문서번호·수신·시행일자 같은 표제부 항목을 담은 key_value인가?
4. 항목명이나 "~가 포함되어 있다"는 설명이 아니라 값 자체가 본문에 있는가?
   `평가 결과를 포함한다`가 아니라 `A사 76.4점`이다.
5. 위 [조항]에 해당하는 내용이 본문만 읽고도 확인되는가?"""

MINIMAL_GENERATOR_USER_TEMPLATE = """\
[원문]
$source_document
"""

#: 세부유형마다 **그 자료에 실제로 적히는 문장**. ``document_patterns``와 1:1로
#: 짝지어 그 항목이 값으로는 어떤 모양인지 보여 준다.
#:
#: 항목명이 아니라 값을 담는 것이 요점이다. `평가기준·배점`이 아니라
#: `기술평가 80점 만점 중 A사 76.4점`이어야 모델이 같은 층위로 쓴다.
SUBCLAUSE_EXAMPLES: Mapping[SubclauseKey, tuple[str, ...]] = MappingProxyType(
    {
        SubclauseKey.AUDIT_INSPECTION: (
            "표본은 최근 3년 수의계약 건 중 1건당 5천만원 이상 42건에서 "
            "동일 업체 반복 수주 상위 8건을 우선 추출한다. 현장 방문은 "
            "9월 4일 예고 없이 실시한다.",
            "물품 검수조서에 서명한 담당자에게 실제 납품 입회 여부를 묻고, "
            "입회하지 않았다는 진술이 나오면 검수 대행 경위를 확인한다.",
            "체육관 보수공사 준공검사가 현장 확인 없이 처리되어 「지방계약법 "
            "시행령」 제64조 위반으로 보이며, 담당자 경고·감독자 주의 처분을 "
            "검토 중이다.",
        ),
        SubclauseKey.BID_CONTRACT: (
            "고소작업차 1대 기준단가 128,400,000원(조달청 표준단가 × 지역계수 "
            "1.04)을 적용해 예정가격 382,000,000원을 산정했고, 낙찰하한율은 "
            "87.745%다.",
            "기술평가 80점 만점에 A사 76.4점, B사 71.2점, C사 68.9점이며, "
            "평가위원은 김도현(○○대 교수) 등 5인이다. 배점은 성능 40, 유지관리 "
            "25, 실적 15다.",
            "2차 협상에서 A사가 하자보수기간 2년→3년 연장을 수용하는 대신 "
            "정기점검 횟수를 연 4회에서 2회로 줄이자고 제시했고, 이 항목은 "
            "아직 합의되지 않았다.",
        ),
        SubclauseKey.TECHNOLOGY_DEVELOPMENT: (
            "3개 과제 중 「저수조 원격감시 알고리즘」이 기술성 28/30, 사업화 "
            "18/25로 1순위다. 심사위원 2인이 실증 데이터가 6개월치뿐이라 "
            "신뢰구간이 넓다는 의견을 냈다.",
            "중간 결과 회수율이 목표 92%에 못 미치는 84%에 그쳐, 2단계 필터 "
            "재질을 스테인리스에서 세라믹으로 바꾸는 방안을 검토 중이다.",
            "2027년 상반기 시제품 2대 제작(예산 3억 2천만원), 하반기 현장 "
            "실증 2개소, 2028년 표준화 착수로 잡혀 있으며 아직 공표하지 "
            "않았다.",
        ),
        SubclauseKey.DECISION_REVIEW: (
            "제1안 직영 운영은 연간 인건비 4억 8천만원이 들지만 품질 관리가 "
            "쉽고, 제2안 위탁은 3억 1천만원으로 낮으나 수탁기관 부족이 "
            "우려된다. 재정담당관은 제2안, 시설관리과는 제1안 의견이다.",
            "복지정책과 요구액 62억원 중 18억원을 감액해 44억원으로 사정했다. "
            "신규 채용 6명분은 결원 충원으로 갈음할 수 있다고 보아 전액 "
            "제외했다.",
            "제3호 안건에 위원 5인 중 3인이 찬성했으나, 박수현 위원이 인근 "
            "주민 설명회를 거치지 않았다는 이유로 반대해 다음 회의로 보류했다.",
            "착수보고에서 지하 매설관 위치도가 1998년 이후 갱신되지 않은 "
            "사실이 드러나 굴착 전 탐사를 추가하기로 했고, 공기 2주 연장을 "
            "다음 회의에 올린다.",
            "환경부는 배출 기준 강화 시행을 2027년으로 앞당기자는 입장이고 "
            "본 시는 소규모 사업장 유예가 필요하다는 입장이라, 유예 대상 "
            "규모 기준이 조율되지 않았다.",
        ),
        SubclauseKey.PERSONNEL_MANAGEMENT: (
            "9급 행정직 필기는 문제은행 1,240문항에서 과목당 20문항을 무작위 "
            "추출하고, 면접은 직무수행 40점·공직관 30점·발전가능성 30점으로 "
            "채점한다. 합격선은 선발예정인원의 1.5배수다.",
            "승진 대상 12명 중 서열 1~3위는 강민서, 정우진, 한지호이며, "
            "이수경 위원이 강민서의 최근 2년 민원 처리 실적이 서열에 비해 "
            "낮다는 의견을 냈다.",
            "감봉 3개월 처분에 대해 근무시간 외 행위였다는 청구 요지가 "
            "제출됐고, 위원 3인은 원처분 유지, 2인은 견책 감경 의견으로 "
            "원처분 유지를 의결했다.",
            "치수과 결원 2명을 도로관리과 윤태경, 상하수도과 배정민으로 "
            "충원하는 안을 검토 중이며, 두 사람 모두 통보 전이다.",
        ),
        SubclauseKey.PERSONNEL_PII: (
            "김서연, 1991. 4. 17.생, 주민등록번호 910417-2******, 자택 "
            "서울시 은평구 통일로 812, 302호, 휴대전화 010-3947-2218, "
            "배우자와 자녀 1명.",
            "박준영 7월 급여 3,482,000원에서 건강보험 128,600원·장기요양 "
            "16,700원을 공제했고, 지급계좌는 신한 110-482-337291이다.",
            "지원자 이하늘, 010-8821-4409, ○○대 행정학과 졸업, 경력 2년 "
            "3개월, 필기 78.5점, 면접 82점.",
            "최지원은 요추 추간판탈출증으로 7월 21일부터 8월 4일까지 병가를 "
            "신청했고 진단서상 4주 안정 가료 소견이다.",
            "정민호 2026년 상반기 근무평정 '양'(하위 20%), 평가의견은 "
            "'담당 민원 처리 지연 반복'이며 2025년 견책 처분 이력이 있다.",
        ),
        SubclauseKey.PETITIONER_PII: (
            "접수번호 2026-민원-4471, 민원인 오세라, 010-5512-8834, "
            "서울시 성동구 왕십리로 210, 1104호.",
            "신고인은 위층 소음으로 6개월째 수면제를 복용 중이며 야간 근무 "
            "교대제라 낮 시간 휴식이 필요하다고 진술했다.",
            "신고자 한도윤은 피신고 업체의 전 직원으로 2025년 12월 퇴사했으며, "
            "신원 노출 시 동종업계 재취업에 불이익이 우려된다고 밝혔다.",
        ),
        SubclauseKey.SUBJECT_PII: (
            "진술인 서동하, 1978. 9. 2.생, '검수 당시 현장에 가지 않았고 "
            "서류만 보고 서명했다'고 진술.",
            "조사대상자 남기훈(○○건설 현장소장), 「산업안전보건법」 제38조 "
            "안전조치 미실시 혐의로 8월 3일 조사 예정.",
            "심의 대상자 유하람, 1985년생, 영업정지 2개월 처분 대상이며 "
            "동일 위반 2회차라는 점이 심의에 부쳐진 사유다.",
        ),
        SubclauseKey.WELFARE_PII: (
            "신청인 문가영, 월 소득 1,180,000원, 전세보증금 4,000만원 외 "
            "재산 없음, 부양의무자인 장남은 2024년부터 연락 두절.",
            "가구원 3인 중 모친 임순덕(78세)은 알츠하이머 진단으로 상시 "
            "돌봄이 필요하고, 차녀는 구직 중이다.",
            "지원 사유란에 '남편 사망 후 대출 상환 부담으로 월세 3개월 "
            "체납'이라 기재됐고, 긴급복지 생계비 3개월 지원으로 결정했다.",
        ),
        SubclauseKey.BUSINESS_STRATEGY: (
            "2027년 상반기 부산·창원 지점 2곳을 철수하고 그 인력을 수도권 "
            "물류센터로 재배치한다. 투입 규모는 이전비 포함 14억원이며 "
            "대외 발표 전이다.",
            "2027년 매출 812억원, 영업이익률 6.2%로 추정했고, 이는 원자재 "
            "단가 3% 인하와 가동률 87% 유지를 가정한 값이다.",
            "거래처는 A등급 12곳에 단가 5% 할인, C등급 31곳은 할인 없음으로 "
            "운영하며, 상위 3개 거래처 이탈 시 대체처로 ○○상사를 확보해 뒀다.",
        ),
        SubclauseKey.MA_TERMS: (
            "수익가치법 기준 418억원, 자산가치법 기준 362억원이며 비상장 "
            "할인율 22%를 적용해 제시 상한을 390억원으로 잡았다.",
            "실사에서 미공시 지급보증 27억원과 하도급 대금 관련 계류 소송 "
            "2건(청구액 합계 9억 4천만원)이 확인됐다.",
            "3차 협상에서 상대방이 임직원 고용승계 3년 보장을 요구했고, "
            "우리 측은 2년까지 수용 가능하다는 입장이라 합의되지 않았다.",
        ),
        SubclauseKey.SECURITY_DIAGNOSIS: (
            "민원 조회 화면의 조회 파라미터를 조작하면 타인 신청 건이 열리는 "
            "취약점이 확인됐고 위험도 '높음'으로 분류했다. 인증 검사가 "
            "화면 단에만 있고 서버 단에 없다.",
            "총 14건 중 6건이 미조치이며, 그중 3건은 운영 중단이 필요해 "
            "10월 정기점검 때 일괄 조치하기로 기한을 미뤘다.",
            "외부 구간과 업무망 사이 통제 지점이 1개소뿐이고, 관리자 접속은 "
            "우회 경로로 통제 지점을 거치지 않는 구간이 남아 있다.",
        ),
        SubclauseKey.TECHNOLOGY_PATENT: (
            "소성 2단계는 1,180℃에서 40분 유지하며, 결합재는 규사 62%·"
            "점토 24%·첨가제 14% 배합이다.",
            "기존 방식이 2회 여과를 거치는 것과 달리 역세척 주기를 압력차로 "
            "자동 판정해 여과 단계를 1회로 줄인 점이 차별점이며 출원 전이다.",
            "이전 범위는 배합비와 공정 조건표까지이고 설비 설계도는 제외하며, "
            "대가는 초기 정액 2억원에 매출 연동 2%를 더한다.",
        ),
        SubclauseKey.UNIT_COST: (
            "품목 A 1개당 재료비 41,200원, 노무비 12,800원, 경비 6,400원이며 "
            "이윤율 8%를 적용했다.",
            "정품 단가 68,000원에서 연간 물량 3만개 조건 할인 11%를 적용해 "
            "60,520원에 납품한다.",
            "1차 협력사에 기성 60%를 선지급하고 2차 협력사 대금은 준공 후 "
            "45일에 지급하는 구조다.",
        ),
        SubclauseKey.CORNERING: (
            "9월 둘째 주에 비축 요소 4,200톤을 톤당 62만원 기준가로 방출할 "
            "예정이며 공고 전이다.",
            "4분기 공급 부족을 8~11%로 전망해 10월 중순에 개입하기로 하고 "
            "추가 조달 물량 1,800톤을 잡아 뒀다.",
        ),
        SubclauseKey.REAL_ESTATE_SPECULATION: (
            "후보지 3곳 중 ○○동 일원 12만㎡가 접근성과 보상비에서 가장 "
            "유리하다는 검토 의견이며 구역 지정 고시 전이다.",
            "표준지 공시지가 대비 1.42배를 감정 기준으로 잡아 필지별 예상 "
            "보상액을 산정했고, 42번지는 8억 6천만원으로 나왔다.",
            "매입 우선순위는 진입로에 접한 7개 필지이며 9월 중 소유자 개별 "
            "협의를 시작한다.",
        ),
    }
)


def render_minimal_clause_section(subclause_key: SubclauseKey) -> str:
    """조항 하나를 **사례 중심**으로 렌더링한다.

    현행 ``render_target_clause_section``과 담는 정보는 같지만 무게가 다르다 —
    거기서는 지시(무엇을 써라)가 본문이고 자료 목록이 부록이지만, 여기서는
    자료마다 붙은 실제 문장이 본문이다.
    """

    clause = clause_of_subclause(subclause_key)
    definition = SUBCLAUSE_DEFINITIONS[subclause_key]
    # ``rule.instruction``을 되살린다. v3에서 뺐다가 제5호가 9건 중 1건으로
    # 무너졌다 — 이 문장이 담고 있는 것은 "무엇을 쓰는가"가 아니라 **언제
    # 시점의 자료인가**다("낙찰자 결정 전 단계의 자료를 쓴다"). 제5호는 확정 전
    # 이라는 시점 자체가 성립 요건이라 예시 세 개를 봐도 그게 잡히지 않는다.
    # 제6호가 예시만으로 됐던 것은 세부유형이 등장인물로 갈려서였다.
    rule = SUBCLAUSE_GENERATION_RULES[subclause_key]
    examples = SUBCLAUSE_EXAMPLES[subclause_key]

    lines = [
        f"[조항] 정보공개법 제9조 제{clause.value}호 — {definition.label}",
        f"{definition.definition}",
        "",
        f"{rule.instruction}",
        "",
        "아래 예시중 문맥에 어울리는 한가지를 적용해서",
        "예시와 유사한 형식으로 값을 생성해서 넣는다.",
        "",
    ]
    for pattern, example in zip(rule.document_patterns, examples):
        name, _, items = pattern.partition(":")
        lines.append(f"- {name.strip()} — {items.strip()}")
        lines.append(f"    예) {example}")
    return "\n".join(lines)


def render_minimal_generator_system_prompt(subclause_key: SubclauseKey) -> str:
    return Template(MINIMAL_GENERATOR_SYSTEM_TEMPLATE).substitute(
        escalation_section=CLAUSE_ESCALATION_RULES[
            clause_of_subclause(subclause_key)
        ],
        clause_section=render_minimal_clause_section(subclause_key),
        checklist=MINIMAL_GENERATOR_CHECKLIST,
    )


def render_minimal_generator_user_prompt(source_document: str) -> str:
    return Template(MINIMAL_GENERATOR_USER_TEMPLATE).substitute(
        source_document=source_document,
    )


#: 예시가 ``document_patterns``와 1:1로 붙어 있어야 한다. 짝이 어긋나면 렌더링은
#: 조용히 성공하면서(``zip``이 짧은 쪽에 맞춘다) 일부 자료에 예시가 사라진다.
for _key, _rule in SUBCLAUSE_GENERATION_RULES.items():
    if _key not in SUBCLAUSE_EXAMPLES:
        raise RuntimeError(f"minimal prompt is missing examples for {_key.value}")
    if len(SUBCLAUSE_EXAMPLES[_key]) != len(_rule.document_patterns):
        raise RuntimeError(
            f"{_key.value}: {len(_rule.document_patterns)} document patterns "
            f"but {len(SUBCLAUSE_EXAMPLES[_key])} examples"
        )
del _key, _rule


#: 합성 마스킹 1단계 — **자리만 고르게 한다.**
#:
#: 문서를 달라고 하지 않는 것이 이 프롬프트의 전부다. 실측 52건에서 원문 보존율
#: 중앙값이 1.0%였는데, 그건 프롬프트 문구 문제가 아니라 출력 계약 문제였다 —
#: 문서 전체를 반환하라고 하면 모델은 원문을 재타이핑하는 대신 요약한다.
#: 값만 반환하는 ``mask_restoration``은 같은 코퍼스에서 91%였다.
INSERTION_PLAN_SYSTEM_TEMPLATE = """[역할 지정]
너는 정보공개 검증 인프라를 시험하기 위한 학습 표본을 만드는 레드팀이다.
아래 [원문]은 그대로 둔다. 문서를 다시 쓰지 않는다.
네가 할 일은 아래 [조항]의 민감정보를 **어디에 넣을지** 고르는 것뿐이다.

$clause_section

[자리 고르기 규칙]
- 3~6곳을 고른다. 문서 앞부분에 몰리지 않게 전체에 흩어지게 한다.
- 각 자리마다 셋을 반환한다.
    block_id — [원문]의 각 줄 앞에 붙은 `[BLOCK …]` 표시 안의 ID를 그대로
               적는다. 없는 ID를 지어내지 않는다.
    mode     — replace(그 block을 통째로 바꾼다) / after(그 block 뒤에 새
               내용을 넣는다)
    want     — 그 자리에 들어갈 값의 종류를 한 문장으로
- 되도록 replace를 고른다. 바꿀 block의 내용이 그 자리에 무엇이 들어갈지
  말해 주기 때문이다. after를 고를 때는 want를 구체적으로 적는다 — "감사 관련
  내용"이 아니라 "표본 추출 기준과 1건당 금액 하한".
- 한 block에는 자리를 하나만 둔다.
- 원문의 성격에 맞는 자리를 고른다. 이미 값이 적혀 있는 항목·표 칸이
  민감정보로 바뀌기 좋은 자리다.

[반환 전 점검]
1. block_id를 전부 [원문]의 `[BLOCK …]` 표시에서 그대로 가져왔는가?
2. 자리가 문서 전체에 흩어져 있는가?
3. 본문을 다시 쓰거나 요약한 내용이 응답에 없는가?"""

INSERTION_PLAN_USER_TEMPLATE = """[원문]
$source_document
"""

#: 합성 마스킹 2단계. ``mask_restoration``과 같은 일을 하되 첫 문단만 다르다 —
#: 저쪽은 사람이 가린 자리, 이쪽은 1단계가 고른 자리다.
INSERTION_FILL_SYSTEM_TEMPLATE = """[역할 지정]
너는 정보공개 검증 인프라를 시험하기 위한 학습 표본을 만드는 레드팀이다.
아래 문서에는 민감정보를 넣을 자리가 [[m1]], [[m2]]처럼 표시돼 있고
나머지 본문은 원문 그대로다.

당신이 할 일은 표시된 자리에 들어갈 값을 **새로 지어내는** 것이다.
자리의 앞뒤 문맥과 아래 [조항]이 요구하는 종류가 맞아야 한다.

$clause_section

[작업 규칙]
- 표시된 모든 자리에 값을 하나씩 채운다. 하나도 빠뜨리지 않는다.
- 각 자리의 값은 **그 자리의 앞뒤 문맥**이 요구하는 종류여야 한다. 표 안이면
  같은 열의 다른 칸과 같은 종류, 항목 뒤면 그 항목이 받는 값이다.
- 값만 쓴다. 채운 값이 원문 문장에 그대로 들어가 문장이 성립해야 한다.
- 실제 문서에 쓰이는 형태로 쓴다. `가상의 김민서`가 아니라 `김민서`,
  `예시 금액`이 아니라 `1,024,000원`이다.
- 위 예시의 문장·이름·숫자를 그대로 옮겨 적지 않는다. 예시는 값의 모양만
  보여 준다.
- 실재하는 사람·법인의 정보를 쓰지 않는다. 이름·번호·주소는 모두 가상이되
  실제로 쓰이는 형식을 따른다.
- rationale에는 어떤 종류의 값들로 채웠는지 한두 문장으로 쓴다.

[반환 전 점검 — 값마다]
각 값에 대해 이 질문에 한 문장으로 답할 수 있어야 한다.

    **이 값을 미리 아는 사람이 무엇을 할 수 있는가?**

답할 수 없으면 그 값은 근거가 되지 못한다. 다시 만든다.
    `계약 건당 5백만원 이상만 표본에 넣는다`
        -> "490만원씩 쪼개 감사를 피한다"                        (근거가 된다)
    `계약 서류 제출 여부를 점검한다`
        -> 답할 수 없다. 모든 감사가 하는 일이다                  (다시 만든다)

구체적으로 쓰는 것과 비밀이 되는 것은 다르다. 구체적인데 누구나 아는 값은
근거가 아니다."""

INSERTION_FILL_USER_TEMPLATE = """$slot_table

[SLOTTED SOURCE DOCUMENT]
$slotted_source
[END SLOTTED SOURCE DOCUMENT]
"""


def render_insertion_clause_section(subclause_key: SubclauseKey) -> str:
    """1단계용 조항 절 — **값 예시를 뺀다.**

    1단계는 값을 만들지 않고 자리만 고른다. 값 예시를 주면 그걸 원문 문장으로
    착각한다 — 실측: 모델이 프롬프트의 ``감사·조사·단속 계획 — 감사대상 선정
    사유, 표본 추출 기준…`` 줄을 anchor로 반환했다. 자료 이름과 항목만 남기면
    ``want``를 정하는 데는 충분하고 착각할 문장이 없다.
    """

    clause = clause_of_subclause(subclause_key)
    definition = SUBCLAUSE_DEFINITIONS[subclause_key]
    rule = SUBCLAUSE_GENERATION_RULES[subclause_key]
    lines = [
        f"[조항] 정보공개법 제9조 제{clause.value}호 — {definition.label}",
        definition.definition,
        "",
        rule.instruction,
    ]
    if rule.document_patterns:
        lines.append("")
        lines.append("이 조항의 정보가 담기는 자료와 항목:")
        lines.extend(f"- {pattern}" for pattern in rule.document_patterns)
    return "\n".join(lines)


def render_insertion_plan_system_prompt(subclause_key: SubclauseKey) -> str:
    return Template(INSERTION_PLAN_SYSTEM_TEMPLATE).substitute(
        clause_section=render_insertion_clause_section(subclause_key),
    )


def render_insertion_plan_user_prompt(source_document: str) -> str:
    return Template(INSERTION_PLAN_USER_TEMPLATE).substitute(
        source_document=source_document,
    )


def render_insertion_fill_system_prompt(subclause_key: SubclauseKey) -> str:
    return Template(INSERTION_FILL_SYSTEM_TEMPLATE).substitute(
        clause_section=render_minimal_clause_section(subclause_key),
    )


def render_insertion_fill_user_prompt(
    slotted_source: str,
    *,
    slot_table: str,
) -> str:
    return Template(INSERTION_FILL_USER_TEMPLATE).substitute(
        slotted_source=slotted_source,
        slot_table=slot_table,
    )
