"""C트랙(1~4호) `기관 × 세부조항` 템플릿과 사건 프레임 전개.

**왜 필요한가.** 5~8호는 실제 원문에서 seed를 뽑아 엔트로피를 외부에서 조달한다
(``seed_assembly.build_sensitive_seed``). 1~4호는 rd2 DB에 해당 기관(국정원·국방부·
검찰 계열) 문서가 0건이라 그 통로가 없고, 사람이 손으로 적은
``clause_data.CLAUSES[n].scenario_prompts`` 10~14장이 전부다. 조항당 4000건을
만들면 ``(시나리오 × 기관 × 문서유형)``이 4호 기준 96조합뿐이라 같은 프롬프트를
42회 반복하게 된다 — ``seed_assembly`` 모듈 주석이 기록한 실패(무관한 두 원문이
똑같이 ``428,000,000원``을 뱉음)가 그대로 재현되는 규모다.

**두 번째 이유.** ``clause_data``에는 ``subclause`` 개념이 아예 없고,
``classification_taxonomy.SUBCLAUSE_GENERATION_RULES``/``SUBCLAUSE_PERSONA_CONTEXT``는
5~8호 전용이다(``_GENERATABLE_CLAUSES``). C트랙 세부조항 8개는 "무엇인지"(판정용
``SubclauseDefinition``)만 있고 **"어떻게 쓰라"는 규칙이 없다.** 그 결과 세부조항이
학습 라벨인데도 생성 배치에서 세부조항 분포를 통제할 수 없다. 이 모듈이 그
빈자리를 채운다.

**인물·시간을 값으로 바꾸지 않는다.** 성명을 `김철수`→`이영희`로, 일자를
`2024-03-15`→`2023-11-02`로 바꾸면 조합 수는 늘지만 문서 내용은 한 글자도 바뀌지
않는다 — near-duplicate 탐지에 전부 같은 문서로 잡힌다. 그래서 슬롯은 값이 아니라
**구조**를 흔든다: 인물은 ``역할구성``(누가 누구와 함께 다루는가), 시간은
``진행단계``(확정 전인가 시행 후인가)로 둔다. 둘 다 본문 구성 자체를 바꾼다.
구체적인 성명·일자·금액은 생성기가 만든다 — ``seed_assembly``가 "구체적 값은 넣지
않는다"고 정한 것과 같은 이유다.

**프롬프트는 이 모듈이 통째로 만든다.** 이전 C 생성 프롬프트(``c_only_generation``,
삭제됨)는 사건 프레임이 없던 시절 것이라 ``CLAUSES[n].scenario_prompts[0]``을
무조건 첫 번째로 박았다 — 교정 보안 문서에 "대형 경제범죄 압수수색 계획"이 딸려
들어가고, 그 한 줄이 4000건 전부에 똑같이 박힌다. 여기서는 그 하드코딩을 버리고,
그쪽에만 있던 문체·기호체계 지시만 가져와 한 벌로 합쳤다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterator, Mapping

from rd2.disclosure.military_secret import (
    MILITARY_SECRET_GRADE_LABELS,
    MILITARY_SECRET_GRADES,
    is_military_secret_agency,
)

from rd2.disclosure.clause_data import CLAUSES
from rd2.source_generation.classification_taxonomy import (
    DOCUMENT_FORM_DEFINITIONS,
    SUBCLAUSE_DEFINITIONS,
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
    clause_of_subclause,
)
from rd2.source_generation.document_form import header_keys_for

#: 템플릿·전개·프롬프트 규칙의 버전. 슬롯 구성이나 프롬프트 문구가 바뀌면 올린다 —
#: planner policy hash에 실려 과거 계획을 무효화한다.
C_TRACK_TEMPLATE_VERSION = "c-track-template-v2"

#: 3단계(분석-작성-기록) 접두사의 버전. 템플릿과 따로 매기는 이유는 이쪽이
#: **대체가 아니라 대조군**이기 때문이다 — ``minimal_prompt``가 현행 프롬프트를
#: 두고 별도 모듈로 선 것과 같다. 같은 템플릿·같은 case_index로 두 접두사를
#: 돌려 무엇이 달라지는지 보려면 버전이 각자 움직여야 한다.
#: v2에서 골격을 갈아엎었다. v1은 단일 출력 접두사에 ``[출력 순서]`` 절 하나를
#: 얹은 것이었다 — 지시는 여전히 종류별 절로 쌓여 있고 3단계는 맨 뒤 한 절이
#: 가리키기만 했다. v2는 절 자체를 단계로 세우고 각 단계 끝에 그 단계가 채우는
#: 필드를 적는다. ``#역할``·``#과제``가 앞에 서고 ``[조건]``이 user로 간다.
#:
#: v3은 **부정문을 대체문으로** 바꿨다. "A를 적지 않는다"가 전부 "A 자리에 B를
#: 적는다"가 됐고, 질문 1의 주어가 행위자에서 보호 대상으로 옮겨갔다.
#:
#: **이것은 검증된 처방이 아니라 실험의 한 갈래다.** 근거가 실호출 2건이고 그
#: 둘도 변경 두 개가 한꺼번에 들어가 교락돼 있다. 대조 실험(arm당 N≥5)을 돌리기
#: 전까지 이 판이 v2보다 낫다고 말할 근거는 없다 — 방향에 대한 논거만 있다.
C_TRACK_COT_PROMPT_VERSION = "c-track-cot-2026-08-05-v3"

#: 비군사기관 문서의 등급 표기. ``is_military_secret_agency``가 참이면
#: ``MILITARY_SECRET_GRADES``(Ⅰ~Ⅲ급)를 쓴다.
_CONFIDENTIAL_GRADE = "대외비"

_GRADE_GUIDANCE: Mapping[str, str] = MappingProxyType(
    {
        _CONFIDENTIAL_GRADE: (
            "표제부에 '대외비' 표기를 넣는다. 기관 내부와 협의 기관까지만 도는 "
            "수준의 민감도로 쓴다."
        ),
        "1급": (
            "누설되면 국가안전보장에 극히 중대한 위해가 생기는 수준으로 쓴다. "
            "취급자를 지정 인원으로 한정하는 문구를 넣는다."
        ),
        "2급": "누설되면 국가안전보장에 막대한 지장을 주는 수준으로 쓴다.",
        "3급": "누설되면 국가안전보장에 손해를 주는 수준으로 쓴다.",
    }
)

#: 진행단계 축과 그 단계가 본문에서 무엇으로 나타나야 하는지.
#:
#: ``CaseSlot``이 아니라 여기 있는 이유는 ``SubjectCase.allowed_stages``가
#: 문서마다 허용 단계를 좁히기 때문이다. 슬롯으로 두면 모든 문서가 네 단계를
#: 다 받는다 — 실측(2026-08-03): ``수용자 이송 승인 품의``(승인받기 전 문서)에
#: ``시행 결과 보고``(이미 끝난 단계)가 붙었고, 모델이 구체적 지시가 달린 단계
#: 쪽을 따라 결재란이 있어야 할 품의가 결과보고서로 나왔다. ``(사안, 형식)``을
#: 묶어 없앤 충돌과 같은 종류다.
STAGE_GUIDANCE: Mapping[str, str] = MappingProxyType(
    {
        "검토 착수": (
            "① 무엇이 있었기에 검토가 시작됐는지 경위를 시간 순으로 적고 → "
            "② 그래서 무엇이 문제인지 짚고 → ③ 앞으로 확인할 항목을 나열한다. "
            "결론은 아직 내지 않는다."
        ),
        "확정 전 내부 검토": (
            "① 검토한 대안을 안별로 제시하고 → ② 각 안의 장단점과 소요를 "
            "비교하고 → ③ 어느 부서가 어느 안에 왜 찬성·반대·보류했는지 적고 → "
            "④ 아직 정해지지 않은 쟁점으로 끝낸다. 결재란은 비워 둔다."
        ),
        "시행 중간점검": (
            "① 당초 계획을 먼저 다시 적고 → ② 현재 진행률을 그것과 대비시키고 → "
            "③ 차질이 난 항목과 그 원인을 짚고 → ④ 조정이 필요한 부분을 적는다."
        ),
        "시행 결과 보고": (
            "① 당초 목표를 먼저 적고 → ② 실제 결과 수치를 그것과 대비시키고 → "
            "③ 목표와 어긋난 항목의 원인을 짚고 → ④ 남은 과제로 끝낸다."
        ),
    }
)

#: 승인·계획 계열은 확정 전 단계까지만, 보고·점검 계열은 시행 이후 단계만.
_PRE_DECISION_STAGES = ("검토 착수", "확정 전 내부 검토")
_POST_ACTION_STAGES = ("시행 중간점검", "시행 결과 보고")

#: 제목 지시.
#:
#: ``- 문서: 수용자 이송 실시 결과보고``만 주면 모델이 그 문자열을 제목으로
#: 그대로 베낀다 — 실측(2026-08-03) 10건 중 8건이 ``document_name``과 완전히
#: 같았고 ``수용자 이송 실시 결과보고``가 3번 나왔다. 4000건이면 같은 제목이
#: 수백 개가 된다. ``generate.py``가 같은 실패를 이미 기록했다: "title을
#: scenario 원문 그대로 쓰던 이전 방식(같은 scenario_index가 여러 건에 반복되면
#: title 컬럼이 통째로 동일 문자열이 되는 문제 — 사용자 지적)".
#:
#: 문서명은 **종류**일 뿐이고 제목은 그 종류 안의 한 건을 가리켜야 한다.
_TITLE_RULE = """[제목]
- [문서 정보]의 `문서`에 적힌 이름은 문서의 **종류**이지 제목이 아니다. 그
  문자열을 제목으로 그대로 쓰지 않는다.
- [다루는 사건]의 조건(대상시설·촉발계기·진행단계)이 드러나는 제목을 짓는다.
  연도나 차수처럼 이 건을 특정하는 표현을 넣어 다른 건과 구별되게 한다.
- '[합성]', '(가상)', 'AI 생성' 같은 라벨은 절대 넣지 않는다."""

#: 삭제된 ``c_only_generation``에만 있던 문체·기호체계 지시.
#: 그쪽의 "3. 확인 사항"(화이트리스트·조항 분포를 확인하라)은 가져오지 않았다 —
#: 모델이 확인할 수 있는 대상이 아니라 개발자 메모다.
#:
#: 절 이름은 ``[문체]``다. ``[문서형식]``이던 때는 문서 **구조**를 다루는
#: ``render_generator_form_section``의 ``[이 문서의 형식: ...]`` 절과 나란히 서서
#: 같은 것을 가리키는 이름 둘이 다른 얘기를 했다.
_DOCUMENT_STYLE_RULES = """[문체]
1. 문체 및 종결어미:
   - 철저히 개조식(Bullet points) 문체로 작성한다.
   - 문장의 끝은 반드시 명사형 종결("~음", "~슴", "~임", "~함") 또는 "~것"으로 맺는다.
   - 미사여구나 감정적 표현, 근거 없는 수식어는 모두 제거하고 수치와 사실 중심으로 쓴다.
2. 공공기관 표준 기호 체계 준수:
   - 다음 순서의 기호 체계를 지켜 단락과 하위 항목을 구성한다.
     □ (대항목 - 현황, 문제점, 개선방안 등)
       ○ (중항목 - 핵심 내용)
         - (소항목 - 세부 사실 및 근거)
           ※ (참고/주의사항 - 보충 설명)"""


#: 위 기호 위계를 **어느 블록에 담을지**. 문체만 정해 두면 모델은 개요 전체를
#: ``paragraph.text`` 하나에 개행으로 이어 붙인다 — 실측(2026-08-04, case 141):
#: 11개 블록 중 단락 하나가 개행 9개짜리 471자였다. HTML은 개행을 공백으로
#: 접고 ``official_variants`` 계열 CSS에는 ``white-space: pre-line``이 없어서,
#: PDF에서 □·○·- 위계가 통째로 사라진 통글이 됐다.
#:
#: 계약은 이미 이걸 예상하고 있었다 — ``contracts.BULLET_MARKERS``는 "C트랙
#: 프롬프트가 위계를 요구하므로 생성기가 낸 ``items``에 기호가 이미 붙어
#: 있다"고 적고, ``render_bullet_item``은 그래서 기호를 벗기지 않는다. 빠져
#: 있던 것은 **그 items에 담으라는 말** 하나였다.
_BLOCK_STRUCTURE_RULES = """[출력 구조]
- 개요를 한 블록의 text에 개행으로 이어 붙이지 않는다. 개행은 렌더링에서 공백으로
  접혀 위계가 사라진다.
- □ 대항목 한 줄은 paragraph 블록 하나로 낸다. text에는 그 한 줄만 담는다.
- 그 아래 ○·-·※ 줄들은 뒤따르는 bullet_list 블록 하나에 담는다. 줄 하나가
  items 항목 하나이며, 각 항목은 자기 기호(○, -, ※)와 들여쓰기를 그대로 달고
  있어야 한다 — 기호가 곧 깊이다.
- 따라서 □ 절 하나마다 paragraph 1개 + bullet_list 1개가 짝으로 나온다.
- 표제부(문서번호·수신·시행일자 등)는 key_value 블록으로 낸다."""


#: 단일 출력(``GeneratedDocumentIR``) 경로의 ``[비공개 근거]`` 절.
#:
#: 3단계 경로에는 싣지 않는다. 이 절이 요구하는 것을 ``SecurityAnalysis``의
#: 두 필드가 그대로 받고, 같은 말이 두 층에 서면 성긴 쪽을 지우는 것이
#: ``minimal_prompt`` v9가 호 단위 절을 뺀 근거였다.
_NONDISCLOSURE_BASIS = (
    "[비공개 근거]\n"
    "공개 시 이 세부유형의 보호 대상에 생길 구체적인 지장이 본문에서 "
    "확인되게 한다. 어떤 정보가 누구에게 알려지면 무엇이 무력화되는지 "
    "경로가 드러나야 하며, 근거 없이 '민감함'·'대외비임'으로 끝내지 않는다."
)

#: 3단계 프롬프트의 1단계 절.
#:
#: 단계를 **절 이름으로** 세우고 각 절 끝에 그 단계가 채우는 필드를 적는다.
#: 산문으로 "먼저 분석하라"고만 쓰지 않는 이유는 ``gateway.parse``가 이미
#: 스키마를 보내고 있어서다 — 손으로 쓴 JSON 예시를 프롬프트에 얹으면 두 계약이
#: 어긋나고 모델이 그 사이에서 흔들린다. 순서는 스키마가 잡고, 절은 각 단계에
#: **무엇을** 적을지만 말한다.
#:
#: 질문 세 개는 ``[비공개 근거]`` 절이 하던 말을 받는다. 절이었을 때는 가리킬
#: 대상이 없어 추상적으로 끝났지만, 질문이 되면 답이 필드에 남고 2단계가 그
#: 답을 가리킬 수 있다.
#: 질문 1의 주어가 v3에서 바뀌었다. ``아래 각자가 무엇을 할 수 있는가``였다.
#:
#: 실호출 2건 대조(2026-08-05)에서 나온 것이다. 템플릿의 ``what_they_gain``을
#: 소독한 방법이 **행위자를 지우거나 자동사로 돌리기**였는데("통제가 겨냥한
#: 연락이 끝나 버린다"), 질문이 "각자가 무엇을 **할 수** 있는가"라 지운 것을
#: 문법적으로 되돌렸다 — 주어가 행위자면 서술어 자리가 타동사를 요구한다.
#: 실측: 문장 종결형이 v1의 결과 프레임 4:0에서 v2의 능력 프레임 0:4로 완전히
#: 뒤집혔고, 네 항목이 전부 "기획할·선택할·조정하거나 …할 수 있게 됨"이 됐다.
#:
#: **소독이 비운 자리를 질문이 도로 요구했다.** 그래서 금지 문장을 더 붙이지
#: 않고 질문의 주어를 바꾼다.
_COT_STAGE_1_HEAD = """[단계 1: 세부조항 취약점 분석]
문서를 쓰기 전에 세 가지를 먼저 답한다.
1. 이 문서가 아래 각자에게 알려지면 이 기관의 무엇이 무력화되는가?"""

#: v3에서 부정문을 전부 **대체문**으로 바꿨다. "A를 적지 않는다" 대신
#: "A 자리에 B를 적는다"다.
#:
#: v2의 금지 문장("통제가 어느 조건에서 느슨해지는지, 무엇을 악용·은폐하면
#: 되는지를 적지 않는다")은 효과가 없었다 — 실호출 대조에서 그 낱말들이 v1과
#: 같은 횟수로 남았고, 정작 새로 생긴 마커(``우회``)는 프롬프트에 없는 낱말이라
#: 프라이밍으로도 설명되지 않는다. 금지는 **쓰지 말 것**을 말할 뿐 **쓸 것**을
#: 주지 않아서, 모델이 형식만 따르고 층위는 그대로 뒀다(v2 분석의 머리글이
#: "유출 시 행위자별 악용 경로와 결과임"으로 금지어와 요구어를 한 라벨에
#: 붙였다).
#:
#: 대체문은 빈자리를 남기지 않는다. 종결형까지 지정하는 것이 요점이다 —
#: 실측에서 드리프트가 어휘가 아니라 **문형**에서 났으므로 문형을 준다.
_COT_STAGE_1_TAIL = """   각 항목은 그 사람이 하는 일 자리에 **우리 쪽에서 성립하지 않게 되는 것**을
   적는다. 문장은 "…가 무력화된다" 또는 "…가 성립하지 않게 된다"로 끝낸다.
2. 그중 어느 정보가 공개될 때 이 세부조항의 보호 대상에 가장 큰 피해를 주는가?
3. 그래서 [조건]에 주어진 조항 적용이 타당한가?
   '민감함'·'대외비임' 자리에 위 [대상 조항]의 **판정 기준 문구**를 놓고, 이
   문서의 어느 값이 그 문구에 걸리는지로 답한다.
→ 1·2의 답을 security_analysis.vulnerability에, 2·3의 답을
  security_analysis.impact에 적는다."""

#: 지어낼 것과 실재하는 것의 경계.
#:
#: ``minimal_prompt``의 역할 지정에 있는 문장("실재하는 기관·제품·주소·계정·
#: 자격증명과 실행 가능한 공격 절차는 쓰지 않는다")을 그대로 옮길 수 없다.
#: 저쪽은 공개 원문을 변형하므로 기관을 지워도 되지만, C트랙은 **기관과 시설이
#: 템플릿 축 자체**다 — ``agency``·``departments``·장소 슬롯이 전부 실존명이고,
#: 그래야 부처별 문체와 조직 맥락이 표본에 남는다(2026-08-05 사용자 결정).
#:
#: 그래서 금지 목록이 아니라 **경계선**으로 쓴다. 조직은 실재하고 그 안을 채우는
#: 값은 전부 가상이다. 감사(2026-08-05)가 지적한 두 위험이 이 문장으로 막힌다 —
#: 실존 자산에 대한 위험 판정 기록의 형식(제3호 ``property``), 실존 양자 관계에
#: 대한 위조 외교 기록의 형식(제2호 ``unification_diplomacy``).
#: v3에서 부정문을 대체문으로 바꾸고 **예시 리터럴을 뺐다.**
#:
#: v2의 ``"○○ 장비 1식"처럼``이 생성 본문에 따옴표까지 그대로 두 번 복사됐다
#: (실호출 2026-08-05: "설비 항목은 '○○ 장비 1식' 단위로 교체 계획 수립
#: 필요함"). ``minimal_prompt``가 같은 실패를 이미 기록해 뒀다 — 예시를 주고
#: "베끼지 말라"를 빼자 36개 슬롯 중 22개가 글자 그대로 복사됐다. 저쪽은 반환 전
#: 점검으로 막았지만, 여기서는 애초에 **베낄 문자열을 두지 않는** 쪽을 택한다.
#: 무엇을 적을지는 종류로 말하면 충분하다.
_BOUNDARY_RULE = """[경계]
기관·부서·시설은 실재하는 이름을 쓴다. 그 안을 채우는 값은 전부 새로 지어낸다.
- 실존 인물 자리에 새로 지어낸 사람을 넣는다. 성명·직위·연락처 모두 가상이다.
- 계정·인증 체계·장비 제원·주파수·좌표 자리에는 그 값 대신 **수량과 용도**만
  적는다.
- 실재하는 시설·자산·상대국의 실제 판정·합의 내용 자리에는 이 문서가 다루는
  **가상의 건**을 적는다.
- 실행 절차·수법·회피 방법 자리에는 **무엇이 무력화되는지**를 적는다. 위험한
  것이 무엇인지는 값으로 남기고, 어떻게 하는지는 문서가 말하지 않는다."""

#: 2단계 절의 머리. 뒤에 ``template.instruction``·``[제목]``·``[문체]``·
#: ``[출력 구조]``가 이어 붙는다.
#:
#: **문체·구조 규칙을 지우지 않는 이유.** 이 셋은 프롬프트 취향이 아니라 출력
#: 계약의 일부다 — ``[출력 구조]``를 빼면 개조식 위계가 ``paragraph.text`` 하나에
#: 뭉쳐 PDF에서 통글이 되고(실측 2026-08-04, case 141: 개행 9개짜리 471자),
#: ``[제목]``을 빼면 제목이 ``document_name``과 같아진다(실측 2026-08-03, 10건 중
#: 8건). 골격은 갈아엎어도 이 세 절은 자리만 옮긴다.
_COT_STAGE_2_HEAD = """[단계 2: 기밀 문서 작성]
[단계 1]에서 답한 취약점이 **본문만 읽고도** 확인되는 내부 문서 한 건을 쓴다.
분석에서 든 정보가 실제로 본문 안에 값으로 들어 있어야 한다.
[조건]에 주어지지 않은 소재를 새로 끌어들이지 않는다.
→ document.blocks와 document.title에 적는다."""

#: ``_TITLE_RULE``의 3단계 판. **규칙이 부르는 이름이 절 구성을 따라와야 한다.**
#:
#: v2에서 절을 재편하면서 대상시설 슬롯이 ``[조건]``의 ``- 대상 장소:``로 올라갔다.
#: 그런데 공유 ``_TITLE_RULE``은 "[다루는 사건]의 조건(대상시설·촉발계기·진행단계)"
#: 이라고 **옛 절·옛 라벨**을 부른다 — 감사 실측(60/60 프레임): CoT 프롬프트에
#: 문자열 ``대상시설``은 이 규칙 본문에만 있고 값이 실린 줄은 ``- 대상 장소:``다.
#: 제목을 건마다 구별시키는 축 중 값 종류가 가장 많은 것(시설 6종)이 규칙의
#: 사정권 밖으로 나간 셈이고, 그러면 제목이 ``document_name``으로 돌아가는 실측
#: 실패(2026-08-03, 10건 중 8건)가 그만큼 다시 열린다.
#:
#: ``[문서 정보]`` 때와 같은 회귀이고 한 칸 옆에서 다시 났다. 저쪽은 절 이름을
#: 되돌려 막았지만 이쪽은 되돌릴 수 없다 — ``[조건]``의 ``대상 장소``는 프롬프트
#: 골격이 요구하는 이름이다. 그래서 규칙 쪽을 옮긴다.
_COT_TITLE_RULE = """[제목]
- [문서 정보]의 `문서`에 적힌 이름은 문서의 **종류**이지 제목이 아니다. 그
  문자열을 제목으로 그대로 쓰지 않는다.
- [조건]의 `대상 장소`와 [다루는 사건]의 조건(촉발계기·진행단계)이 드러나는
  제목을 짓는다. 연도나 차수처럼 이 건을 특정하는 표현을 넣어 다른 건과
  구별되게 한다.
- '[합성]', '(가상)', 'AI 생성' 같은 라벨은 절대 넣지 않는다."""

#: 3단계 절.
#:
#: "그대로 옮긴다"가 문장 이상인 이유는
#: ``CTrackCoTResponse._snippets_must_be_in_the_document``에 있다. 지키지 않으면
#: 렌더링이 아니라 파싱이 실패한다.
_COT_STAGE_3 = """[단계 3: 행정 기록 로그 생성]
시스템 DB에 저장할 취약점 로그를 뽑는다.
- confidential_snippets: [단계 2]에서 쓴 본문 중 가장 민감한 문구를 **3개 이상**
  고른다. 서로 다른 block에서 고른다 — 한 자리만 가리키면 가장 민감한 것을
  고른 것이 아니라 처음 눈에 띈 것을 집은 것이 된다.
  quote는 그 block에 적은 **그대로** 옮긴다 — 요약하거나 다듬지 않는다.
  block_id는 그 문구가 있는 block의 ID다.
- reasoning_for_storage: [단계 1]의 분석과 위 문구를 가리켜, 이 문서가 왜
  [조건]의 조항에 따라 비공개로 저장되어야 하는지 행정적으로 요약한다."""


@dataclass(frozen=True)
class CaseSlot:
    """사건 프레임의 축 하나.

    ``name``은 프롬프트에 그대로 라벨로 나가므로 사람이 읽어 뜻이 통해야 한다.

    ``guidance``는 값 하나가 **본문에서 무엇으로 나타나야 하는지**를 적는다.
    라벨만 던지면 모델이 무시한다 — ``classification_taxonomy``의 제6호 4종이
    "누구에게 적용되는지만 말하고 무엇을 쓰라는 말이 없어" 사람이 아예 등장하지
    않는 문서가 나온 실측(2026-07-31)과 같은 실패다. 값 의미가 라벨만으로
    자명한 슬롯(예: 대상시설)은 비워둔다.
    """

    name: str
    values: tuple[str, ...]
    guidance: Mapping[str, str] = MappingProxyType({})
    #: 이 슬롯이 **장소** 축인지. 3단계 프롬프트는 장소를 ``[조건]``의
    #: ``대상 장소``로 올리고 나머지는 ``[다루는 사건]``에 둔다.
    #:
    #: 이름으로 판별하지 않는다. ``"대상시설"``을 문자열로 박아 두면 국방부
    #: 템플릿이 ``대상부대``, 경찰청이 ``관할구역``으로 부르는 순간 그 슬롯이
    #: 조용히 사건 축으로 떨어져 ``[조건]``에 장소가 없는 프롬프트가 된다.
    place: bool = False

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError(f"slot {self.name!r} needs at least one value")
        if len(self.values) != len(set(self.values)):
            raise ValueError(f"slot {self.name!r} has duplicate values")
        unknown = set(self.guidance) - set(self.values)
        if unknown:
            raise ValueError(
                f"slot {self.name!r} has guidance for unknown values: {sorted(unknown)}"
            )


@dataclass(frozen=True)
class SubjectCase:
    """``(사안, 문서형식)`` 한 쌍이 실제로 만드는 문서.

    **왜 두 축을 묶는가.** 사안과 형식을 독립 축으로 두면 형식이 프롬프트에
    label 한 줄로만 전달되어 일을 하지 않는다 — 실측에서 ``계획안``·``승인·품의``
    ·``대응계획서`` 셋은 표제부까지 같아 단어 하나 차이였다. 그렇다고
    ``DOCUMENT_FORM_DEFINITIONS``의 형식별 ``generation_detail``(추진배경-목표-…)
    을 붙이면 이번엔 ``진행단계``·``역할구성`` 슬롯의 지시와 어느 쪽이 본문
    구성을 정하는지 모르게 겹친다.

    묶으면 둘 다 없어진다. "보안등급 재조정을 회의록으로 쓰면 무엇인가"에
    직접 답하므로 형식이 내용을 실제로 바꾸고, generic 구성 지시가 필요 없다.

    비용은 조합 수만큼 손으로 쓰는 것이다. 사안 6 × 형식 6 = 36을 다 채우지
    않고 **말이 되는 쌍만** 적는다 — 그러면 ``자살사고 특별관리``를 품의로
    쓰는 것 같은 비호환 조합도 함께 사라진다.
    """

    subject: str
    document_form: DocumentForm
    #: 이 조합이 실무에서 불리는 이름. 제목의 뼈대가 된다.
    document_name: str
    #: 그 문서 안에 실제로 담기는 항목.
    contents: tuple[str, ...]
    #: 이 문서에 붙을 수 있는 진행단계. 품의·계획안에 `시행 결과 보고`가
    #: 붙는 것 같은 모순 조합을 애초에 뽑히지 않게 한다.
    allowed_stages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.contents:
            raise ValueError(f"{self.document_name!r} needs at least one content item")
        if self.document_form is DocumentForm.OTHER:
            raise ValueError("`other` is not a generatable form for C-track templates")
        if not self.allowed_stages:
            raise ValueError(f"{self.document_name!r} needs at least one stage")
        unknown = set(self.allowed_stages) - set(STAGE_GUIDANCE)
        if unknown:
            raise ValueError(f"unknown stages for {self.document_name!r}: {sorted(unknown)}")


#: 적대자가 문서에 대해 서 있는 위치. 셋을 섞으라고 두는 것이지 분류가 목적이
#: 아니다 — 내부자만 넷을 세우면 [단계 1]의 답이 한 방향으로 좁아진다.
ADVERSARY_POSITIONS: Mapping[str, str] = MappingProxyType(
    {"internal": "내부", "external": "외부", "intermediary": "매개"}
)


@dataclass(frozen=True)
class Adversary:
    """이 세부조항이 막고 있는 것을 노리는 행위자 하나.

    ``what_they_gain``이 이 dataclass의 전부다. ``who``만 주면 모델은 이름의
    낱말로 짐작한다 — ``SUBCLAUSE_EXAMPLES``가 "항목명이 아니라 값"을 담는 것과
    같은 이유이고, 여기서 값에 해당하는 것이 "이 정보를 미리 알면 무엇이
    가능해지는가"다.

    **결과로 적고 절차로 적지 않는다.** 이 문장이 [단계 1]의 답을 이끌고 그
    답이 [단계 2]의 본문을 이끈다. "어느 구간이 언제 비는지"라고 쓰면 본문이
    공백 목록이 되고, "그 시점을 노린 접촉이 가능해진다"라고 쓰면 본문은 일정이
    된다 — 조항 요건은 둘 다 서지만 뒤엣것만 표본으로 쓸 수 있다.

    감사(2026-08-05)에서 12건이 이 선을 넘은 채 제안됐다. 넘는 방식은 셋으로
    반복됐다: (1) 통제의 **공백**을 이득으로 지목하기, (2) 회피 방법을 이득에
    적기("감시가 걸리지 않는 통로로"), (3) 수단을 열거하기("증거 정리·도피·
    출국·진술 맞추기"). 새 템플릿을 쓸 때 이 셋을 먼저 확인하라.
    """

    who: str
    position: str
    what_they_gain: str

    def __post_init__(self) -> None:
        if self.position not in ADVERSARY_POSITIONS:
            raise ValueError(
                f"unknown adversary position {self.position!r}; "
                f"one of {sorted(ADVERSARY_POSITIONS)}"
            )
        if not self.who.strip() or not self.what_they_gain.strip():
            raise ValueError("adversary needs both `who` and `what_they_gain`")

    @property
    def position_label(self) -> str:
        return ADVERSARY_POSITIONS[self.position]


@dataclass(frozen=True)
class CTrackTemplate:
    """``(기관, 세부조항)`` 한 쌍의 생성 재료.

    ``instruction``/``document_patterns``는 5~8호의 ``SubclauseGenerationRule``과
    같은 역할이고, ``slots``가 이 모듈이 새로 더하는 부분이다. 템플릿은 문서의
    **골격을 고정하지 않는다** — 골격까지 고정하면 한 템플릿이 담당하는 수백 건이
    전부 같은 뼈대를 갖는다. 템플릿은 재료만 공급하고 구성은 문서유형과 생성기에
    맡긴다.
    """

    subclause_key: SubclauseKey
    agency: str
    #: ``SUBCLAUSE_PERSONA_CONTEXT``의 C트랙 대응 — 생성기 페르소나의 업무 맥락.
    persona_context: str
    #: 이 세부조항이 막고 있는 것을 노리는 행위자.
    #:
    #: **왜 필드인가.** 여기 오기 전까지 적대자는 ``instruction`` 산문 한 구절
    #: ("미리 알려지면 수용자가 대비하거나…")에만 있었고, 3단계 프롬프트의 질문
    #: 1은 템플릿 8종이 접두사를 공유하느라 "막고 있는 것을 노리는 사람"이라는
    #: 공통분모로 내려가 있었다. 공통분모만 말하는 층은 ``minimal_prompt`` v9가
    #: 지운 것과 같은 성질이고, ``CaseSlot.guidance``의 실측(2026-07-31)이 같은
    #: 말을 한다 — **라벨만 던지면 모델이 무시한다.**
    #:
    #: 하나만 두면 안 된다. 같은 템플릿의 수천 건이 전부 같은 취약점을 말하게
    #: 된다 — 축이 하나로 좁아지는 것은 ``expand_cases``가 보폭 방식에서 두 번
    #: 데인 것과 같은 실패다.
    adversaries: tuple["Adversary", ...]
    #: 기관 아래 실제 생산 부서. ``MARKING_SPEC_AGENCY_WHITELIST``가 부처 단위까지만
    #: 내려가 있어 "국방부가 쓴 문서"밖에 못 만들던 것을 과 단위로 좁힌다.
    departments: tuple[str, ...]
    instruction: str
    #: ``사안``은 여기 없다 — ``subject_cases``가 문서형식과 묶어서 들고 있다.
    slots: tuple[CaseSlot, ...]
    #: ``(사안, 문서형식)`` 쌍의 목록. 말이 되는 쌍만 적는다.
    subject_cases: tuple[SubjectCase, ...]

    def __post_init__(self) -> None:
        if not self.subject_cases:
            raise ValueError("template needs at least one subject case")
        seen = {(case.subject, case.document_form) for case in self.subject_cases}
        if len(seen) != len(self.subject_cases):
            raise ValueError("duplicate (subject, document_form) pair")
        if any(slot.name == "사안" for slot in self.slots):
            raise ValueError("`사안` belongs to subject_cases, not slots")
        # 둘을 요구한다. 하나면 [단계 1]의 답이 한 방향으로만 나오고, 그 답이
        # [단계 2]를 이끌므로 같은 템플릿의 문서들이 같은 취약점만 말하게 된다.
        if len(self.adversaries) < 2:
            raise ValueError("template needs at least two adversaries")
        if len({adversary.who for adversary in self.adversaries}) != len(
            self.adversaries
        ):
            raise ValueError("duplicate adversary")
        if len(self.place_slots) != 1:
            raise ValueError("template needs exactly one place slot")

    @property
    def place_slots(self) -> tuple[CaseSlot, ...]:
        return tuple(slot for slot in self.slots if slot.place)

    @property
    def clause_no(self) -> ClauseNumber:
        return clause_of_subclause(self.subclause_key)

    @property
    def security_grades(self) -> tuple[str, ...]:
        """등급 축. 국방부·국정원만 Ⅰ~Ⅲ급이고 나머지는 대외비 하나다."""

        if is_military_secret_agency(self.agency):
            return MILITARY_SECRET_GRADES
        return (_CONFIDENTIAL_GRADE,)

    @property
    def subjects(self) -> tuple[str, ...]:
        """등장 순서를 지킨 사안 목록."""

        out: list[str] = []
        for case in self.subject_cases:
            if case.subject not in out:
                out.append(case.subject)
        return tuple(out)

    @property
    def document_forms(self) -> tuple[DocumentForm, ...]:
        """이 템플릿이 실제로 쓰는 문서형식."""

        out: list[DocumentForm] = []
        for case in self.subject_cases:
            if case.document_form not in out:
                out.append(case.document_form)
        return tuple(out)

    @property
    def case_stage_pairs(self) -> tuple[tuple[SubjectCase, str], ...]:
        """``(문서, 진행단계)``로 펼친 축.

        단계를 독립 슬롯으로 두면 품의에 `시행 결과 보고`가 붙는다. 문서마다
        허용 단계만 펼쳐 그런 조합이 애초에 존재하지 않게 한다.
        """

        return tuple(
            (case, stage)
            for case in self.subject_cases
            for stage in case.allowed_stages
        )

    @property
    def case_count(self) -> int:
        """이 템플릿이 중복 없이 만들 수 있는 문서 개수.

        사안과 형식은 더 이상 독립 축이 아니므로 곱이 아니라
        ``len(subject_cases)`` 하나로 들어간다.
        """

        total = (
            len(self.departments)
            * len(self.case_stage_pairs)
            * len(self.security_grades)
        )
        for slot in self.slots:
            total *= len(slot.values)
        return total


@dataclass(frozen=True)
class CaseFrame:
    """템플릿에서 전개된 문서 한 건의 좌표."""

    subclause_key: SubclauseKey
    agency: str
    department: str
    #: 잠긴 ``(사안, 문서형식)`` 쌍과 그 조합이 만드는 문서.
    subject_case: SubjectCase
    #: 그 문서에 허용된 진행단계 중 하나.
    stage: str
    security_grade: str
    #: 슬롯 이름 -> 선택된 값.
    slot_values: Mapping[str, str]
    #: 전개 인덱스. 같은 (template, seed)면 항상 같은 프레임이 나온다.
    case_index: int

    @property
    def document_form(self) -> DocumentForm:
        return self.subject_case.document_form

    @property
    def subject(self) -> str:
        return self.subject_case.subject


# ---------------------------------------------------------------------------
# 템플릿 정의
# ---------------------------------------------------------------------------

_CORRECTION_SECURITY_MOJ = CTrackTemplate(
    subclause_key=SubclauseKey.CORRECTION_SECURITY,
    agency="법무부",
    persona_context="교정시설 보안·수용관리 업무",
    # 감사(2026-08-05)가 제안한 4종을 경계 검토 지적을 반영해 고쳐 실었다.
    # 원안에서 넷 중 셋이 선을 넘었다 — (a)는 "계호·통제가 어느 조건에서
    # 느슨해지는지"로 통제 공백을 이득으로 지목했고 "자해를 시도"가
    # `자살·자해사고 특별관리` 사안과 만나 본문에 자해 경위를 요구할 참이었다,
    # (b)는 "어느 절차가 대조·확인되지 않는지" + "기록에 남지 않을 여지"로 반입
    # 수법의 요건 두 개를 짝지었다, (c)는 "감시가 걸리지 않는 통로로"라고 회피
    # 방법을 이득에 적었다. 셋 다 이득을 **제도의 사실**이나 **결과**로 되돌렸다.
    adversaries=(
        Adversary(
            who="수용자",
            position="internal",
            what_they_gain=(
                "통제와 처우 판정이 무엇을 근거로 언제 바뀌는지 미리 알면, "
                "바뀌기 전에 대비하거나 판정을 자기에게 유리한 쪽으로 돌릴 "
                "여지가 생긴다."
            ),
        ),
        Adversary(
            who="외부 청탁에 응한 교정 종사자",
            position="internal",
            what_they_gain=(
                "무엇이 어느 주기로 확인되는지 알면 자기 행위가 그 확인에 "
                "걸릴지를 미리 가늠할 수 있게 된다."
            ),
        ),
        Adversary(
            who="접견인·서신 상대 등 외부 조력자",
            position="intermediary",
            what_they_gain=(
                "누가 통제 대상으로 지정됐는지 미리 알면 통제가 겨냥한 연락이 "
                "통제가 서기 전에 끝나 버린다."
            ),
        ),
        Adversary(
            who="시설 밖 조직범죄 관계자·공범",
            position="external",
            what_they_gain=(
                "수용자가 시설 밖으로 나오는 일정이 미리 알려지면 그 시점을 "
                "노린 접촉 시도가 가능해진다."
            ),
        ),
    ),
    departments=(
        "교정본부 보안과",
        "교정본부 분류심사과",
        "교정본부 사회복귀과",
        "교정본부 의료과",
        "교정본부 교정기획과",
        "범죄예방정책국 보호관찰과",
        "범죄예방정책국 치료처우과",
    ),
    instruction=(
        "형 집행·교정시설 운영·보안처분의 내부 자료를 쓴다. 계호 배치, 수용 구분, "
        "이송 경로, 통제 절차처럼 미리 알려지면 수용자가 대비하거나 도주·반입 "
        "시도가 가능해지는 내용을 담고, 공개 시 교정 직무수행이 어떻게 곤란해지는지 "
        "문맥에서 드러나게 한다."
    ),
    subject_cases=(
        SubjectCase(
            subject="보안등급 재조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="보안등급 조정 계획(안)",
            contents=(
                "시설별 현행 등급과 조정안, 조정 사유",
                "계호인력 증감 소요와 시행 일정",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="보안등급 재조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="보안등급 조정 심의 회의록",
            contents=(
                "회차·개최일시·장소와 참석자 명단",
                "등급안별 위원 발언과 찬반 이유, 의결·보류 결과",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="보안등급 재조정",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="보안등급 조정 품의",
            contents=(
                "품의 사유와 승인받을 조정 등급",
                "기안-검토-결재 열을 가진 결재란",
                "조정에 따른 소요 인력과 예산",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="수용자 이송 계획 수립",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="수용자 이송 계획(안)",
            contents=(
                "이송 대상 구분과 인원",
                "이송 경로와 출발·도착 시각, 호송 인력 편성",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="수용자 이송 계획 수립",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="수용자 이송 승인 품의",
            contents=(
                "이송이 필요한 사유와 대상자 수",
                "호송 방법과 소요 인력",
                "기안-검토-결재 열을 가진 결재란",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="수용자 이송 계획 수립",
            document_form=DocumentForm.REPORT,
            document_name="수용자 이송 실시 결과보고",
            contents=(
                "실제 이송 인원과 구간별 소요 시간(표)",
                "이송 중 발생한 문제와 조치",
                "다음 이송에 반영할 개선 사항",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="계호인력 재배치",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="계호인력 재배치 계획(안)",
            contents=(
                "근무지별 현원 대비 배치안(표)",
                "취약 시간대와 순찰 주기, 사각지대",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="계호인력 재배치",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="계호 근무 실태 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "근무지별 결원 현황과 지적사항",
                "시정 요구 사항과 이행 기한",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="계호인력 재배치",
            document_form=DocumentForm.REPORT,
            document_name="계호인력 운영 현황보고",
            contents=(
                "기준 시점과 근무지별 정원·현원 집계표",
                "결원 발생 원인과 충원 계획",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="접견·서신 통제 강화",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="접견·서신 통제 강화 계획(안)",
            contents=(
                "통제 대상 구분과 검열 기준·절차",
                "예외 인정 범위와 승인 경로",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="접견·서신 통제 강화",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="통제 대상자 지정 품의",
            contents=(
                "지정 사유와 지정 기간, 해제 요건",
                "기안-검토-결재 열을 가진 결재란",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="접견·서신 통제 강화",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="통제 이행 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "검열 누락 건수와 절차 위반 사례",
                "시정 요구 사항",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="자살·자해사고 특별관리",
            document_form=DocumentForm.RESPONSE_PLAN,
            document_name="자살·자해사고 대응계획서",
            contents=(
                "상황 단계(관심-주의-경계-심각)별 조치사항(표)",
                "단계별 담당 부서와 연락체계",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="자살·자해사고 특별관리",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="위험군 특별관리 계획(안)",
            contents=(
                "위험군 선별 기준과 관찰 주기",
                "거실 배치와 반입 물품 제한 항목",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="자살·자해사고 특별관리",
            document_form=DocumentForm.REPORT,
            document_name="사고 발생 경위 및 조치 결과보고",
            contents=(
                "발생 경위와 발견·보고 시각",
                "초동 조치 내용과 후속 처리",
                "재발 방지 대책",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="분류처우 기준 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="분류처우 기준 조정(안)",
            contents=(
                "위험도 평가 항목과 배점(표)",
                "처우 등급별 제한 사항과 재심사 요건",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="분류처우 기준 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="분류처우위원회 회의록",
            contents=(
                "회차·개최일시·장소와 참석자 명단",
                "대상자별 심의 의견과 위원 발언",
                "등급 결정과 보류 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="분류처우 기준 조정",
            document_form=DocumentForm.REPORT,
            document_name="분류처우 운영 현황보고",
            contents=(
                "기준 시점과 등급별 인원 분포(표)",
                "재심사 결과와 상향·하향 건수",
                "기준 개선이 필요한 항목",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
    slots=(
        CaseSlot(
            name="대상시설",
            place=True,
            values=(
                "서울구치소",
                "대전교도소",
                "청주여자교도소",
                "안양교도소",
                "서울소년원",
                "공주치료감호소",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            values=(
                "정기 보안점검 결과",
                "수용자 도주 시도 발생",
                "감사원 지적사항",
                "수용정원 초과",
                "외부 진정·언론 보도",
            ),
        ),
        # "인물"을 성명이 아니라 역할 구성으로 둔다 — 이름만 바꾸면 본문이 안 바뀐다.
        CaseSlot(
            name="역할구성",
            values=(
                "담당 부서 단독 검토",
                "본부-일선기관 합동 검토",
                "외부 위원이 참여하는 심의",
                "경찰·검찰 등 유관기관 협의",
            ),
            guidance=MappingProxyType(
                {
                    "담당 부서 단독 검토": (
                        "작성 부서 안에서 오간 것만 적는다. ① 담당자 검토 의견 → "
                        "② 과장 지시사항 → ③ 지시를 반영한 결과 순으로 적고, "
                        "외부 참여자를 등장시키지 않는다."
                    ),
                    "본부-일선기관 합동 검토": (
                        "① 본부가 요구한 사항 → ② 일선기관이 올린 현장 의견 → "
                        "③ 둘이 어긋난 지점 → ④ 조정된 결과 순으로 적는다. "
                        "현장 의견은 본부 판단과 다른 내용을 담아야 한다."
                    ),
                    "외부 위원이 참여하는 심의": (
                        "① 안건 상정과 담당 부서의 설명 → ② 위원별 질의와 그에 "
                        "대한 답변 → ③ 쟁점별 찬반 발언과 근거 → ④ 의결 또는 보류 "
                        "결정과 그 사유 순으로 적는다. 뒤 단계의 발언은 앞 단계에서 "
                        "나온 말을 받아야 하며, 서로 무관한 한 줄 의견을 나열하지 "
                        "않는다."
                    ),
                    "경찰·검찰 등 유관기관 협의": (
                        "① 협의를 요청한 사유 → ② 기관별 입장 표명 → ③ 입장이 갈린 "
                        "쟁점 → ④ 합의된 것과 미합의로 남은 것 순으로 적는다. "
                        "기관마다 서로 다른 이해관계가 드러나야 한다."
                    ),
                }
            ),
        ),
    ),
)


#: 템플릿을 추가할 때의 결정 사항. 8종 초안 감사(2026-08-05, 지적 39건)에서
#: 나온 것이고 사용자가 결정했다.
#:
#: **1. 장소 슬롯 값은 실존명을 쓴다.** 기관·부서·시설이 전부 실재하는 이름이다
#: (``대전교도소``). 부처별 문체와 조직 맥락이 표본에 남아야 하기 때문이고,
#: 대신 그 안을 채우는 값을 전부 가상으로 두는 것이 ``_BOUNDARY_RULE``이다.
#: 초안 단계에서 "부대 유형으로만 두자"는 안이 나왔지만 채택하지 않았다.
#:
#: **2. ``life_body``와 ``property``는 사람/물건으로 가른다.** 초안에서 두
#: 세부조항이 정면 충돌했다 — 기관(행정안전부)이 양쪽에 있고 장소축이 사실상
#: 같고 촉발계기 5개 중 3개가 같은 문장이었다. 적대자까지 겹쳐(안전점검 수탁
#: 업체 ↔ 안전진단 용역업체) 두 템플릿의 [단계 1] 분석이 같아지고 생성 문서를
#: 사후에 구별할 수 없게 된다.
#:   - ``life_body`` — 사람이 그 안에 있을 때 생기는 피해만. 대피·구조·감염병·
#:     고위험물질 취급 통제. 기관은 행정안전부.
#:   - ``property`` — 구조물 등급 판정·진단 결과·사용제한 조치. 기관은 국토교통부.
#:   - 촉발계기 ``정기 안전점검·정밀안전진단 결과``는 ``property`` 전용이다.
#:   - 수탁 업체 적대자는 한쪽에만 둔다 — 취약 지점을 쥐는 쪽이 ``life_body``,
#:     판정 결과를 흘리는 쪽이 ``property``다.
#:
#: **3. 제4호 두 세부조항의 브로커는 파는 물건이 달라야 한다.** ``trial_``
#: ``investigation``의 브로커는 "수사선상에 있다는 사실 자체"를 팔고,
#: ``prosecution``의 브로커는 "처분 결론이 어느 쪽으로 기울었는지"를 판다.
#: 같은 문장으로 두면 taxonomy의 ``excludes``가 생성 단계에서 작동하지 못한다.
C_TRACK_TEMPLATES: Mapping[
    tuple[SubclauseKey, str], CTrackTemplate
] = MappingProxyType(
    {
        (template.subclause_key, template.agency): template
        for template in (_CORRECTION_SECURITY_MOJ,)
    }
)


# ---------------------------------------------------------------------------
# 전개
# ---------------------------------------------------------------------------


def _mixed_radix_decode(index: int, radices: tuple[int, ...]) -> tuple[int, ...]:
    """``index``를 각 축의 자릿수로 분해한다."""

    digits = []
    for radix in reversed(radices):
        digits.append(index % radix)
        index //= radix
    return tuple(reversed(digits))


#: ``expand_cases``가 좌표를 통째로 섞어도 되는 상한. C트랙 템플릿 하나는
#: 수만 조합이라 여유가 크다. 넘으면 메모리를 아끼는 다른 방법이 필요하다.
MAX_SHUFFLED_CASE_COUNT = 5_000_000


def expand_cases(
    template: CTrackTemplate,
    count: int,
    *,
    seed: int = 0,
) -> Iterator[CaseFrame]:
    """템플릿에서 ``count``건의 사건 프레임을 결정론적으로 전개한다.

    ``count <= template.case_count``이면 서로 다른 조합만 나온다. 넘어서면 두 번째
    바퀴부터 같은 조합이 다시 나오므로, 그 시점에는 슬롯 값을 늘리는 게 맞다 —
    호출부가 판단할 수 있게 막지는 않는다.

    **왜 좌표를 통째로 섞는가.** 처음에는 ``(start + i*stride) % total``로 훑었다.
    전단사라 중복은 0이지만 **축이 고르게 돌지 않았다.** 보폭이 전체와 서로소여도
    mixed-radix의 특정 자리는 짧은 주기에 갇힌다. 두 번 데었다.

    1. stride=11: 12만 조합 중 첫 500건이 전부 같은 부서, 문서형식 2종.
    2. 황금비 보폭(37393): ``subject_cases`` 자리(18진법) 증분이 6이 되어
       ``gcd(6,18)=6``, 앞 10건이 18개 중 4개만 썼다.

    2번은 자릿수별 증분까지 서로소로 잡아도 안 잡혔다 — 하위 자리에서 올라오는
    **자리올림**이 증분을 5에서 6으로 밀기 때문이고, 그 빈도는 보폭에서 해석적으로
    통제할 수 없다.

    조합 수가 수만 규모라 ``list(range(total))``을 섞는 비용이 무시할 만하다.
    중복 0과 축 균등이 둘 다 구성상 보장된다.
    """

    if count < 0:
        raise ValueError("count must not be negative")

    radices = (
        len(template.departments),
        len(template.case_stage_pairs),
        len(template.security_grades),
        *(len(slot.values) for slot in template.slots),
    )
    total = template.case_count
    if total > MAX_SHUFFLED_CASE_COUNT:
        raise ValueError(
            f"case_count={total} exceeds {MAX_SHUFFLED_CASE_COUNT}; "
            "전개 방식을 다시 정해야 한다"
        )

    pairs = template.case_stage_pairs
    order = list(range(total))
    random.Random(seed).shuffle(order)

    for i in range(count):
        raw = order[i % total]
        digits = _mixed_radix_decode(raw, radices)
        case, stage = pairs[digits[1]]
        yield CaseFrame(
            subclause_key=template.subclause_key,
            agency=template.agency,
            department=template.departments[digits[0]],
            subject_case=case,
            stage=stage,
            security_grade=template.security_grades[digits[2]],
            slot_values=MappingProxyType(
                {
                    slot.name: slot.values[digit]
                    for slot, digit in zip(template.slots, digits[3:])
                }
            ),
            case_index=raw,
        )


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------


def _grade_display(grade: str) -> str:
    return MILITARY_SECRET_GRADE_LABELS.get(grade, grade)


def render_fixed_prefix(template: CTrackTemplate) -> str:
    """템플릿 하나에서 **문서마다 바뀌지 않는** 앞부분.

    OpenAI 프롬프트 캐싱은 요청들의 **공통 접두사**에만 걸린다. 고정 내용이 뒤에
    흩어져 있으면 한 글자도 캐시되지 않는다 — 실측(2026-08-04): 첫 줄이
    ``당신은 {기관} {부서}에서``라 부서에서 갈렸고, 50건의 공통 접두사가 **8자**
    였다.

    그래서 순서를 바꾼다. 조항·업무맥락·작성지시·비공개근거·제목·문체가 먼저 오고,
    부서·문서·사건은 그 뒤로 간다. 같은 템플릿으로 4000건을 돌리면 이 앞부분이
    전부 캐시 히트가 된다.

    **잠긴 것만 준다는 원칙은 깨지 않는다.** 진행단계·역할구성의 전개 지시를 네
    개씩 전부 앞에 실으면 접두사가 길어져 캐싱에는 유리하지만, 목표가 이미 하나로
    잠긴 상태에서 관련 없는 셋을 함께 읽히는 것이 된다 —
    ``classification_taxonomy``가 제5~8호 16개 규칙을 한 문자열에 넣었을 때
    "금지 규칙이 관련 없는 15개 사이에 묻혀" 생성물이 정확히 그 금지 문장으로
    끝난 실측이 있다. 입력 토큰 절반을 아끼자고 뒤집을 근거가 못 된다.
    """

    return "\n".join(
        [
            *_situation_sections(template),
            "",
            # 경계는 두 경로 모두에 싣는다. 대조군을 글자 그대로 보존하는 것보다
            # 우선한다 — 두 경로가 같은 종류의 문서를 만들고, 한쪽만 경계를
            # 빼두는 것은 실험 순도로 정당화되지 않는다.
            _BOUNDARY_RULE,
            "",
            _NONDISCLOSURE_BASIS,
            "",
            _TITLE_RULE,
            "",
            _DOCUMENT_STYLE_RULES,
            "",
            _BLOCK_STRUCTURE_RULES,
        ]
    )


def _situation_sections(template: CTrackTemplate) -> list[str]:
    """단일 출력 접두사의 앞부분 — 업무 맥락·대상 법조문·작성 지시.

    3단계 접두사 v1이 이 목록을 함께 썼다. v2에서 골격이 갈리면서(``#역할``·
    ``#과제``) 쓰는 쪽은 여기 하나만 남았지만, 함수는 그대로 둔다 — 두 접두사가
    같은 리스트 리터럴을 다시 공유하게 되면 3단계 쪽을 손볼 때 대조군 문구가
    조용히 따라 움직인다. 대조하려고 만든 것이 대조 대상까지 바꾸면 비교가
    성립하지 않는다.
    """

    definition = SUBCLAUSE_DEFINITIONS[template.subclause_key]
    clause = CLAUSES[template.clause_no.value]
    return [
        "[업무 맥락]",
        f"- 소속: {template.agency}",
        f"- 담당 업무: {template.persona_context}",
        "아래 [문서 정보]와 [다루는 사건]에 주어진 조건 그대로 내부 문서 "
        "한 건을 작성한다. 주어지지 않은 소재를 새로 끌어들이지 않는다.",
        "",
        "[대상 법조문]",
        f"- 정보공개법 제9조제1항 제{template.clause_no.value}호: {clause.title}",
        f"- 세부유형: {definition.label}({template.subclause_key.value})",
        f"- 판정 기준: {definition.definition}",
        "",
        "[작성 지시]",
        template.instruction,
    ]


def render_cot_stage_1(template: CTrackTemplate) -> str:
    """[단계 1]에 이 템플릿의 적대자를 이름과 이득으로 꽂는다.

    이름만 나열하지 않는다. ``Adversary.what_they_gain``이 함께 서야 모델이
    "무엇이 무력화되는지"를 그 층위로 답한다 — 이름만 주면 낱말로 짐작한다.
    """

    lines = [_COT_STAGE_1_HEAD]
    for adversary in template.adversaries:
        lines.append(
            f"   - {adversary.who}({adversary.position_label})"
            f" — {adversary.what_they_gain}"
        )
    lines.append(_COT_STAGE_1_TAIL)
    return "\n".join(lines)


def render_cot_fixed_prefix(template: CTrackTemplate) -> str:
    """3단계(분석-작성-기록) 경로의 고정 접두사.

    ``render_fixed_prefix``와 **골격이 다르다.** 저쪽은 지시를 종류별 절로
    쌓아 두고(맥락·법조문·작성지시·근거·제목·문체·구조) 문서 하나를 요구한다.
    이쪽은 절을 **단계**로 세우고, 각 단계 끝에 그 단계가 채우는 필드를 적는다.

    - ``#역할``·``#과제`` — 기관과 세부조항이 여기서 잠긴다.
    - ``[단계 1]`` — 취약점 세 질문 → ``security_analysis``
    - ``[단계 2]`` — 작성 지시 + ``[제목]``·``[문체]``·``[출력 구조]``
      → ``document``
    - ``[단계 3]`` — 발췌와 저장 사유 → ``confidential_snippets``·
      ``reasoning_for_storage``

    ``[비공개 근거]`` 절은 없다. 그 절이 하던 말이 [단계 1]의 세 질문으로
    옮겨갔고, 같은 말이 두 층에 서면 성긴 쪽을 지우는 것이 ``minimal_prompt``
    v9가 호 단위 절을 뺀 근거였다.

    ``[문체]``·``[출력 구조]``·``[제목]``은 골격이 바뀌어도 자리만 옮긴다 —
    ``_COT_STAGE_2_HEAD`` 주석에 왜인지 적어 뒀다.

    조항은 ``[조건]``(user)에 실려 오지만 세부조항 정의와 판정 기준은 여기
    ``#과제``에 둔다. 같은 템플릿의 모든 건이 같은 세부조항이라 고정 접두사에
    있어야 프롬프트 캐시에 걸린다.
    """

    definition = SUBCLAUSE_DEFINITIONS[template.subclause_key]
    clause = CLAUSES[template.clause_no.value]

    return "\n".join(
        [
            "#역할",
            f"너는 {template.agency}의 보안 행정 전문가이자, 정보공개법에 따른 "
            "비공개 근거를 실무적으로 판단하는 보안 심의관이다.",
            f"- 담당 업무: {template.persona_context}",
            "",
            "#과제",
            "아래 [조건]에 주어진 세부조항의 취약점을 찾고, 그 취약점이 실제로 "
            "담긴 내부 문서 한 건을 3단계로 만든다.",
            f"- 대상 조항: 정보공개법 제9조제1항 제{template.clause_no.value}호"
            f" — {clause.title}",
            f"- 세부유형: {definition.label}({template.subclause_key.value})",
            f"- 판정 기준: {definition.definition}",
            "",
            _BOUNDARY_RULE,
            "",
            render_cot_stage_1(template),
            "",
            _COT_STAGE_2_HEAD,
            "",
            template.instruction,
            "",
            _COT_TITLE_RULE,
            "",
            _DOCUMENT_STYLE_RULES,
            "",
            _BLOCK_STRUCTURE_RULES,
            "",
            _COT_STAGE_3,
        ]
    )


def render_cot_case_section(template: CTrackTemplate, frame: CaseFrame) -> str:
    """3단계 경로의 ``[조건]`` — 문서 한 건에서만 달라지는 뒷부분.

    ``render_case_section``과 담는 값은 같고 묶는 방식이 다르다. 저쪽은
    ``[문서 정보]``·``[다루는 사건]``·``[이 문서에 담기는 항목]`` 세 절이지만,
    이쪽은 프롬프트 골격이 참조하는 이름(``[조건]``) 하나로 모은다 — [단계 1]과
    [단계 3]이 "[조건]에 주어진 조항"을 가리키므로 가리킬 절이 하나여야 한다.

    ``법적 근거``를 여기 한 번 더 싣는다. 접두사 ``#과제``에도 있지만 그쪽은
    캐시에 걸린 고정 문자열이고, [단계 1]·[단계 3]이 지목하는 것은 이 절이다.

    값 자체는 여전히 만들지 않는다 — ``render_case_section``과 같은 원칙이다.
    성명·일자·금액을 여기서 박으면 같은 조합을 받은 문서들이 같은 숫자를
    공유한다.
    """

    case = frame.subject_case
    place_slots = [slot for slot in template.slots if slot.place]
    event_slots = [slot for slot in template.slots if not slot.place]

    lines = [
        "[조건]",
        f"- 대상 기관: {template.agency}",
        f"- 작성 부서: {frame.department}",
    ]
    for slot in place_slots:
        value = frame.slot_values[slot.name]
        lines.append(f"- 대상 장소: {value}")
        # 사건 축과 같은 층위로 guidance를 싣는다. 값만 찍고 끝내면 이 슬롯만
        # 처방을 실을 통로가 없어진다 — 라벨만 던지면 모델이 무시한다는 것이
        # ``CaseSlot.guidance`` 주석의 실측(2026-07-31)이다.
        hint = slot.guidance.get(value)
        if hint:
            lines.append(f"  → {hint}")
    lines += [
        f"- 법적 근거: 정보공개법 제9조제1항 제{template.clause_no.value}호"
        f" — {SUBCLAUSE_DEFINITIONS[template.subclause_key].label}",
        "",
        "[다루는 사건]",
        f"- 사안: {case.subject}",
        f"- 진행단계: {frame.stage}",
        f"  → {STAGE_GUIDANCE[frame.stage]}",
    ]
    for slot in event_slots:
        value = frame.slot_values[slot.name]
        lines.append(f"- {slot.name}: {value}")
        hint = slot.guidance.get(value)
        if hint:
            lines.append(f"  → {hint}")

    lines += [
        "",
        # 절 이름은 ``[문서 정보]``여야 한다. ``_TITLE_RULE``이 "[문서 정보]의
        # `문서`에 적힌 이름은 종류이지 제목이 아니다"라고 이 절을 지목한다 —
        # 이름을 바꾸면 규칙이 없는 절을 가리키게 되고, 그러면 제목이
        # ``document_name``으로 돌아간다(실측 2026-08-03, 10건 중 8건).
        "[문서 정보]",
        f"- 문서: {case.document_name}",
        f"- 문서형식: {DOCUMENT_FORM_DEFINITIONS[case.document_form].label}",
        f"- 표제부 항목: {' / '.join(header_keys_for(case.document_form))}",
        f"- 비밀등급: {_grade_display(frame.security_grade)}",
        f"  → {_GRADE_GUIDANCE[frame.security_grade]}",
        "",
        "[핵심 정보 — 알려지면 이 세부조항에 가장 큰 피해를 주는 값]",
        "아래 항목을 새 가상 값으로 채운다. 성명·일자·인원·시각·식별자는 실제 "
        "형식의 값을 만들어 넣고, 값 없이 항목명만 나열하지 않는다.",
    ]
    lines.extend(f"- {item}" for item in case.contents)

    return "\n".join(lines)


def render_case_section(template: CTrackTemplate, frame: CaseFrame) -> str:
    """문서 한 건에서만 달라지는 뒷부분.

    ``seed_assembly.build_sensitive_seed``와 같은 원칙을 따른다 — **무엇이
    필요한지**만 정하고 성명·일자·금액 같은 구체적 값은 생성기가 만들게 한다.
    여기서 값을 박으면 같은 조합을 받은 문서들이 같은 숫자를 공유한다.
    """

    case = frame.subject_case
    lines = [
        "[문서 정보]",
        f"- 작성 부서: {frame.department}",
        f"- 문서: {case.document_name}",
        f"- 문서형식: {DOCUMENT_FORM_DEFINITIONS[case.document_form].label}",
        f"- 표제부 항목: {' / '.join(header_keys_for(case.document_form))}",
        f"- 비밀등급: {_grade_display(frame.security_grade)}",
        f"  → {_GRADE_GUIDANCE[frame.security_grade]}",
        "",
        "[다루는 사건]",
        f"- 사안: {case.subject}",
        f"- 진행단계: {frame.stage}",
        f"  → {STAGE_GUIDANCE[frame.stage]}",
    ]
    for slot in template.slots:
        value = frame.slot_values[slot.name]
        lines.append(f"- {slot.name}: {value}")
        hint = slot.guidance.get(value)
        if hint:
            lines.append(f"  → {hint}")

    lines += [
        "",
        "[이 문서에 담기는 항목]",
        "아래 항목을 새 가상 값으로 채운다. 성명·일자·인원·시각·식별자는 실제 "
        "형식의 값을 만들어 넣고, 값 없이 항목명만 나열하지 않는다.",
    ]
    lines.extend(f"- {item}" for item in case.contents)

    return "\n".join(lines)


def render_generation_prompt(template: CTrackTemplate, frame: CaseFrame) -> str:
    """문서 한 건의 생성 프롬프트 전문 = 고정 접두사 + 이 건의 조건."""

    return f"{render_fixed_prefix(template)}\n\n{render_case_section(template, frame)}"


_C_TRACK_CLAUSES = frozenset(
    {
        ClauseNumber.CLAUSE_1,
        ClauseNumber.CLAUSE_2,
        ClauseNumber.CLAUSE_3,
        ClauseNumber.CLAUSE_4,
    }
)
for _key, _template in C_TRACK_TEMPLATES.items():
    if _template.clause_no not in _C_TRACK_CLAUSES:
        raise RuntimeError(
            f"{_template.subclause_key.value!r} is not a clause 1-4 subclause"
        )
    if not _template.slots:
        raise RuntimeError(f"template {_key} needs at least one case slot")
