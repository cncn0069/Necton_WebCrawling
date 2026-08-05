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
from itertools import combinations
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
#: v3에서 ``STAGE_GUIDANCE``·역할구성 ``guidance``의 ①②③④를 걷어 냈다. 근거는
#: ``STAGE_GUIDANCE`` 주석에 있다 — 번호가 본문 절 이름으로 복사됐고, 프롬프트
#: 규칙으로는 한 축에서만 꺾였다.
#:
#: v4에서 ``adversaries``가 전개 축이 됐다(``CTrackTemplate.adversary_pairs``).
#: **좌표 배치가 바뀌므로 v3 이전의 ``case_index``는 다른 문서를 가리킨다.**
#: 되쓰기가 ``source_url``에 좌표를 싣고 있어(``c_track_source_document_id``)
#: 버전을 올리지 않으면 옛 행과 새 행이 같은 키로 뭉갠다.
#:
#: v5는 촉발계기 60개에 guidance를 채웠다. **좌표는 그대로다** — 값 개수가
#: 그대로라 ``case_count``도 자리 배치도 안 바뀌고, 같은 ``case_index``는 여전히
#: 같은 프레임이다. 달라진 것은 그 프레임이 만드는 프롬프트 문구뿐이다.
#:
#: v6은 ``SubjectCase.contents`` 216개를 다섯 항목으로 맞췄다. 좌표는 역시
#: 그대로다 — 축이 아니라 축 하나의 값이 두꺼워진 것이다. 근거는 그 필드
#: 주석에 있다.
#:
#: v7은 진행단계 순서 문장을 단계마다 넷으로 늘리고 그것을 축으로 세웠다
#: (``STAGE_VARIANT_COUNT``). **좌표 배치가 다시 바뀐다** — v6 이전의
#: ``case_index``는 다른 문서를 가리킨다.
C_TRACK_TEMPLATE_VERSION = "c-track-template-v7"

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
#:
#: v4는 절 하나를 더한다(``_COT_CONDITION_USE_RULE``). 조건표와 지시 문구가
#: 본문으로 새는 것을 막는 자리이고, 근거가 되는 실측은 그 상수 주석에 있다.
#:
#: v5는 그 규칙이 못 막은 것을 **형태**로 막는다 — ``[다루는 사건]``과
#: ``[문서 정보]``의 ``라벨: 값`` 표가 문장으로 바뀌었다(``CaseSlot.phrasing``).
#: v4에서 규칙을 세우고도 조건표가 값 없이 라벨만 문서 첫 절로 복사된 실측이
#: 근거다.
#:
#: v6에서 적대자 목록이 접두사에서 ``[조건]``의 ``[관계자]`` 절로 내려갔다.
#: 접두사에 남는 것은 [단계 1]의 질문 골격뿐이고, 누구를 놓고 답하는지는
#: 건마다 달라진다.
C_TRACK_COT_PROMPT_VERSION = "c-track-cot-2026-08-05-v6"

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
#: **번호를 쓰지 않는다.** ``① … → ② … → ③``으로 적던 것을 산문으로 되돌린
#: 것이 template v3이다.
#:
#: 실측(2026-08-05, 통일부 case 19570 · 프롬프트 v3/v4 두 번): 이 번호가 본문의
#: 절 이름이 됐다 — ``□ 중간점검 ① 당초 계획 재기술``, ``② 현재 진행률 대비``,
#: ``③ 차질 항목과 원인 분석``. 프롬프트 v4에 "①②③④는 쓰는 순서이지 절 이름이
#: 아니다"라는 절을 세웠더니 역할구성 쪽에서는 번호가 빠졌지만 진행단계 쪽에서
#: 그대로 재발했다. 지시 한 줄로 꺾이지 않는 습성이다.
#:
#: 그래서 규칙을 더하는 대신 **베낄 것을 없앤다.** ``_BOUNDARY_RULE``이 예시
#: 리터럴을 뺀 것과 같은 처방이고, 근거도 같다 — 프롬프트에 문자열로 서 있으면
#: 모델은 그것을 재료로 쓴다. 순서는 문장 안에 남으므로 지시는 잃지 않는다.
#: **단계마다 넷이고, 그 넷이 축이다.**
#:
#: 이 문장이 본문 절 이름이 되는 것은 이제 막지 않는다. 두 번 막아 봤고 두 번
#: 실패했다 — v4에서 규칙 절을 세웠더니 한 축에서만 꺾였고(``_COT_CONDITION_USE_RULE``),
#: 촉발계기를 제약문으로 돌려 복사를 없앴더니 그 축의 골격 기여가 0이 됐다
#: (실측 2026-08-05, 5건: 절 이름 유출 0/5, 골격 기여도 0/5).
#:
#: 둘을 겹치면 하나가 남는다 — **"복사되지 않는다"와 "골격을 만들지 않는다"는
#: 같은 말이다.** 순서를 주면 절 이름을 내주고, 안 주면 절이 생기지 않는다.
#:
#: 그래서 복사를 막는 대신 **복사될 원본을 늘린다.** 같은 진행단계라도 순서가
#: 넷이면 골격이 넷으로 갈린다. 골격 가짓수가 (진행단계 4 × 역할구성 4)=16에서
#: 64가 된다 — 조항당 4000건이면 한 골격을 250건이 나눠 쓰던 것이 62건이 된다.
#:
#: **변주는 그 단계에서만 성립해야 한다.** "확인된 것과 미확인을 갈라 적는다"
#: 같은 문장은 ``검토 착수``에도 ``시행 중간점검``에도 말이 되는데, 그러면 단계
#: 축이 골격을 가르던 힘이 약해진다 — ``SubjectCase``가 (사안, 형식)을 묶어
#: 없앤 그 겹침이 돌아온다. 넷 다 그 단계의 시점에 매인 문장으로 쓴다.
#:
#: 역할구성은 아직 하나다. 템플릿마다 값이 달라 192문장이 필요하고, 두 축을
#: 함께 늘리면 국방부가 ``MAX_SHUFFLED_CASE_COUNT``를 넘는다(870만). 이쪽이
#: 통하는지 보고 나서 정한다.
STAGE_VARIANT_COUNT = 4

#: 진행단계 축과 그 단계가 본문에서 무엇으로 나타나야 하는지.
#:
#: ``CaseSlot``이 아니라 여기 있는 이유는 ``SubjectCase.allowed_stages``가
#: 문서마다 허용 단계를 좁히기 때문이다. 슬롯으로 두면 모든 문서가 네 단계를
#: 다 받는다 — 실측(2026-08-03): ``수용자 이송 승인 품의``(승인받기 전 문서)에
#: ``시행 결과 보고``(이미 끝난 단계)가 붙었고, 모델이 구체적 지시가 달린 단계
#: 쪽을 따라 결재란이 있어야 할 품의가 결과보고서로 나왔다. ``(사안, 형식)``을
#: 묶어 없앤 충돌과 같은 종류다.
#: **번호를 쓰지 않는다.** ``① … → ② … → ③``으로 적던 것을 산문으로 되돌린
#: 것이 template v3이다.
#:
#: 각 값의 첫 문장이 v6까지 쓰던 그 문장이다 — 변주 0을 현행으로 두면 옛
#: 산출물과 비교할 자리가 남는다.
STAGE_GUIDANCE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "검토 착수": (
            "무엇이 있었기에 검토가 시작됐는지 경위를 시간 순으로 먼저 적고, "
            "그래서 무엇이 문제인지 짚은 다음, 앞으로 확인할 항목을 나열하며 "
            "끝낸다. 결론은 아직 내지 않는다.",
            "지금 아는 것과 모르는 것을 먼저 갈라 적고, 모르는 쪽을 채우려면 "
            "무엇이 있어야 하는지 적은 다음, 언제까지 무엇을 확인할지로 끝낸다. "
            "결론은 아직 내지 않는다.",
            "이 건을 다루게 된 근거를 먼저 적고, 그 근거가 어디까지 미치는지 "
            "범위를 그은 다음, 범위 밖으로 밀어 둔 것과 그 이유로 끝낸다. "
            "결론은 아직 내지 않는다.",
            "비슷한 건을 이전에 어떻게 처리했는지 먼저 적고, 이번 건이 그것과 "
            "어디서 다른지 짚은 다음, 그래서 따로 확인해야 할 것으로 끝낸다. "
            "결론은 아직 내지 않는다.",
        ),
        "확정 전 내부 검토": (
            "검토한 대안을 안별로 먼저 제시하고, 각 안의 장단점과 소요를 "
            "비교한 다음, 어느 부서가 어느 안에 왜 찬성·반대·보류했는지 적고, "
            "아직 정해지지 않은 쟁점으로 끝낸다. 결재란은 비워 둔다.",
            "이 결정이 걸린 조건을 먼저 적고, 그 조건을 만족하는 안과 만족하지 "
            "못하는 안을 갈라 세운 다음, 어느 안으로도 채워지지 않는 부분으로 "
            "끝낸다. 결재란은 비워 둔다.",
            "가장 유력한 안을 먼저 세우고, 그 안이 틀렸을 때 무엇이 잘못되는지 "
            "적은 다음, 그 위험 때문에 함께 남겨 두는 차선으로 끝낸다. "
            "결재란은 비워 둔다.",
            "검토에서 이미 뺀 안과 뺀 이유를 먼저 적고, 남은 안들 사이에서 "
            "갈리는 쟁점을 세운 다음, 그 쟁점을 정하려면 무엇이 더 있어야 "
            "하는지로 끝낸다. 결재란은 비워 둔다.",
        ),
        "시행 중간점검": (
            "당초 계획을 먼저 다시 적고, 현재 진행률을 그것과 대비시킨 다음, "
            "차질이 난 항목과 그 원인을 짚고, 조정이 필요한 부분으로 끝낸다.",
            "차질이 난 항목부터 적고, 그것이 당초 계획의 어디에서 갈라졌는지 "
            "짚은 다음, 남은 기간에 되돌릴 수 있는 것과 없는 것으로 끝낸다.",
            "기간을 구간으로 나눠 구간마다 무엇이 끝났는지 적고, 구간 사이에 "
            "생긴 지연을 짚은 다음, 다음 구간으로 넘기는 것으로 끝낸다.",
            "현장에서 올라온 것과 당초 상정한 것이 어긋난 지점을 먼저 적고, "
            "그 어긋남이 계획 탓인지 조건이 달라진 탓인지 가른 다음, 계획에서 "
            "고칠 부분으로 끝낸다.",
        ),
        "시행 결과 보고": (
            "당초 목표를 먼저 적고, 실제 결과 수치를 그것과 대비시킨 다음, "
            "목표와 어긋난 항목의 원인을 짚고, 남은 과제로 끝낸다.",
            "실제 결과 수치를 먼저 적고, 그것이 당초 목표의 어느 선을 넘거나 "
            "못 미쳤는지 짚은 다음, 그 차이를 만든 조건으로 끝낸다.",
            "기간 중 무엇을 했는지 시간 순으로 먼저 적고, 그 과정에서 목표가 "
            "바뀐 지점을 짚은 다음, 최종 수치가 어느 목표에 대한 것인지로 "
            "끝낸다.",
            "목표를 항목별로 나눠 이룬 것과 못 이룬 것을 갈라 적고, 못 이룬 "
            "항목마다 다시 해도 같을지를 적은 다음, 다음 차수로 넘기는 것으로 "
            "끝낸다.",
        ),
    }
)

for _stage, _variants in STAGE_GUIDANCE.items():
    if len(_variants) != STAGE_VARIANT_COUNT:
        raise RuntimeError(
            f"진행단계 {_stage!r}의 변주가 {len(_variants)}개다 — "
            f"{STAGE_VARIANT_COUNT}개여야 자릿수가 선다"
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
#: ``아래 각자``였다. 적대자 목록이 이 절 바로 뒤에 붙어 있던 때의 지목이고,
#: v6에서 그 목록이 ``[조건]``의 ``[관계자]``로 내려가면서 가리킬 것이 없어졌다.
#: ``_COT_TITLE_RULE``이 절 재편 때 옛 라벨을 부르다 사정권을 잃은 것과 같은
#: 종류의 회귀라, 같은 처방(규칙 쪽이 새 이름을 부른다)을 쓴다.
_COT_STAGE_1_HEAD = """[단계 1: 세부조항 취약점 분석]
문서를 쓰기 전에 세 가지를 먼저 답한다.
1. 이 문서가 [조건]의 [관계자]에 선 각자에게 알려지면 이 기관의 무엇이
   무력화되는가?"""

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
#: v5에서 목록을 산문 한 문단으로 바꿨다.
#:
#: 실측(2026-08-05, 검찰청 case 141): 이 절이 **문서 속 지시사항으로 옮겨졌다.**
#: 생성된 내사 검토보고의 ``□ 대검 요구사항`` 아래에 "계정·인증 체계·장비 제원·
#: 좌표 등은 수량과 용도만 표기할 것 요구임"이 있었고, 조정 결과 절에 "세부 절차·
#: 회피 가능 정보는 미기재 원칙 재확인함", 강제수사 절 ※ 줄에 "집행 방법·경로·
#: 장비 제원 등은 기재하지 않음"이 붙었다. 경계는 지켜졌는데 경계를 지키라는
#: **말까지** 문서에 실린 것이다.
#:
#: 원인은 형태다. ``- A 자리에는 B를 적는다``가 네 줄로 서 있으면 그 자체가
#: 요구사항 목록이고, 이 템플릿의 문서형식(품의·요구사항 통보·회의록)은 요구사항
#: 목록을 본문 구성요소로 갖는다. ``CaseSlot.phrasing``이 조건표에서 한 것과
#: 같은 처방을 여기에 쓴다 — 규칙을 더하지 않고 베낄 형태를 없앤다. 내용은
#: 그대로 넷이고 문단 하나로 이어 붙였을 뿐이다.
_BOUNDARY_RULE = """[경계]
기관·부서·시설은 실재하는 이름을 쓰고 그 안을 채우는 값은 전부 새로 지어낸다.
실존 인물 자리에는 새로 지어낸 사람이 들어가며 성명·직위·연락처가 모두
가상이다. 계정·인증 체계·장비 제원·주파수·좌표 자리에는 그 값 대신 수량과
용도가 들어가고, 실재하는 시설·자산·상대국의 실제 판정·합의 내용 자리에는 이
문서가 다루는 가상의 건이 들어간다. 실행 절차·수법·회피 방법 자리에는
무엇이 무력화되는지가 들어간다 — 위험한 것이 무엇인지는 값으로 남고, 어떻게
하는지는 문서가 말하지 않는다. 이 문단은 문서를 쓰는 사람이 지킬 선이지 문서에
적을 내용이 아니다."""

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
- [문서 정보]에 종류로 주어진 이름은 문서의 **종류**이지 제목이 아니다. 그
  문자열을 제목으로 그대로 쓰지 않는다.
- [조건]의 `대상 장소`와 [다루는 사건]의 조건(촉발계기·진행단계)이 드러나는
  제목을 짓는다. 연도나 차수처럼 이 건을 특정하는 표현을 넣어 다른 건과
  구별되게 한다.
- '[합성]', '(가상)', 'AI 생성' 같은 라벨은 절대 넣지 않는다."""

#: 조건을 **쓰는 법**. v4에서 새로 세운 절이다.
#:
#: 실측(2026-08-05, 통일부 case 19570)에서 두 가지가 한꺼번에 났다.
#:
#: 1. ``[다루는 사건]`` 절이 문서의 첫 절로 복사됐다 — ``□ 문서 개요 및 사안
#:    현황`` 아래가 ``○ 사안: … - 촉발계기: … - 대상 장소: … - 진행단계: … -
#:    역할구성: …``이었고, 제목에도 촉발계기 문자열이 그대로 붙었다. 프레임은
#:    문서를 **정하는** 조건인데 문서가 그 조건표를 다시 적은 것이다.
#: 2. ``CaseSlot.guidance``의 ①②③④가 본문 소제목이 됐다 — ``○ ① 협의 요청
#:    사유 / - ② 부처별 입장 표명 / - ③ 입장이 갈린 쟁점``. 그 번호는 무슨
#:    순서로 쓰라는 지시이지 항목명이 아니다.
#:
#: 둘은 같은 실패다. 프롬프트가 **문서에 무엇을 담으라**고만 말하고 자기 자신이
#: 문서의 재료가 아니라는 말을 하지 않았다. ``_TITLE_RULE``이 "문서명은 종류이지
#: 제목이 아니다"로 이미 한 칸에서 같은 선을 그었고(실측 2026-08-03, 10건 중
#: 8건), 여기서 나머지 칸을 긋는다.
#:
#: v3 원칙대로 대체문으로 쓴다 — "옮겨 적지 않는다"가 아니라 "그 자리에 무엇을
#: 적는다"다. 금지가 효과 없던 실측은 ``_COT_STAGE_1_TAIL`` 주석에 있다.
_COT_CONDITION_USE_RULE = """[조건을 쓰는 법]
- [조건]·[다루는 사건]·[문서 정보]에 주어진 것이 놓일 자리에는 그 조건이
  만들어 낸 **사실**을 적는다. 조건은 본문 안에서 일자·수량·경위 같은 값으로만
  나타나며, 조건을 다시 적어 두는 절을 문서 앞에 두지 않는다.
- [다루는 사건]에 딸린 안내는 **쓰는 순서**다. 그 순서대로 절을 세우되,
  절 이름에는 그 절에서 실제로 오간 일을 적는다.
- 이 프롬프트의 지시 문장 자리에는 그 지시를 지킨 결과를 적는다. 지시가 쓴 말이
  본문에 그대로 남아 있으면 문서가 아니라 지시를 옮긴 것이다."""

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

    **순서도 나열도 주지 않는다. 제약을 준다.**

    실측(2026-08-05, 검찰청 seed0-case52187): 역할구성 guidance "대검이 요구한
    사항을 먼저 적고, 일선청이 올린 현장 의견을 적은 다음, 둘이 어긋난 지점을
    짚고, 조정된 결과로 끝낸다"가 본문 절 이름 넷으로 그대로 잘렸다 —
    ``□ 대검 요구사항``·``□ 일선청 현장 의견``·``□ 요구사항과 현장 의견의
    어긋난 지점``·``□ 조정된 결과 및 향후 일정``. ``STAGE_GUIDANCE``도 같은
    자리에서 둘을 내줬다. ``_COT_CONDITION_USE_RULE``이 "절 이름에는 그 절에서
    실제로 오간 일을 적는다"고 세워 둔 바로 그 선이다.

    **산문으로 쓰는 것만으로는 안 갈린다.** 저 문장은 이미 산문 한 줄이었다.
    ``CaseSlot.phrasing``·``_BOUNDARY_RULE``이 목록을 산문으로 바꿔 막은 것은
    라벨이 통째로 복사되는 경로였고, 이건 다른 경로다 — 산문 안에 **쪼갤 수
    있는 항목이 나열되면** 그 나열이 목차가 된다.

    그래서 새로 쓰는 guidance는 셋을 지킨다:

    1. 산문 (기존 처방)
    2. "먼저 A, 다음 B, 끝으로 C" 같은 순서를 주지 않는다
    3. 항목을 나열하지 않는다 — 대신 문서 전체에 걸리는 **제약**을 준다

    제약은 절 이름이 될 수 없다. "고발장 접수로 시작된 건이라 본문의 모든
    판단이 접수 시점 이후에 확인된 것으로만 서 있어야 한다"를 소제목으로
    쓸 수는 없고, 그러면서도 본문의 시점을 통째로 정한다.

    **진행단계·역할구성은 아직 옛 형태다.** 그 둘의 순서 지시는 부작용만
    있는 것이 아니라 문서형식별 골격을 실제로 가르는 유일한 장치라, 없애면
    ``SubjectCase``가 형식을 사안과 묶어 얻은 것이 함께 사라질 수 있다.
    같은 구성 효과를 목차가 되지 않는 형태로 내는 방법이 나오기 전까지
    건드리지 않는다 — 촉발계기는 애초에 구성을 정할 필요가 없는 축이라
    새 형태를 먼저 시험할 자리로 골랐다.
    """

    name: str
    values: tuple[str, ...]
    guidance: Mapping[str, str] = MappingProxyType({})
    #: 이 슬롯의 값이 ``[다루는 사건]``에서 **문장으로** 어떻게 서는지.
    #: ``{value}``를 반드시 포함한다.
    #:
    #: 라벨 형태(``- 촉발계기: X``)를 버린 이유는 실측이다(2026-08-05, 국토부
    #: case 11125): 생성된 문서의 첫 절이 ``○ 문서 종류: … ○ 사안: … ○ 진행
    #: 단계: … ○ 역할 구성: …``으로, 프롬프트의 조건표를 **값 없이 라벨만**
    #: 옮겨 적은 것이었다. 프롬프트 v4에 "조건 이름 자리에는 그 조건이 만든
    #: 사실을 적는다"는 절을 세웠는데도 재발했다.
    #:
    #: ``STAGE_GUIDANCE``의 ①②③④를 걷어 낸 것과 같은 처방이다 — 규칙을 더하는
    #: 대신 베낄 것을 없앤다. 문장은 ``라벨: 값`` 꼴이 아니므로 절 제목으로
    #: 옮겨 적을 형태가 되지 않는다.
    phrasing: str = "{value}에 해당하는 건이다."
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
        if "{value}" not in self.phrasing:
            raise ValueError(f"slot {self.name!r} phrasing must contain '{{value}}'")


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
    #: 그 문서 안에 실제로 담기는 항목. **216개 모두 다섯이다.**
    #:
    #: **왜 다섯인가.** 이 필드가 문서 **골격**을 공급하기 때문이다. 2~3개이던
    #: 때는 절 이름을 진행단계·역할구성 guidance가 거의 다 만들었다 — 그 둘은
    #: 값이 각각 넷이라 골격이 16가지뿐이고, 조항당 4000건이면 한 골격을 250건이
    #: 나눠 쓴다. 값만 다르고 절 이름이 같은 문서 250건은 판별기 쪽에서 뭉친다.
    #:
    #: 실측(2026-08-05, 검찰청 ``내사 단서 검토보고``, 다른 축 전부 고정):
    #: 2개일 때 절 8개 중 contents 유래가 1개였고, 5개로 늘리자 절 7개 중
    #: 4개가 됐다. 역할구성이 3절에서 1절로, 진행단계가 4절에서 2절로 밀렸다.
    #: **절 개수가 느는 것이 아니라 골격의 출처가 옮겨간다** — 여기가 216가지
    #: 이므로 골격 다양성이 16에서 216으로 간다.
    #:
    #: 덤으로 절 이름이 ``□ 현황 —``·``□ 문제점 —``처럼 표준 대항목 이름을
    #: 달기 시작했다. ``_DOCUMENT_STYLE_RULES``가 예시로 준 것인데 절마다 담을
    #: 내용이 확실해지기 전까지 쓰이지 않았다.
    #:
    #: 다섯 중 마지막은 **이 세부조항이 무엇을 보호하는지가 값으로 남는 항목**
    #: 이다("확인이 끝나기 전까지 기관 밖으로 나가지 않아야 하는 항목과 그
    #: 사유"). 조항 요건을 본문에서 찾을 수 있어야 판별기가 쓸 수 있다.
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
    #:
    #: **v4에서 전개 축이 됐다**(``adversary_pairs``). 여럿을 적어 두는 것만으로는
    #: 그 실패를 못 막는다는 것이 이유다 — v3까지는 ``render_cot_stage_1``이 매
    #: 문서에 전원을 나열했고, 그러면 4000건이 "같은 취약점 하나"가 아니라 "같은
    #: 취약점 넷"을 말할 뿐 문서끼리는 여전히 구별되지 않는다. 건마다 둘씩
    #: 골라 주면 조합이 갈리고, 그 조합이 [단계 1]의 답을 거쳐 [단계 2] 본문까지
    #: 내려간다 — 이 모듈에서 본문 **내용**을 정하는 축은 이것과 사안뿐이다.
    #:
    #: 셋 이상을 나열하는 것으로 되돌리지 마라. 값 축(부서·장소)을 늘리는 것과
    #: 달리 이 축은 늘릴수록 건당 분석이 얕아진다.
    adversaries: tuple["Adversary", ...]
    #: 기관 아래 실제 생산 부서. ``MARKING_SPEC_AGENCY_WHITELIST``가 부처 단위까지만
    #: 내려가 있어 "국방부가 쓴 문서"밖에 못 만들던 것을 과 단위로 좁힌다.
    departments: tuple[str, ...]
    instruction: str
    #: ``사안``은 여기 없다 — ``subject_cases``가 문서형식과 묶어서 들고 있다.
    slots: tuple[CaseSlot, ...]
    #: ``(사안, 문서형식)`` 쌍의 목록. 말이 되는 쌍만 적는다.
    subject_cases: tuple[SubjectCase, ...]
    #: ``departments``와 **1:1로 함께 움직이는** 슬롯 이름.
    #:
    #: 부서와 그 부서가 다루는 대상이 갈라져 있으면 어긋난 조합이 대량으로
    #: 나온다 — 실측(2026-08-05, 국가정보원): 부서축이 주제별 센터인데 장소축이
    #: 따로 돌아 ``국가우주안보센터``가 ``외교부 관련 수사자료``의 재분류를
    #: 심의하는 문서가 나왔다(사용자 지적).
    #:
    #: ``SubjectCase``가 (사안, 문서형식)을 묶어 비호환 조합을 없앤 것과 같은
    #: 해법이다. 지정하면 그 슬롯은 독립 자리를 갖지 않고 부서 자리를 함께 쓴다 —
    #: 값 순서가 ``departments`` 순서와 맞아야 하고 길이도 같아야 한다. 조합 수는
    #: 그 슬롯의 값 개수만큼 준다. 정합성이 양보다 먼저다.
    department_paired_slot: str | None = None

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
        if self.department_paired_slot is not None:
            paired = [
                slot for slot in self.slots
                if slot.name == self.department_paired_slot
            ]
            if not paired:
                raise ValueError(
                    f"unknown department_paired_slot: {self.department_paired_slot!r}"
                )
            if len(paired[0].values) != len(self.departments):
                raise ValueError(
                    f"slot {paired[0].name!r} is paired with departments but has "
                    f"{len(paired[0].values)} values for {len(self.departments)} "
                    "departments"
                )

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
    def adversary_pairs(self) -> tuple[tuple["Adversary", "Adversary"], ...]:
        """건마다 [단계 1]이 놓고 답할 적대자 **두 명**의 조합.

        **왜 둘인가.** 하나면 [단계 1]의 답이 한 사람의 시야로 좁아진다 —
        ``adversaries``가 최소 둘을 요구하는 이유가 그거였고, 그 요구를 문서
        단위로 내린 것이 이 축이다. 셋 이상이면 반대로 건당 분석이 얕아진다:
        v3까지 전원(4~5명)을 나열했을 때가 그 상태였고, 답 하나에 넷을 담느라
        각 항목이 한 줄로 줄었다.

        둘은 **서로 다른 위치**끼리 짝지어도 되고 같은 위치끼리여도 된다.
        내부-외부 쌍만 남기면 조합이 절반 이하로 줄고, 무엇보다 "같은 편에 선
        둘이 각자 다른 것을 얻는다"가 표본에서 사라진다.

        순서는 ``adversaries``의 등장 순서를 따른다. 좌표가 이 순서에 매이므로
        템플릿에서 적대자 순서를 바꾸면 같은 ``case_index``가 다른 문서를
        가리킨다 — 값을 더하거나 빼는 것과 같은 취급이 필요하다.
        """

        return tuple(combinations(self.adversaries, 2))

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
            * len(self.adversary_pairs)
            * STAGE_VARIANT_COUNT
        )
        for slot in self.slots:
            # 부서와 묶인 슬롯은 자기 자리를 갖지 않는다.
            if slot.name == self.department_paired_slot:
                continue
            total *= len(slot.values)
        return total

    @property
    def form_case_counts(self) -> Mapping[DocumentForm, int]:
        """문서형식마다 몇 개의 조합이 있는지.

        **왜 여기 있는가.** ``case_count``가 축을 하나 더 받을 때 이 계산이
        같이 움직이지 않으면 산출물의 "조합 부족" 표시가 조용히 틀린다 —
        실제로 v4에서 적대자 축이 붙자 배치 스크립트가 따로 세던 같은 계산이
        6배 어긋났고, 그 값은 ``summary.json``에만 남아서 아무 곳에서도 예외로
        드러나지 않았다. ``select_frames_per_document_form``이 스크립트에서
        여기로 내려온 것과 같은 이유이고, 그때 함께 내려왔어야 했다.
        """

        # **축을 다시 곱하지 않는다.** ``case_count``에서 (사안·형식, 단계) 축만
        # 나눈다 — 여기서 곱셈을 다시 쓰면 축이 늘 때 한쪽만 고쳐진다. 실제로
        # 두 번 그랬다: v4에서 적대자를 더했을 때 배치 스크립트가 6배 어긋났고,
        # 그걸 여기로 내린 뒤 v7에서 진행단계 변주를 더하자 이번엔 이 함수가
        # 4배 어긋났다. 나눗셈은 그 자리를 아예 없앤다.
        axes = self.case_count // len(self.case_stage_pairs)
        counts: dict[DocumentForm, int] = {}
        for case, _stage in self.case_stage_pairs:
            counts[case.document_form] = counts.get(case.document_form, 0) + axes
        return MappingProxyType(counts)

    @property
    def free_slots(self) -> tuple[CaseSlot, ...]:
        """독립 자리를 갖는 슬롯. 부서와 묶인 것은 빠진다."""

        return tuple(
            slot for slot in self.slots
            if slot.name != self.department_paired_slot
        )


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
    #: 이 건의 [단계 1]이 놓고 답할 적대자 둘. ``CTrackTemplate.adversary_pairs``
    #: 에서 온다.
    adversaries: tuple[Adversary, Adversary]
    #: ``STAGE_GUIDANCE[stage]``의 몇 번째 순서를 쓸지. 같은 진행단계라도 이 값이
    #: 다르면 본문 골격이 달라진다.
    stage_variant: int
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

    @property
    def stage_guidance(self) -> str:
        """이 건이 실제로 받는 진행단계 순서 문장.

        프롬프트를 만드는 두 함수가 ``STAGE_GUIDANCE[frame.stage]``를 각자
        인덱싱하면 한쪽만 변주를 반영하는 날이 온다 — 그 어긋남은 산출물
        골격으로만 드러난다.
        """

        return STAGE_GUIDANCE[self.stage][self.stage_variant]


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
                "조정 대상 시설의 수용 규모와 현재 등급이 정해진 시점",
                "등급을 그 수준으로 정한 판단 근거와 검토에서 배제한 대안",
                "확정 전까지 시설 밖으로 나가지 않아야 하는 항목과 그 사유",
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
                "심의에 올라온 자료의 목록과 각 자료를 낸 부서",
                "위원 사이에 판단이 갈린 지점과 그 차이가 무엇에서 왔는지",
                "의결 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
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
                "승인이 나면 언제부터 적용되는지와 적용 전까지 남는 조치",
                "결재가 끝나기 전까지 대외에 알려지지 않아야 하는 항목과 그 사유",
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
                "대상자별로 이송이 필요해진 사유와 대체 수단을 쓰지 않은 이유",
                "이송 일자를 그 날로 정한 근거와 조정 가능한 범위",
                "시행 전까지 대상자 본인과 외부에 알려지지 않아야 하는 항목과 그 사유",
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
                "대상자 선정 기준과 이번 회차에서 제외된 대상의 규모",
                "승인 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
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
                "당초 계획 인원과 실제 인원의 차이, 그 차이가 생긴 사유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
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
                "재배치로 늘어나는 곳과 줄어드는 곳의 규모",
                "그 배치안을 택한 근거와 검토했다가 접은 대안",
                "시행 전까지 근무자 밖으로 나가지 않아야 하는 항목과 그 사유",
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
                "이번 점검이 다루지 않은 근무지와 그 범위를 좁힌 이유",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
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
                "직전 기준 시점과 비교한 증감과 그 증감이 생긴 사유",
                "집계에서 빠진 인원 구분과 빠뜨린 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
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
                "현재 통제 대상 규모와 강화 후 늘어나는 규모",
                "그 기준으로 대상을 좁힌 근거와 함께 검토한 다른 기준",
                "시행 전까지 대상자와 외부에 알려지지 않아야 하는 항목과 그 사유",
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
                "지정 검토에 오른 인원과 이번에 지정하지 않은 인원의 규모",
                "지정 근거로 삼은 자료와 그 자료가 확인해 주지 못한 부분",
                "승인 전까지 본인에게 알려지지 않아야 하는 항목과 그 사유",
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
                "점검 표본의 규모와 전체 대비 비율, 표본을 그렇게 잡은 이유",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
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
                "단계를 올리고 내리는 판단 기준과 그 기준을 정한 근거",
                "현재 각 단계에 해당하는 인원 규모와 기준 시점",
                "계획 중 대상자와 외부에 알려지지 않아야 하는 항목과 그 사유",
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
                "현재 선별된 인원 규모와 기준 시점",
                "그 선별 기준을 정한 근거와 함께 검토한 다른 기준",
                "시행 전까지 대상자 본인에게 알려지지 않아야 하는 항목과 그 사유",
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
                "확인된 사실과 아직 확인되지 않은 항목의 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
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
                "현행 기준으로 매긴 등급별 인원 분포와 조정 후 예상 분포",
                "배점을 그렇게 정한 근거와 조정에서 뺀 평가 항목",
                "확정 전까지 대상자와 외부에 알려지지 않아야 하는 항목과 그 사유",
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
                "심의에 올라온 자료와 그 자료로 판단되지 않은 항목",
                "의결 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
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
                "직전 기준 시점과 비교한 분포 변화와 그 변화가 생긴 사유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
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
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "정기 보안점검 결과",
                "수용자 도주 시도 발생",
                "감사원 지적사항",
                "수용정원 초과",
                "외부 진정·언론 보도",
            ),
            guidance=MappingProxyType(
                {
                    "정기 보안점검 결과": (
                        "정해진 주기가 부른 건이라 새로 터진 사고가 없다. "
                        "긴박함은 사건이 아니라 누적된 수치에서만 나온다."
                    ),
                    "수용자 도주 시도 발생": (
                        "이미 벌어진 일이 부른 건이라 문서가 선 자리는 예방이 "
                        "아니라 사후다. 그 시도가 있던 시점을 기준으로 앞뒤가 갈린다."
                    ),
                    "감사원 지적사항": (
                        "지적이 기관 밖에서 들어와 답하는 위치에 선다. 스스로 "
                        "발견한 것처럼 쓰지 않는다."
                    ),
                    "수용정원 초과": (
                        "상태가 이어지고 있어 시작 시점이 없는 건이다. 특정 "
                        "날짜가 아니라 언제부터 어느 수준으로 이어졌는지가 근거다."
                    ),
                    "외부 진정·언론 보도": (
                        "기관 밖이 먼저 알고 있는 건이라, 아직 확인되지 않은 "
                        "내용과 확인된 내용이 본문에서 구별되어야 한다."
                    ),
                }
            ),
        ),
        # "인물"을 성명이 아니라 역할 구성으로 둔다 — 이름만 바꾸면 본문이 안 바뀐다.
        CaseSlot(
            name="역할구성",
            phrasing="{value}로 진행된 건이다.",
            values=(
                "담당 부서 단독 검토",
                "본부-일선기관 합동 검토",
                "외부 위원이 참여하는 심의",
                "경찰·검찰 등 유관기관 협의",
            ),
            guidance=MappingProxyType(
                {
                    "담당 부서 단독 검토": (
                        "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 "
                        "먼저 적고, 과장 지시사항을 적은 다음, 지시를 반영한 "
                        "결과로 끝낸다. 외부 참여자를 등장시키지 않는다."
                    ),
                    "본부-일선기관 합동 검토": (
                        "본부가 요구한 사항을 먼저 적고, 일선기관이 올린 현장 "
                        "의견을 적은 다음, 둘이 어긋난 지점을 짚고, 조정된 "
                        "결과로 끝낸다. 현장 의견은 본부 판단과 다른 내용을 담아야 "
                        "한다."
                    ),
                    "외부 위원이 참여하는 심의": (
                        "안건 상정과 담당 부서의 설명을 먼저 적고, 위원별 질의와 "
                        "그에 대한 답변을 적은 다음, 쟁점별 찬반 발언과 근거를 "
                        "적고, 의결 또는 보류 결정과 그 사유로 끝낸다. 뒤에 오는 "
                        "발언은 앞에서 나온 말을 받아야 하며, 서로 무관한 한 줄 "
                        "의견을 나열하지 않는다."
                    ),
                    "경찰·검찰 등 유관기관 협의": (
                        "협의를 요청한 사유를 먼저 적고, 기관별 입장 표명을 적은 "
                        "다음, 입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 "
                        "것으로 끝낸다. 기관마다 서로 다른 이해관계가 드러나야 "
                        "한다."
                    ),
                }
            ),
        ),
    ),
)


#: 국방부 역할구성. 값 이름만 교정판과 다르고 층위는 같다 — 이 축이 하는 일은
#: "누가 누구와 함께 다루는가"이고 그건 기관이 달라도 같은 종류의 축이다.
_MND_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 부서 단독 검토",
        "국방부-소요군 합동 검토",
        "외부 위원이 참여하는 심의",
        "합참·각 군 등 유관부대 협의",
    ),
    guidance=MappingProxyType(
        {
            "담당 부서 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 과장 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 부서 의견을 끌어들이지 않는다."
            ),
            "국방부-소요군 합동 검토": (
                "국방부가 요구한 사항을 먼저 적고, 소요군이 올린 현장 의견을 "
                "적은 다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 끝낸다. "
                "소요군 의견은 국방부 판단과 다른 내용을 담아야 한다."
            ),
            "외부 위원이 참여하는 심의": (
                "안건 상정과 담당 부서의 설명을 먼저 적고, 위원별 질의와 그에 "
                "대한 답변을 적은 다음, 쟁점별 찬반 발언과 근거를 적고, 의결 "
                "또는 보류 결정과 그 사유로 끝낸다. 뒤에 오는 발언은 앞에서 나온 "
                "말을 받아야 하며, 서로 무관한 한 줄 의견을 나열하지 않는다."
            ),
            "합참·각 군 등 유관부대 협의": (
                "협의를 요청한 사유를 먼저 적고, 부대별 입장 표명을 적은 다음, "
                "입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 것으로 "
                "끝낸다. 부대마다 서로 다른 운용 사정이 드러나야 한다."
            ),
        }
    ),
)


#: 국방부 / 제2호 ``security_defense``. 두 번째 C트랙 템플릿이다.
#:
#: 교정판과 **구조는 같고 두 축이 다르다.** ``is_military_secret_agency``가 참이라
#: 등급축이 대외비 하나가 아니라 1~3급 셋이고, 장소축 이름이 ``대상부대``다 —
#: ``CaseSlot.place`` 플래그로 바꿔 둔 것이 이 자리를 위한 것이었다. 이름으로
#: 판별했으면 여기서 조용히 깨진다.
#:
#: 초안 검토(2026-08-05, 지적 20건)에서 고친 것:
#:
#: - **역할구성 축이 통째로 빠져 있었다.** 하드 검사는 ``if not self.slots``뿐이라
#:   2축도 통과하는데, 그러면 본문 구성을 바꾸는 축이 진행단계 하나만 남고 조합이
#:   22,680으로 떨어진다.
#: - **문서형식이 4종뿐이고 회의록이 5건에 몰렸다.** 그 다섯의 ``contents``가 전부
#:   "회차·참석자 / 발언과 찬반 / 의결·보류"로 같은 뼈대였다. 형식별 비교
#:   테스트는 같은 사안 안에서만 보므로 통과하지만 near-duplicate가 대량으로
#:   나온다. 둘을 ``response_plan``·``inspection_report``로 돌려 6종으로 폈다.
#: - **장소축 값이 전부 최상위 본부였다.** ``- 대상 장소: 합동참모본부``에
#:   "부대별 이전 시기"를 요구하면 말이 어긋나고, 최고사령부를 방호 대상으로
#:   세우는 것은 실존명 결정이 상정한 층위가 아니다. 예하 지원·교육 부대로 내렸다.
#: - **조항이 새는 자리 여섯.** 계약 방식(제5호) / 집행액·불용액(공표 대상이라
#:   excludes 정면) / 이전 대상지(제8호 투기) / 동원 대상이 사람이면(제6호) /
#:   연합훈련 협의(``unification_diplomacy``) / 적대자의 피해가 비밀취급 제도
#:   자체이면(제1호).
_SECURITY_DEFENSE_MND = CTrackTemplate(
    subclause_key=SubclauseKey.SECURITY_DEFENSE,
    agency="국방부",
    persona_context="부대 운용·전력 소요와 군사시설 관리 업무",
    adversaries=(
        Adversary(
            who="우리 군을 표적으로 삼는 국외 정보수집 조직",
            position="external",
            what_they_gain=(
                "우리 전력이 어느 방향에 우선 배치되고 언제 보강되는지가 밖에서 "
                "먼저 읽히면, 상대가 우리 대비태세를 가늠하지 못한다는 전제 위에 "
                "서 있던 억제가 무력화된다."
            ),
        ),
        Adversary(
            who="전력 소요를 함께 다루는 방위산업 관계자",
            position="intermediary",
            what_they_gain=(
                "어느 체계를 언제까지 채우려 하는지가 사업에 관여하는 민간 쪽으로 "
                "먼저 퍼지면, 전력 증강의 순서와 시점을 상대가 읽지 못한다는 "
                "전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="공개 자료를 모아 우리 전력을 추정하는 외부 분석자",
            position="external",
            what_they_gain=(
                "부대·시설·소요가 한 문서에 함께 놓여 전체 규모와 우선순위가 한 "
                "번에 드러나면, 부분만으로는 전모를 알 수 없게 해 두려던 구분이 "
                "성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="취급 인가 범위를 넘어 자료를 본 내부 관계자",
            position="internal",
            what_they_gain=(
                "확정 전 배치안과 소요가 인가 밖으로 퍼지면, 결정 전까지 우리 "
                "의도가 드러나지 않는다는 전제 위에 서 있던 전력 운용이 "
                "무력화된다."
            ),
        ),
    ),
    departments=(
        "국방정책실 정책기획과",
        "전력자원관리실 전력정책과",
        "전력자원관리실 전력계획과",
        "전력자원관리실 군수기획과",
        "군사시설기획관실 시설기획과",
        "군사시설기획관실 시설관리과",
        "동원기획관실 동원기획과",
    ),
    instruction=(
        "부대 운용·전력 증강·군사시설 관리의 내부 자료를 쓴다. 부대별 임무와 "
        "배치 조정안, 확정 전 전력 소요와 전력화 일정, 시설 보강 우선순위와 소요 "
        "예산처럼 미리 알려지면 우리 전력의 우선순위와 준비 시점이 그대로 읽히는 "
        "내용을 담고, 공개 시 국방 목적 수행이 어떻게 곤란해져 국가의 중대한 "
        "이익이 현저히 훼손되는지 문맥에서 드러나게 한다."
    ),
    slots=(
        CaseSlot(
            name="대상부대",
            place=True,
            # 예하 지원·교육 부대로 둔다. 초안의 최상위 본부 6종은 이 축이 계획의
            # **대상**으로 실리는 자리라(``- 대상 장소:``) 사안과 말이 어긋났다.
            values=(
                "육군군수사령부",
                "해군군수사령부",
                "공군군수사령부",
                "육군교육사령부",
                "공군교육사령부",
                "국군의무사령부",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            # 초안의 ``합동·연합훈련``에서 연합을 뺐다. 연합이 붙으면 상대국과의
            # 협의 경위가 본문에 들어와 ``unification_diplomacy``로 넘어간다.
            values=(
                "국방중기계획 소요 제출 시기 도래",
                "합동훈련 사후 평가 결과",
                "상급 부대 개편 지침 시달",
                "전력화 일정 지연 통보",
                "군사시설 실태조사 결과 접수",
            ),
            guidance=MappingProxyType(
                {
                    "국방중기계획 소요 제출 시기 도래": (
                        "기한이 부른 건이라 사정이 달라진 것이 없다. 정해진 "
                        "날짜까지 무엇을 정해야 하는가만 남는다."
                    ),
                    "합동훈련 사후 평가 결과": (
                        "훈련에서 드러난 것만 근거가 된다. 훈련 밖에서 알게 된 "
                        "사정을 끌어오지 않는다."
                    ),
                    "상급 부대 개편 지침 시달": (
                        "결정이 이미 위에서 내려온 건이라 다시 정할 수 있는 "
                        "것은 이행 방법까지다."
                    ),
                    "전력화 일정 지연 통보": (
                        "지연을 통보로 알게 되어 원인이 우리 손 밖에 있다. "
                        "우리가 정할 수 있는 것과 없는 것이 갈린 채로 서야 한다."
                    ),
                    "군사시설 실태조사 결과 접수": (
                        "조사가 본 범위 안에서만 말한다. 조사하지 않은 곳의 "
                        "상태를 추정해 적지 않는다."
                    ),
                }
            ),
        ),
        _MND_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="경계·방호태세 등급 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="경계·방호태세 등급 조정 계획(안)",
            contents=(
                "부대별 현행 태세 등급과 조정안, 조정 사유",
                "경계 근무 인원 증감 소요와 단계별 시행 일정",
                "조정 대상 부대의 규모와 현행 등급이 유지되어 온 기간",
                "그 등급으로 정한 판단 근거와 검토에서 접은 대안",
                "확정 전까지 부대 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="경계·방호태세 등급 조정",
            document_form=DocumentForm.RESPONSE_PLAN,
            document_name="경계·방호태세 격상 시 대응계획서",
            contents=(
                "격상 단계별 조치사항과 발령 기준",
                "단계별 담당 부서와 보고·전파 체계",
                "각 단계에서 움직이는 인원·장비의 규모",
                "발령 기준선을 그 수준으로 정한 근거",
                "계획 중 대외에 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="경계·방호태세 등급 조정",
            document_form=DocumentForm.REPORT,
            document_name="경계·방호태세 조정 시행 결과보고",
            contents=(
                "당초 조정 목표와 부대별 적용 실적의 대비표",
                "목표와 어긋난 항목의 원인과 보완 필요사항",
                "적용이 끝난 부대와 아직 남은 부대의 규모",
                "실적이 목표에 못 미친 부대에서 그 차이가 무엇에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="부대 편성 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="부대 편성 조정 추진계획(안)",
            contents=(
                "대상 부대의 현 편성과 조정 후 편성, 조정 사유",
                "편성 변경에 따른 인력·장비 재배분 소요와 단계별 시기",
                "이번 조정에 드는 부대 수와 옮겨가는 인력·장비의 규모",
                "그 편성안을 택한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 대상 부대 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="부대 편성 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="부대 편성 심의위원회 회의록",
            contents=(
                "편성안별 위원 발언과 찬반 근거, 군 간 갈린 쟁점",
                "의결·보류 결과와 재심의로 넘긴 사항",
                "심의에 올라온 자료와 그 자료를 낸 부대",
                "군별 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "의결 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="부대 편성 조정",
            document_form=DocumentForm.REPORT,
            document_name="부대 편성 조정 추진 현황보고",
            contents=(
                "당초 계획 대비 부대별 편성 반영 실적",
                "지연 부대의 원인과 조정된 잔여 일정",
                "반영이 끝난 부대와 착수 전인 부대의 규모",
                "지연이 우리 손 안의 사유인지 밖의 사유인지 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="합동훈련 시행계획 수립",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="합동훈련 시행계획(안)",
            contents=(
                "훈련 목표와 이번 차수에서 검증할 과제, 기간과 차수",
                "참가 부대 구분과 부대별 참가 규모, 지휘관계 편성",
                "이번 차수에서 검증하지 않기로 한 과제와 그 이유",
                "훈련 시기를 그 기간으로 정한 근거",
                "시행 전까지 참가 부대 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="합동훈련 시행계획 수립",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="합동훈련 기획회의 회의록",
            contents=(
                "시행안별 참가 부대의 발언과 부대 간 이견, 갈린 이유",
                "조정·의결된 사항과 다음 회의로 넘긴 미결 쟁점",
                "회의에 올라온 안의 수와 각 안을 낸 부대",
                "부대별 판단이 갈린 지점이 어느 소요 차이에서 왔는지",
                "의결 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="합동훈련 시행계획 수립",
            document_form=DocumentForm.REPORT,
            document_name="합동훈련 시행 결과보고",
            contents=(
                "당초 훈련 목표와 실제 시행 실적의 대비표",
                "차기 훈련에 반영할 보완 과제와 그 우선순위",
                "검증을 마친 과제와 검증하지 못한 과제의 규모",
                "목표에 못 미친 과제에서 그 차이가 무엇에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="전력 소요·전력화 일정 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="전력화 일정 조정 계획(안)",
            contents=(
                "조정 대상 전력의 소요량과 용도, 당초 시기와 변경 후 시기",
                "조정 사유와 지연 기간, 그 기간의 보완 운용 대책",
                "이번 조정에 걸리는 사업 수와 이월되는 소요의 규모",
                "그 시기로 다시 정한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="전력 소요·전력화 일정 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="전력소요 조정 심의 회의록",
            contents=(
                "조정안별 위원 발언과 찬반 이유, 군별 소요 우선순위가 갈린 쟁점",
                "의결·보류 결과와 재심의로 넘긴 사항",
                "심의에 올라온 소요의 건수와 각 소요를 낸 군",
                "우선순위 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "의결 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="전력 소요·전력화 일정 조정",
            document_form=DocumentForm.REPORT,
            document_name="전력화 추진 현황보고",
            contents=(
                "사업별 전력화 진도율과 당초 목표 대비 달성 실적",
                "지연 사업의 원인 구분과 후속 소요 조정이 필요한 사업",
                "완료된 사업과 착수 전인 사업의 규모",
                "진도율 집계에서 뺀 사업과 뺀 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="군사시설 방호력 보강",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="군사시설 방호력 보강 계획(안)",
            contents=(
                "보강 대상 시설과 목표 수준, 우선순위를 그렇게 정한 사유",
                "과제별 담당 부대와 착수·완료 시기, 연차별 소요 예산",
                "이번 계획에 든 시설 수와 다음 차수로 미룬 시설의 규모",
                "목표 수준을 그 선으로 정한 근거와 접은 대안",
                "확정 전까지 시설 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="군사시설 방호력 보강",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="방호시설 보강 시행 품의",
            contents=(
                "이번 차수 보강 범위와 그 범위로 한정한 품의 사유",
                "소요 금액과 예산과목, 공사 기간",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 시설 수와 이번 차수에서 뺀 시설의 규모",
                "결재가 끝나기 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="군사시설 방호력 보강",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="군사시설 방호 실태 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "시설별 지적사항과 시정 요구 사항, 이행 기한",
                "이번 점검이 다루지 않은 시설과 범위를 좁힌 이유",
                "지적사항이 이번에 처음 나온 것인지 반복된 것인지 구분",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="비상대비 물자·장비 지정 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="비상대비 물자·장비 지정 조정 계획(안)",
            contents=(
                "품목별 현행 지정 규모와 목표 규모, 조정이 필요해진 사유",
                "신규 지정과 지정 해제 품목의 구분과 그 판단 기준",
                "이번 조정에 걸리는 품목 수와 조정되는 물량의 규모",
                "그 기준으로 품목을 가른 근거와 함께 검토한 다른 기준",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="비상대비 물자·장비 지정 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="비상대비 물자 조정 실무협의회 회의록",
            contents=(
                "조정안별 참석 부대 의견과 지정 규모를 두고 갈린 찬반 근거",
                "합의된 조정 범위와 미합의로 남은 쟁점",
                "협의에 올라온 품목 수와 각 안을 낸 부대",
                "규모 판단이 갈린 지점이 어느 소요 산정 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="비상대비 물자·장비 지정 조정",
            document_form=DocumentForm.REPORT,
            document_name="비상대비 물자 지정 조정 추진 현황보고",
            contents=(
                "품목별 조정 목표 대비 지정 실적과 충족률",
                "차질이 난 항목의 원인과 다음 지정 주기 보완 사항",
                "지정을 마친 품목과 미착수 품목의 규모",
                "충족률 집계에서 뺀 품목과 뺀 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 외교부 역할구성. 층위는 앞의 둘과 같고 값 이름만 이 기관의 것이다.
_MOFA_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 과 단독 검토",
        "본부-재외공관 합동 검토",
        "관계부처 합동 검토",
        "외부 전문가가 참여하는 자문회의",
    ),
    guidance=MappingProxyType(
        {
            "담당 과 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 과장 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 부서나 공관 의견을 끌어들이지 않는다."
            ),
            "본부-재외공관 합동 검토": (
                "본부가 요구한 사항을 먼저 적고, 공관이 올린 현지 의견을 적은 "
                "다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 끝낸다. 공관 "
                "의견은 본부 판단과 다른 내용을 담아야 한다."
            ),
            "관계부처 합동 검토": (
                "협의를 요청한 사유를 먼저 적고, 부처별 입장 표명을 적은 다음, "
                "입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 것으로 "
                "끝낸다. 부처마다 서로 다른 소관 사정이 드러나야 한다."
            ),
            "외부 전문가가 참여하는 자문회의": (
                "안건 상정과 담당 부서의 설명을 먼저 적고, 전문가별 질의와 그에 "
                "대한 답변을 적은 다음, 쟁점별 견해와 근거를 적고, 채택·보류된 "
                "자문 의견과 그 사유로 끝낸다. 뒤에 오는 발언은 앞에서 나온 말을 "
                "받아야 하며, 서로 무관한 한 줄 의견을 나열하지 않는다."
            ),
        }
    ),
)


#: 외교부 / 제2호 ``unification_diplomacy``. 세 번째 C트랙 템플릿이다.
#:
#: **같은 제2호의 나머지 반쪽이라 경계가 곧 이 템플릿의 요건이다.**
#: ``_SECURITY_DEFENSE_MND``가 촉발계기에서 연합을 떼어 내며 "상대국과의 협의
#: 경위는 이쪽 소관"이라고 미리 그어 둔 선이 여기서 실물이 된다. 반대 방향도
#: 같다 — 부대·전력·배치가 값으로 들어오는 순간 이 템플릿은 ``security_defense``
#: 문서를 만든 것이 되므로, 사안 여섯 중 어느 것도 군사 대비태세를 다루지 않는다.
#:
#: **장소축을 다자 대표부로 둔 이유.** ``_BOUNDARY_RULE``이 명시한 두 위험 중
#: 하나가 "실존 양자 관계에 대한 위조 외교 기록의 형식(제2호
#: ``unification_diplomacy``)"이다. 장소축에 주요국 양자공관을 세우면 그 위험이
#: 축 자체에 박힌다 — ``- 대상 장소: 주○○대사관``에 "양보 가능 범위"를 요구하는
#: 것은 실재하는 양자 교섭의 우리 측 방침을 지어내라는 말이 된다. 다자 대표부는
#: 상대가 특정 국가가 아니라 회의체라 그 자리가 비어 있다. 국방부 템플릿이
#: 최상위 본부를 예하 부대로 내린 것과 같은 종류의 조정이다.
#:
#: **부서를 기능국으로만 채운 것도 그 결과다.** 지역국 과(동북아1과·유럽1과)를
#: 넣으면 ``작성 부서``와 ``대상 장소``의 관할이 어긋난다 — 유럽1과가 주아세안
#: 대표부 건을 기안하는 문서가 조합의 상당수로 나온다.
#:
#: 조항이 새는 자리 넷을 사안·항목에서 막았다. 분담금·기여금 배분액(제5호
#: ``bid_contract``) / 공관 물리보안 취약점의 열거(제7호 ``security_diagnosis``) /
#: 재외국민 명단·연락처(제6호 ``petitioner_pii``) / 이미 발표된 공동성명·협정문의
#: 내용(이 세부조항의 ``excludes`` 정면).
_UNIFICATION_DIPLOMACY_MOFA = CTrackTemplate(
    subclause_key=SubclauseKey.UNIFICATION_DIPLOMACY,
    agency="외교부",
    persona_context="다자 교섭·조약 사무와 재외공관 운영 업무",
    adversaries=(
        Adversary(
            who="같은 안건을 마주 놓고 앉는 상대측 대표단",
            position="external",
            what_they_gain=(
                "우리가 어느 항목을 어디까지 접을 수 있는지가 교섭 전에 읽히면, "
                "서로 상대의 한계선을 모른 채 마주 앉는다는 전제 위에 서 있던 "
                "교섭이 무력화된다."
            ),
        ),
        Adversary(
            who="같은 자리를 놓고 다투는 제3국 교섭 관계자",
            position="external",
            what_they_gain=(
                "우리가 어느 나라에 무엇을 걸고 지지를 구하는지가 먼저 알려지면, "
                "확보한 지지가 확정 전까지 드러나지 않는다는 전제가 성립하지 "
                "않게 된다."
            ),
        ),
        Adversary(
            who="협의에 배석한 민간 자문·연구 관계자",
            position="intermediary",
            what_they_gain=(
                "확정 전 대응방침이 정부 밖으로 먼저 퍼지면, 정부가 하나의 "
                "입장으로 상대를 마주한다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="우리 공관과 재외국민을 겨냥하는 현지 위해 세력",
            position="external",
            what_they_gain=(
                "우리 보호 조치가 무엇을 상정하고 짜였는지가 밖에서 읽히면, "
                "상정 밖의 일까지 막아 낸다는 전제 위에 서 있던 재외국민 보호가 "
                "무력화된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 전문을 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "확정 전 입장과 접촉 경위가 인가 밖으로 퍼지면, 결정 전까지 우리 "
                "의중이 드러나지 않는다는 전제 위에 서 있던 교섭이 무력화된다."
            ),
        ),
    ),
    departments=(
        "국제기구국 국제기구협력과",
        "국제기구국 인권사회과",
        "조약국 조약과",
        "조약국 국제법규과",
        "영사안전국 재외국민보호과",
        "영사안전국 영사안전정책과",
        "개발협력국 개발협력정책과",
    ),
    # 실호출 1건(2026-08-05, case 141)에서 이 문장이 사안 경계를 넘겼다. 초안은
    # "…접촉 경위, 공관 안전 조치의 소요와 시기처럼"이었는데, `다자회의 대응방침
    # 수립` 문서에 경비 인력 4명·차단 설비 2세트·설치와 철수 시각이 한 절로
    # 붙어 나왔다. instruction은 템플릿의 **모든** 사안에 걸리는 층이라 특정
    # 사안의 소재를 예로 들면 그 소재가 열여덟 건 전부로 번진다 — 사안을
    # `subject_cases`로 나눈 의미가 그만큼 없어진다. 안전 조치는 그것이 사안인
    # 세 건의 `contents`에만 둔다.
    instruction=(
        "다자 교섭·조약 사무와 재외공관 운영의 내부 자료를 쓴다. 확정 전 "
        "대응방침과 우리가 접을 수 있는 범위, 상대측 입장 분석과 접촉 경위처럼 "
        "미리 알려지면 우리 교섭 입지가 딛고 선 전제가 그대로 읽히는 내용을 "
        "담되, 그 사안에서 다루는 것만 쓴다. 공개 시 외교관계 수행이 어떻게 "
        "곤란해져 국가의 중대한 이익이 현저히 훼손되는지 문맥에서 드러나게 한다."
    ),
    slots=(
        CaseSlot(
            name="대상공관",
            place=True,
            values=(
                "주유엔대표부",
                "주제네바대표부",
                "주빈국제기구대표부",
                "주아세안대표부",
                "주경제협력개발기구대표부",
                "주유네스코대표부",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "다자회의 개최 일정 확정 통보",
                "상대측 수정안 접수",
                "국제기구 선거 일정 공고",
                "주재국 정세 급변 보고 접수",
                "조약 발효 요건 점검 결과 접수",
            ),
            guidance=MappingProxyType(
                {
                    "다자회의 개최 일정 확정 통보": (
                        "날짜가 밖에서 정해져 준비 기간이 고정돼 있다. 그 안에 "
                        "무엇을 포기할지가 함께 서야 한다."
                    ),
                    "상대측 수정안 접수": (
                        "상대가 먼저 움직인 건이라 우리 문서는 응답하는 위치에 "
                        "선다. 우리가 먼저 제안한 것처럼 쓰지 않는다."
                    ),
                    "국제기구 선거 일정 공고": (
                        "경쟁 상대가 있고 그쪽도 같은 공고를 봤다. 우리만 아는 "
                        "것과 모두가 아는 것이 구별되어야 한다."
                    ),
                    "주재국 정세 급변 보고 접수": (
                        "상황이 계속 움직이고 있어 지금 적는 판단이 잠정이다. "
                        "확정된 것으로 적지 않는다."
                    ),
                    "조약 발효 요건 점검 결과 접수": (
                        "법적 요건이 기준이라 재량이 좁다. 되고 안 되고가 판단이 "
                        "아니라 요건 충족 여부로 갈린다."
                    ),
                }
            ),
        ),
        _MOFA_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="다자회의 대응방침 수립",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="다자회의 대응방침(안)",
            contents=(
                "의제별 우리 입장과 수용 가능 범위, 그 선을 그은 근거",
                "주요 참가국의 예상 입장과 그에 대한 대응 논리",
                "이번 회의에서 다룰 의제 수와 우리가 발언할 의제의 범위",
                "예상 입장을 그렇게 판단한 근거와 아직 확인되지 않은 부분",
                "확정 전까지 대표단 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="다자회의 대응방침 수립",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="대응방침 검토 회의록",
            contents=(
                "의제별 발언과 입장 수위를 두고 갈린 찬반 근거",
                "확정된 방침과 재검토로 넘긴 의제",
                "검토에 올라온 의제 수와 각 안을 낸 부서",
                "수위 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="다자회의 대응방침 수립",
            document_form=DocumentForm.REPORT,
            document_name="다자회의 참석 결과보고",
            contents=(
                "당초 방침과 실제 논의 결과의 의제별 대비표",
                "방침과 어긋난 항목의 원인과 차기 회의 대응 과제",
                "합의에 이른 의제와 미합의로 남은 의제의 수",
                "상대측 입장 중 이번에 처음 확인된 것과 기존 판단대로였던 것의 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="국제기구 선거 입후보 교섭",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="입후보 교섭 추진계획(안)",
            contents=(
                "목표 확보 표수와 국가군별 지지 판단, 그 판단의 근거",
                "국가군별 교섭 소요와 단계별 접촉 시기",
                "지지·미정·반대로 가른 국가 수와 판단이 서지 않은 국가의 규모",
                "그 목표 표수를 잡은 근거와 함께 검토한 다른 시나리오",
                "확정 전까지 본부 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="국제기구 선거 입후보 교섭",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="입후보 방침 결정 품의",
            contents=(
                "입후보 여부를 이렇게 판단한 사유와 승인받을 방침",
                "교섭에 드는 소요와 추진 기간",
                "기안-검토-결재 열을 가진 결재란",
                "검토한 선택지와 각 선택지가 지는 부담",
                "결재가 끝나기 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="국제기구 선거 입후보 교섭",
            document_form=DocumentForm.REPORT,
            document_name="입후보 교섭 추진 현황보고",
            contents=(
                "국가군별 확보·미확보 집계와 목표 대비 달성률",
                "미확보로 남은 국가군의 원인과 남은 기간의 보완 과제",
                "접촉을 마친 국가 수와 아직 접촉하지 못한 국가의 규모",
                "직전 보고 이후 판단이 바뀐 국가와 그 변화가 무엇에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="조약 교섭 대응",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="조약 교섭 대응방침(안)",
            contents=(
                "조문별 우리 안과 접을 수 있는 순서, 그 순서를 정한 사유",
                "상대측 수정안에 대한 조문별 평가와 대응 논리",
                "쟁점 조문 수와 이미 정리된 조문의 규모",
                "우리 안을 그 수준으로 잡은 근거와 함께 검토한 대안",
                "확정 전까지 대표단 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="조약 교섭 대응",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="조약안 법률 검토의견",
            contents=(
                "조문별 국내법 저촉 여부와 유보가 필요한 항목",
                "발효 요건과 국회 동의 대상 해당 여부에 대한 검토 결론",
                "검토한 조문 수와 판단을 유보한 조문의 규모",
                "저촉 판단의 근거 법령과 아직 확인되지 않은 쟁점",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="조약 교섭 대응",
            document_form=DocumentForm.REPORT,
            document_name="조약 교섭 경과보고",
            contents=(
                "회차별 쟁점 조문의 진전 상황과 당초 방침 대비 결과",
                "미합의로 남은 조문과 다음 회차에 넘긴 사항",
                "이번 회차에서 정리된 조문 수와 남은 조문의 규모",
                "상대측 입장 중 이번에 처음 확인된 것과 기존 판단대로였던 것의 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="재외공관 안전대책 강화",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="공관 안전대책 보강 계획(안)",
            contents=(
                "보강 항목과 목표 수준, 우선순위를 그렇게 정한 사유",
                "항목별 담당과 착수·완료 시기, 소요 인력",
                "이번 계획에 든 항목 수와 다음 차수로 미룬 항목의 규모",
                "목표 수준을 그 선으로 정한 근거와 접은 대안",
                "확정 전까지 공관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="재외공관 안전대책 강화",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="안전대책 보강 시행 품의",
            contents=(
                "이번 차수 보강 범위와 그 범위로 한정한 품의 사유",
                "소요 금액과 예산과목, 시행 기간",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 항목 수와 이번 차수에서 뺀 항목의 규모",
                "결재가 끝나기 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="재외공관 안전대책 강화",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="공관 안전관리 실태 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "항목별 지적사항과 시정 요구 사항, 이행 기한",
                "이번 점검이 다루지 않은 항목과 범위를 좁힌 이유",
                "지적사항이 이번에 처음 나온 것인지 반복된 것인지 구분",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="재외국민 보호 비상대응",
            document_form=DocumentForm.RESPONSE_PLAN,
            document_name="재외국민 비상대응계획서",
            contents=(
                "위기 단계별 조치사항과 각 단계의 발령 기준",
                "단계별 담당 부서와 공관-본부 보고·전파 체계",
                "각 단계에서 대상이 되는 재외국민 규모와 기준 시점",
                "발령 기준선을 그 수준으로 정한 근거",
                "계획 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="재외국민 보호 비상대응",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="재외국민보호 대책회의 회의록",
            contents=(
                "단계 판단을 두고 갈린 근거와 참석 부서별 발언",
                "결정된 조치와 보류된 조치, 그 보류 사유",
                "회의에 올라온 판단 자료와 그 자료를 낸 부서",
                "부서별 판단이 갈린 지점이 어느 정세 인식 차이에서 왔는지",
                "결정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="재외국민 보호 비상대응",
            document_form=DocumentForm.REPORT,
            document_name="비상대응 시행 결과보고",
            contents=(
                "당초 계획과 단계별 실제 조치 실적의 대비표",
                "미흡했던 항목의 원인과 계획에 반영할 보완 사항",
                "조치가 닿은 재외국민 규모와 연락이 닿지 않은 규모",
                "실적이 계획에 못 미친 항목에서 그 차이가 무엇에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="주재국 정세 변동 대응",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="정세 대응 관계부처 협의 회의록",
            contents=(
                "부처별 소관 영향 진단과 대응 수위를 두고 갈린 쟁점",
                "합의된 대응 범위와 미합의로 남은 사항",
                "협의에 올라온 진단 자료와 그 자료를 낸 부처",
                "부처별 진단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="주재국 정세 변동 대응",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="정세 변동 대응방침 조정(안)",
            contents=(
                "변동 요인별 대응 선택지와 각 선택지가 지는 부담",
                "방침을 조정할 시점의 판단 기준과 그 기준을 정한 사유",
                "확인된 변동 요인 수와 아직 확인되지 않은 요인의 범위",
                "그 선택지를 우선에 둔 근거와 함께 검토했다가 접은 안",
                "확정 전까지 본부 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="주재국 정세 변동 대응",
            document_form=DocumentForm.REPORT,
            document_name="정세 변동 및 대응 조정 현황보고",
            contents=(
                "당초 상정한 정세와 실제 전개의 대비, 우리 활동에 미친 영향",
                "조정한 공관 활동 항목과 후속 확인이 필요한 사항",
                "조정을 마친 활동 항목과 아직 조정하지 못한 항목의 규모",
                "상정과 실제가 갈린 지점이 어느 판단에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 통일부 역할구성. 네 번째 값만 이 기관의 것이다 — 외교부의 자문회의 자리에
#: 민간 협력단체가 온다. 대북 협력사업은 단체가 집행 주체로 실제 협의에 들어오고,
#: 그 자리에서 오가는 말이 자문과 다르다.
_MOU_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 과 단독 검토",
        "본부-소속기관 합동 검토",
        "관계부처 합동 검토",
        "민간 협력단체가 참여하는 협의",
    ),
    guidance=MappingProxyType(
        {
            "담당 과 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 과장 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 부서나 소속기관 의견을 끌어들이지 않는다."
            ),
            "본부-소속기관 합동 검토": (
                "본부가 요구한 사항을 먼저 적고, 소속기관이 올린 현장 의견을 "
                "적은 다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 끝낸다. "
                "현장 의견은 본부 판단과 다른 내용을 담아야 한다."
            ),
            "관계부처 합동 검토": (
                "협의를 요청한 사유를 먼저 적고, 부처별 입장 표명을 적은 다음, "
                "입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 것으로 "
                "끝낸다. 부처마다 서로 다른 소관 사정이 드러나야 한다."
            ),
            "민간 협력단체가 참여하는 협의": (
                "정부가 제시한 조건과 그 사유를 먼저 적고, 단체가 제기한 집행상의 "
                "어려움을 적은 다음, 조건과 현장 사정이 어긋난 지점을 짚고, "
                "조정된 것과 정부가 물러서지 않은 것으로 끝낸다. 단체 의견은 "
                "정부 판단과 다른 내용을 담아야 한다."
            ),
        }
    ),
)


#: 통일부 / 제2호 ``unification_diplomacy``. 네 번째 C트랙 템플릿이고, **같은
#: 세부조항의 두 번째 기관**이다.
#:
#: **그래서 요건이 하나 늘었다.** 감사 결정 3(제4호 두 브로커는 파는 물건이
#: 달라야 한다)이 여기 그대로 걸린다 — 적대자가 같으면 [단계 1]의 답이 같아지고,
#: 세부조항까지 같으므로 생성 문서를 사후에 구별할 근거가 기관명 한 줄만 남는다.
#: 그래서 ``_UNIFICATION_DIPLOMACY_MOFA``와 적대자를 겹치지 않게 세웠다.
#:   - 외교부의 상대는 **교섭 상대국·제3국**이고 그들이 읽는 것은 우리가 접을 수
#:     있는 **선**이다.
#:   - 통일부의 상대는 **북측·대북사업 관계자**이고 그들이 읽는 것은 우리가 이번
#:     국면을 어떻게 **판단**하고 무엇을 먼저 풀려 하는지다.
#:   양쪽에 다 있는 내부 열람자도 잃는 것이 다르다 — 저쪽은 교섭 의중이고
#:   이쪽은 공개 시점의 선택권이다.
#:
#: **장소축은 소속기관으로 둔다.** 남북 사이의 장소(공동연락사무소·공단)를 세우면
#: 실존하지 않거나 폐쇄된 자리를 상시 운영 중인 것으로 적게 되고, 그것은
#: ``_BOUNDARY_RULE``이 말하는 "실재하는 시설의 실제 판정" 자리를 지어내는 일이다.
#: 우리 쪽 소속기관은 그 문제가 없고 층위도 앞의 셋과 같다.
#:
#: 조항이 새는 자리 넷. 북한이탈주민의 성명·출신지·정착지(제6호 ``welfare_pii``·
#: ``petitioner_pii``) / 반출 물자의 단가·계약 상대(제5호 ``bid_contract``·제7호
#: ``unit_cost``) / 접경지역의 부대·전력 배치(제2호 ``security_defense``) /
#: 이미 발표된 합의서의 내용(이 세부조항의 ``excludes`` 정면).
_UNIFICATION_DIPLOMACY_MOU = CTrackTemplate(
    subclause_key=SubclauseKey.UNIFICATION_DIPLOMACY,
    agency="통일부",
    persona_context="남북 회담·교류협력과 인도협력 업무",
    adversaries=(
        Adversary(
            who="같은 사안을 두고 마주 앉는 북측 대표단",
            position="external",
            what_they_gain=(
                "우리가 이번 국면을 어떻게 읽고 무엇을 먼저 풀려 하는지가 마주 "
                "앉기 전에 알려지면, 서로 상대의 판단을 모른 채 국면을 다룬다는 "
                "전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="재개를 앞두고 움직이는 대북 협력사업 관계자",
            position="intermediary",
            what_they_gain=(
                "무엇을 어느 조건에서 어느 순서로 열려 하는지가 확정 전에 "
                "퍼지면, 정부가 조건을 걸어 순서를 정한다는 전제가 성립하지 않게 "
                "된다."
            ),
        ),
        Adversary(
            who="북한이탈주민의 정착 경로를 좇는 외부 세력",
            position="external",
            what_they_gain=(
                "보호와 정착 지원이 어느 규모로 어디를 거쳐 이뤄지는지가 밖에서 "
                "읽히면, 보호 대상이 드러나지 않는다는 전제 위에 서 있던 보호가 "
                "무력화된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 협의 기록을 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "남북 사이에 오간 것이 우리 결정보다 먼저 알려지면, 무엇을 언제 "
                "공개할지를 우리가 정한다는 전제가 성립하지 않게 된다."
            ),
        ),
    ),
    # 2025-11-04 시행 직제 개편 후의 현행 조직이다(unikorea.go.kr 조직도, 확인
    # 2026-08-05). 초안에 적었던 `교류협력국`·`인도협력국`·`이산가족과`는 그
    # 개편으로 없어진 이름이라 전부 갈았다 — 실존명 원칙(2026-08-05 사용자 결정)은
    # "실재하는 이름"이지 "실재했던 이름"이 아니다. 부처 조직은 개편으로 움직이므로
    # 이 축은 템플릿을 새로 쓸 때마다 대조해야 하는 자리다.
    departments=(
        "통일정책실 정책총괄과",
        "평화교류실 평화교류총괄과",
        "평화교류실 평화경제·제재관리과",
        "평화교류실 남북경제협력과",
        "평화교류실 인도지원과",
        "평화교류실 접경협력과",
        "사회문화협력국 자립지원과",
    ),
    instruction=(
        "남북 회담·교류협력·인도협력과 그 소속기관 운영의 내부 자료를 쓴다. "
        "확정 전 대응방침과 우리가 접을 수 있는 범위, 북측 통지와 접촉 경위에 "
        "대한 우리 판단처럼 미리 알려지면 우리 협의 입지가 딛고 선 전제가 그대로 "
        "읽히는 내용을 담되, 그 사안에서 다루는 것만 쓴다. 공개 시 통일정책 "
        "수행이 어떻게 곤란해져 국가의 중대한 이익이 현저히 훼손되는지 문맥에서 "
        "드러나게 한다."
    ),
    slots=(
        CaseSlot(
            name="대상기관",
            place=True,
            # 현행 소속기관 넷이 전부다(확인 2026-08-05). 앞의 셋이 여섯 값을 쓴
            # 것과 달리 넷인 이유는 여기서 늘릴 자리가 없어서다 — 초안의
            # `경의선/동해선 남북출입사무소`는 2023-09 남북관계관리단으로
            # 통폐합되며 폐지됐고, 그 관리단마저 2025-11 개편으로 없어졌다.
            # `국립통일교육원`은 `국립평화통일민주교육원`으로 재편됐다.
            values=(
                "남북회담본부",
                "국립평화통일민주교육원",
                "북한이탈주민정착지원사무소",
                "북한인권기록센터",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "북측 통지문 접수",
                "연락채널 운영 상태 변동 보고 접수",
                "인도적 지원 반출 승인 신청 접수",
                "접경지역 상황 변동 보고 접수",
                "국제사회 제재 면제 심사 일정 통보",
            ),
            guidance=MappingProxyType(
                {
                    "북측 통지문 접수": (
                        "상대가 보낸 문서 하나가 아는 것의 전부다. 그 밖의 "
                        "의도는 추정으로만 남는다."
                    ),
                    "연락채널 운영 상태 변동 보고 접수": (
                        "상태가 바뀐 이유를 우리가 모른다. 변동 사실과 그에 대한 "
                        "해석이 섞이지 않아야 한다."
                    ),
                    "인도적 지원 반출 승인 신청 접수": (
                        "신청인이 밖에 있어 기관은 심사하는 위치에 선다. 신청 "
                        "내용을 넘어선 사항을 스스로 끌어오지 않는다."
                    ),
                    "접경지역 상황 변동 보고 접수": (
                        "현장에서 올라온 건이라 본부가 직접 본 것이 아니다. "
                        "보고받은 것과 확인한 것이 구별되어야 한다."
                    ),
                    "국제사회 제재 면제 심사 일정 통보": (
                        "판단 권한이 밖에 있다. 우리가 정할 수 있는 것은 무엇을 "
                        "어떻게 설명할지까지다."
                    ),
                }
            ),
        ),
        _MOU_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="남북 당국회담 대응방침 수립",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="남북 당국회담 대응방침(안)",
            contents=(
                "의제별 우리 입장과 수용 가능 범위, 그 선을 그은 근거",
                "북측 예상 주장과 그에 대한 대응 논리",
                "이번 회담에서 다룰 의제 수와 우리가 먼저 꺼낼 의제의 범위",
                "예상 주장을 그렇게 판단한 근거와 아직 확인되지 않은 부분",
                "확정 전까지 대표단 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="남북 당국회담 대응방침 수립",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="대응방침 검토 회의록",
            contents=(
                "의제별 발언과 입장 수위를 두고 갈린 찬반 근거",
                "확정된 방침과 재검토로 넘긴 의제",
                "검토에 올라온 의제 수와 각 안을 낸 부서",
                "수위 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="남북 당국회담 대응방침 수립",
            document_form=DocumentForm.REPORT,
            document_name="회담 진행 경과보고",
            contents=(
                "당초 방침과 실제 논의 결과의 의제별 대비표",
                "방침과 어긋난 항목의 원인과 차기 회담 대응 과제",
                "합의에 이른 의제와 미합의로 남은 의제의 수",
                "북측 입장 중 이번에 처음 확인된 것과 기존 판단대로였던 것의 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="인도적 지원 물자 반출 승인",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="지원 물자 반출 승인 품의",
            contents=(
                "승인받을 반출 품목과 수량, 그 범위로 한정한 품의 사유",
                "반출 경로와 시기, 전달 확인 방법",
                "기안-검토-결재 열을 가진 결재란",
                "신청된 품목 수와 이번 승인에서 뺀 품목의 규모, 뺀 기준",
                "결재가 끝나기 전까지 신청인과 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="인도적 지원 물자 반출 승인",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="반출 승인 법률 검토의견",
            contents=(
                "남북교류협력 관련 법령상 승인 요건 충족 여부와 미비 항목",
                "국제사회 제재와의 저촉 여부 및 면제 필요 항목에 대한 검토 결론",
                "검토한 품목 수와 판단을 유보한 품목의 규모",
                "저촉 판단의 근거 규정과 아직 확인되지 않은 쟁점",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="인도적 지원 물자 반출 승인",
            document_form=DocumentForm.REPORT,
            document_name="반출 이행 결과보고",
            contents=(
                "당초 승인 내용과 실제 반출 실적의 품목별 대비표",
                "전달 확인 결과와 확인되지 않은 항목의 원인",
                "반출을 마친 물량과 반출하지 못한 물량의 규모",
                "승인 내용과 실적이 갈린 지점이 무엇에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="남북 연락·왕래 채널 운영 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="연락·왕래 채널 운영 조정 계획(안)",
            contents=(
                "현행 운영 방식과 조정안, 조정이 필요해진 사유",
                "조정에 따른 인력·시설 소요와 단계별 시행 시기",
                "현재 운영 중인 채널 수와 조정 대상이 되는 규모",
                "그 조정안을 택한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 본부 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="남북 연락·왕래 채널 운영 조정",
            document_form=DocumentForm.RESPONSE_PLAN,
            document_name="채널 중단 시 대응계획서",
            contents=(
                "중단 단계별 조치사항과 각 단계의 판단 기준",
                "단계별 담당 부서와 보고·전파 체계",
                "각 단계에서 영향을 받는 협의 사안의 규모",
                "판단 기준선을 그 수준으로 정한 근거",
                "계획 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="남북 연락·왕래 채널 운영 조정",
            document_form=DocumentForm.REPORT,
            document_name="채널 운영 현황보고",
            contents=(
                "기준 시점의 운영 실적과 당초 계획 대비 차이",
                "차질이 난 항목의 원인과 조정이 필요한 부분",
                "정상 운영된 기간과 운영이 멈춘 기간의 규모",
                "차질 원인이 우리 손 안의 사유인지 밖의 사유인지 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="북한이탈주민 정착지원 운영 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="정착지원 운영 조정 계획(안)",
            contents=(
                "현행 수용 규모와 조정 목표, 조정이 필요해진 사유",
                "시설·인력 소요와 단계별 시행 시기, 보호 조치 보강 항목",
                "현재 수용 인원과 조정 후 감당할 수 있는 인원의 차이",
                "그 목표 규모를 잡은 근거와 함께 검토한 다른 안",
                "확정 전까지 시설 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="북한이탈주민 정착지원 운영 조정",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="정착지원시설 운영 실태 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "항목별 지적사항과 시정 요구 사항, 이행 기한",
                "이번 점검이 다루지 않은 항목과 범위를 좁힌 이유",
                "지적사항이 이번에 처음 나온 것인지 반복된 것인지 구분",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="북한이탈주민 정착지원 운영 조정",
            document_form=DocumentForm.REPORT,
            document_name="정착지원 운영 현황보고",
            contents=(
                "기준 시점의 수용 규모와 목표 대비 운영 실적",
                "보호 조치에서 미흡했던 항목과 그 원인",
                "직전 기준 시점과 비교한 증감과 그 증감이 생긴 사유",
                "집계에서 뺀 대상 구분과 뺀 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="접경지역 상황 대응",
            document_form=DocumentForm.RESPONSE_PLAN,
            document_name="접경지역 상황 대응계획서",
            contents=(
                "상황 단계별 조치사항과 각 단계의 발령 기준",
                "단계별 담당 부서와 관계기관 통보 체계",
                "각 단계에서 대상이 되는 구역과 인원의 규모",
                "발령 기준선을 그 수준으로 정한 근거",
                "계획 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="접경지역 상황 대응",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="접경지역 상황 대책회의 회의록",
            contents=(
                "단계 판단을 두고 갈린 근거와 참석 부서별 발언",
                "결정된 조치와 보류된 조치, 그 보류 사유",
                "회의에 올라온 상황 보고와 그 보고를 낸 기관",
                "부서별 판단이 갈린 지점이 어느 상황 인식 차이에서 왔는지",
                "결정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="접경지역 상황 대응",
            document_form=DocumentForm.REPORT,
            document_name="상황 대응 조치 결과보고",
            contents=(
                "당초 계획과 단계별 실제 조치 실적의 대비표",
                "미흡했던 항목의 원인과 계획에 반영할 보완 사항",
                "조치를 마친 항목과 착수하지 못한 항목의 규모",
                "확인된 사실과 아직 확인되지 않은 항목의 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="교류협력 사업 재개 대비 검토",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="사업 재개 대비 검토(안)",
            contents=(
                "재개 후보 사업의 구분과 여는 순서, 그 순서를 정한 사유",
                "사업별 재개 조건과 조건 충족 여부를 판단하는 기준",
                "검토에 오른 사업 수와 이번에 후보에서 뺀 사업의 규모",
                "그 순서를 정한 근거와 함께 검토했다가 접은 배열",
                "확정 전까지 참여 주체와 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="교류협력 사업 재개 대비 검토",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="사업 재개 실무협의 회의록",
            contents=(
                "재개 조건을 두고 정부와 참여 주체가 갈린 쟁점과 그 근거",
                "합의된 조건과 미합의로 남은 사항",
                "협의에 올라온 사업 수와 각 안을 낸 주체",
                "조건 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="교류협력 사업 재개 대비 검토",
            document_form=DocumentForm.REPORT,
            document_name="재개 준비 추진 현황보고",
            contents=(
                "사업별 준비 진도와 당초 목표 대비 달성 실적",
                "지연된 사업의 원인 구분과 재개 순서 조정이 필요한 사업",
                "준비를 마친 사업과 착수 전인 사업의 규모",
                "진도 집계에서 뺀 사업과 뺀 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 행정안전부 역할구성. 네 번째 값이 이 기관의 것이다 — 감사 결정 2가 수탁 업체
#: 적대자를 ``life_body`` 쪽에 배정했으므로, 그 상대가 실제로 앉는 자리를 축에도
#: 둔다. 적대자와 역할구성이 같은 사람을 가리켜야 [단계 1]의 답이 본문에서 값을
#: 갖는다.
_MOIS_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 과 단독 검토",
        "중앙-지자체 합동 검토",
        "소방·경찰·질병관리 등 관계기관 협의",
        "시설 관리 수탁 업체가 참여하는 협의",
    ),
    guidance=MappingProxyType(
        {
            "담당 과 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 과장 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 부서나 지자체 의견을 끌어들이지 않는다."
            ),
            "중앙-지자체 합동 검토": (
                "중앙이 요구한 사항을 먼저 적고, 지자체가 올린 현장 의견을 적은 "
                "다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 끝낸다. 현장 "
                "의견은 중앙 판단과 다른 내용을 담아야 한다."
            ),
            "소방·경찰·질병관리 등 관계기관 협의": (
                "협의를 요청한 사유를 먼저 적고, 기관별 입장 표명을 적은 다음, "
                "입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 것으로 "
                "끝낸다. 기관마다 서로 다른 소관 사정이 드러나야 한다."
            ),
            "시설 관리 수탁 업체가 참여하는 협의": (
                "행정청이 제시한 요구 수준과 그 사유를 먼저 적고, 업체가 제기한 "
                "이행상의 어려움을 적은 다음, 요구와 현장 사정이 어긋난 지점을 "
                "짚고, 조정된 것과 행정청이 물러서지 않은 것으로 끝낸다. 업체 "
                "의견은 행정청 판단과 다른 내용을 담아야 한다."
            ),
        }
    ),
)


#: 행정안전부 / 제3호 ``life_body``. 다섯 번째 C트랙 템플릿이다.
#:
#: **``property``와 정면으로 붙어 있어 경계가 이 템플릿의 요건이다.** 감사 결정
#: 2가 그 선을 이미 그어 뒀다 — 이쪽은 **사람이 그 안에 있을 때 생기는 피해**만
#: 다루고(대피·구조·감염병·고위험물질 취급 통제), 구조물 등급 판정과 진단 결과·
#: 사용제한은 국토교통부 ``property`` 몫이다. 그래서 촉발계기에 `정기 안전점검·
#: 정밀안전진단 결과`를 두지 않았다. 그 한 값이 두 템플릿의 사건 축을 겹치게
#: 만들고, 겹치면 생성 문서를 사후에 구별할 수 없다.
#:
#: 수탁 업체 적대자도 결정 2가 이쪽에 배정한 것이다 — 취약 지점을 쥐는 쪽이
#: ``life_body``, 판정 결과를 흘리는 쪽이 ``property``다.
#:
#: **사안은 업무가 아니라 취약점으로 세운다.** 초안의 여섯은 전부 업무 이름
#: (다중밀집 안전관리·대피계획 정비·감염병 확산 대응·대피소 운영)이었는데,
#: 그 이름이 부르는 문서는 원래 공표되는 정책이다. 실측(2026-08-05, case 141)에서
#: 그대로 나왔다 — 생성된 `다중밀집 상황 안전관리 계획(안)`의 본체가 우회 유도
#: 경로·통제 시각·배치 인력이었고, 셋 다 시민에게 미리 알려야 실효가 있는 값이라
#: 공개 쪽이다. 비공개가 선 것은 `감당 한계 12,000명`·`밀도 3.5인/㎡` 같은
#: 곁가지뿐이었다.
#:
#: 이 세부조항이 무엇을 비공개로 세우는지는 taxonomy가 이미 셋으로 못 박아 뒀다
#: — 방호 **취약점**, 보호 대상자의 **소재**, 모방 위험이 큰 **수법**. 셋 다
#: "무엇을 하는가"가 아니라 "무엇이 취약한가"다. 그래서 사안을 그 축으로 다시
#: 세웠다(사용자 결정, 2026-08-05): 진단·부족분·사각지대·미달 구역이 사안이 되고,
#: 업무 수행은 그 부족분을 재는 배경으로만 남는다. 문서형식도 점검·현황 계열로
#: 옮겨 갔다.
#:
#: **부서는 2026-08-05에 대조한 현행 조직이다**(mois.go.kr 조직도). 통일부에서
#: 개편으로 없어진 이름을 적었던 일이 있어 이번에는 축을 세우기 전에 맞췄다.
#: 점검·조사 계열 과(재난안전점검과·재난안전조사과)는 넣지 않았다 — 이름이
#: 구조물 진단 쪽으로 문서를 끌고 가 ``property``와 만난다.
#:
#: 장소축은 광역지자체다. 이 기관의 재난 문서는 시·도를 대상으로 쓰이므로 부서와
#: 관할이 맞는다 — 외교부에서 지역국 과를 뺀 것과 같은 이유의 반대편이다.
#:
#: 조항이 새는 자리 넷. 구조물 안전등급·사용제한(제3호 ``property``) / 확진자·
#: 대피자 명단(제6호 ``welfare_pii``) / 시설 보강 공사의 계약 방식(제5호
#: ``bid_contract``) / 이미 공지된 안전수칙과 통계(이 세부조항의 ``excludes``).
_LIFE_BODY_MOIS = CTrackTemplate(
    subclause_key=SubclauseKey.LIFE_BODY,
    agency="행정안전부",
    persona_context="재난 대응과 국민 안전 관리 업무",
    adversaries=(
        Adversary(
            who="안전관리를 위탁받아 현장 사정을 쥐고 있는 수탁 업체 관계자",
            position="intermediary",
            what_they_gain=(
                "어느 자리가 사람이 몰렸을 때 얼마나 못 버티는지가 관리 밖으로 "
                "나가면, 그것을 아는 사람이 관리 책임 안에 머문다는 전제가 "
                "성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="사람이 몰리는 자리를 노려 해를 끼치려는 자",
            position="external",
            what_they_gain=(
                "사람이 언제 어디에 얼마나 모이고 대피가 어느 규모를 상정했는지가 "
                "밖에서 읽히면, 상정 밖의 일까지 막아 낸다는 전제 위에 서 있던 "
                "보호가 무력화된다."
            ),
        ),
        Adversary(
            who="고위험물질을 손에 넣으려는 외부인",
            position="external",
            what_they_gain=(
                "무엇이 어디에 얼마나 있는지가 밖에서 읽히면, 그 물질에 닿을 수 "
                "있는 사람이 통제 안에 있다는 전제가 무력화된다."
            ),
        ),
        Adversary(
            who="발령 전 판단을 미리 알고 움직인 내부 관계자",
            position="internal",
            what_they_gain=(
                "대응 수위가 정해지기 전에 그 판단이 인가 밖으로 퍼지면, 경보와 "
                "함께 사람들이 움직인다는 전제가 성립하지 않게 된다."
            ),
        ),
    ),
    departments=(
        "사회재난실 사회재난정책과",
        "사회재난실 보건사회재난대응과",
        "사회재난실 국토산업재난대응과",
        "자연재난실 재난관리정책과",
        "자연재난실 재난대응훈련과",
        "재난복구지원국 재난구호과",
        "자연재난실 재난영향분석과",
    ),
    instruction=(
        "재난·안전 관리 가운데 사람이 그 자리에 있을 때 무엇이 모자라고 어디가 "
        "취약한지를 확인한 내부 자료를 쓴다. 상정한 규모와 실제로 감당되는 규모의 "
        "차이, 보호가 닿지 않는 구역, 통제가 못 미치는 항목처럼 미리 알려지면 "
        "그 자리에 있는 사람이 그대로 노출되는 내용을 담되, 그 사안에서 다루는 "
        "것만 쓴다. 시민에게 미리 알려야 실효가 있는 안내 사항(대피 요령·통제 "
        "구간 고지·행사 안내)은 문서의 본체가 아니다. 공개 시 국민의 생명·신체 "
        "보호에 어떤 지장이 생기는지 문맥에서 드러나게 한다."
    ),
    slots=(
        CaseSlot(
            name="대상지역",
            place=True,
            values=(
                "서울특별시",
                "부산광역시",
                "인천광역시",
                "강원특별자치도",
                "전라남도",
                "경상북도",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            # `정기 안전점검·정밀안전진단 결과`는 여기 없다 — 감사 결정 2가
            # `property` 전용으로 못 박은 값이다.
            values=(
                "호우·태풍 특보 발효",
                "감염병 위기경보 단계 상향",
                "유해화학물질 누출 신고 접수",
                "대규모 인파 밀집 행사 개최 계획 접수",
                "대피 훈련 결과 미흡 사항 접수",
            ),
            guidance=MappingProxyType(
                {
                    "호우·태풍 특보 발효": (
                        "상황이 아직 진행 중이라 결과를 알 수 없다. 지금까지 "
                        "확인된 것과 앞으로 벌어질 수 있는 것이 갈려야 한다."
                    ),
                    "감염병 위기경보 단계 상향": (
                        "단계가 올라간 사실이 기준선을 바꿨다. 이전 단계에서 "
                        "하던 것이 왜 부족한지에서 판단이 선다."
                    ),
                    "유해화학물질 누출 신고 접수": (
                        "신고 하나로 시작된 건이라 규모가 아직 확정되지 않았다. "
                        "확인된 범위를 넘어선 수치를 적지 않는다."
                    ),
                    "대규모 인파 밀집 행사 개최 계획 접수": (
                        "행사는 아직 열리지 않았고 주최가 따로 있다. 우리가 "
                        "통제할 수 있는 부분과 요청할 수밖에 없는 부분이 갈린다."
                    ),
                    "대피 훈련 결과 미흡 사항 접수": (
                        "훈련에서 드러난 것이라 실제 상황이 아니다. 훈련 조건에서만 "
                        "성립하는 결과인지가 함께 서야 한다."
                    ),
                }
            ),
        ),
        _MOIS_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="밀집 취약구간 진단",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="밀집 취약구간 진단 결과",
            contents=(
                "진단 일시·대상 구간·진단자",
                "구간별 감당 한계와 그에 못 미치는 항목, 시정 요구 사항과 기한",
                "이번 진단이 다루지 않은 구간과 범위를 좁힌 이유",
                "감당 한계를 그 수치로 산정한 근거",
                "공개되면 그 구간에 있는 사람이 그대로 노출되는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="밀집 취약구간 진단",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="취약구간 보완 계획(안)",
            contents=(
                "구간별 부족한 인력·장비와 보완 목표, 우선순위를 그렇게 정한 사유",
                "보완이 끝나기까지 남는 위험과 그 기간에 두는 임시 조치",
                "이번 계획에 든 구간 수와 다음 차수로 미룬 구간의 규모",
                "목표 수준을 그 선으로 정한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그것이 알려지면 누가 노출되는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="밀집 취약구간 진단",
            document_form=DocumentForm.REPORT,
            document_name="취약구간 보완 추진 현황보고",
            contents=(
                "구간별 보완 목표와 실제 확보 실적의 대비표",
                "여전히 감당 한계에 못 미치는 구간과 그 원인",
                "보완을 마친 구간과 착수 전인 구간의 규모",
                "실적이 목표에 못 미친 구간에서 그 차이가 무엇에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="대피 사각지대 확인",
            document_form=DocumentForm.REPORT,
            document_name="대피 소요시간 실측 결과보고",
            contents=(
                "구역별 상정 대피 시간과 실측 시간의 대비표",
                "상정한 시간 안에 빠져나오지 못하는 구역과 그 원인",
                "실측한 구역 수와 실측하지 못한 구역의 규모",
                "실측 조건과 실제 상황이 달라질 수 있는 지점",
                "공개되면 그 구역에 있는 사람이 그대로 노출되는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="대피 사각지대 확인",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="대피 사각지대 해소 계획(안)",
            contents=(
                "사각지대로 판단한 구역과 그렇게 판단한 기준",
                "해소에 드는 소요와 시기, 해소 전까지 그 구역에 두는 임시 보호 조치",
                "사각지대로 판단된 구역 수와 그 구역에 있는 인원의 규모",
                "그 기준으로 사각지대를 가른 근거와 함께 검토한 다른 기준",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그것이 알려지면 누가 노출되는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="대피 사각지대 확인",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="사각지대 판정 검토 회의록",
            contents=(
                "판정 기준을 두고 갈린 근거와 참석 기관별 발언",
                "확정된 사각지대와 재검토로 넘긴 구역",
                "회의에 올라온 판정 자료와 그 자료를 낸 기관",
                "기관별 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="감염병 대응 자원 부족분 점검",
            document_form=DocumentForm.REPORT,
            document_name="대응 자원 부족분 점검 결과보고",
            contents=(
                "지역별 필요 규모와 확보 규모의 대비표(병상·이송 수단·인력)",
                "부족이 큰 지역과 그 원인, 그 지역에서 감당하지 못하는 상황 규모",
                "이번 점검이 다루지 않은 지역과 범위를 좁힌 이유",
                "필요 규모를 그 수치로 산정한 근거",
                "공개되면 그 지역에 있는 사람이 그대로 노출되는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="감염병 대응 자원 부족분 점검",
            document_form=DocumentForm.RESPONSE_PLAN,
            document_name="자원 부족 상황 대응계획서",
            contents=(
                "부족 정도별 조치사항과 각 단계의 판단 기준",
                "단계별 담당 기관과 전원·이송 연계 체계",
                "각 단계에서 감당할 수 있는 규모와 넘어서는 규모",
                "판단 기준선을 그 수준으로 정한 근거",
                "계획 중 대외에 나가지 않아야 하는 항목과 그것이 알려지면 누가 노출되는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="감염병 대응 자원 부족분 점검",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="자원 배분 조정 관계기관 회의록",
            contents=(
                "배분 우선순위를 두고 갈린 근거와 기관별 발언",
                "합의된 배분안과 미합의로 남은 지역",
                "회의에 올라온 배분안 수와 각 안을 낸 기관",
                "기관별 판단이 갈린 지점이 어느 부족 산정 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="고위험물질 취급시설 방호 취약점 확인",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="취급 통제 보완 계획(안)",
            contents=(
                "취급 물질의 수량과 용도, 통제가 못 미치는 항목과 그렇게 판단한 사유",
                "출입·취급 인가 범위와 확인 주기, 보완 전까지 두는 주민 보호 조치",
                "대상 시설 수와 이번 보완에 들지 못한 시설의 규모",
                "확인 주기를 그 간격으로 정한 근거",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그것이 알려지면 누가 노출되는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="고위험물질 취급시설 방호 취약점 확인",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="취급 통제 이행 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "통제 항목별 지적사항과 시정 요구 사항, 이행 기한",
                "점검 표본의 규모와 전체 대비 비율, 표본을 그렇게 잡은 이유",
                "지적사항이 이번에 처음 나온 것인지 반복된 것인지 구분",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="고위험물질 취급시설 방호 취약점 확인",
            document_form=DocumentForm.REPORT,
            document_name="취급 통제 현황보고",
            contents=(
                "기준 시점의 시설 수와 통제 항목별 이행률",
                "이행이 늦은 항목의 원인과 그 시설에서 감당하지 못하는 사고 규모",
                "직전 기준 시점과 비교한 이행률 변화와 그 변화가 생긴 사유",
                "집계에서 뺀 시설 구분과 뺀 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="대피소 수용 부족 구역 점검",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="대피소 운영 실태 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "대피소별 실제 수용 가능 인원과 지정 규모에 못 미치는 항목, 시정 요구 사항과 기한",
                "이번 점검이 다루지 않은 대피소와 범위를 좁힌 이유",
                "실제 수용 가능 인원을 그 수치로 본 근거",
                "공개되면 그 구역에 있는 사람이 그대로 노출되는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="대피소 수용 부족 구역 점검",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="수용 부족 구역 보완 계획(안)",
            contents=(
                "부족 구역과 부족 규모, 추가 확보 대상과 그 순서를 정한 사유",
                "확보가 끝나기까지 그 구역에 적용할 분산 수용 방안",
                "부족 구역 수와 그 구역에 사는 인원의 규모",
                "그 순서를 정한 근거와 함께 검토했다가 접은 배열",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그것이 알려지면 누가 노출되는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="대피소 수용 부족 구역 점검",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="대피소 추가 지정 승인 품의",
            contents=(
                "추가 지정이 필요한 사유와 지정 규모, 그 규모로 한정한 근거",
                "소요 예산과 확보 기간",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 후보 수와 이번 지정에서 뺀 후보의 규모",
                "결재가 끝나기 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        # 초안의 `비상대비 민방위 대피 훈련`을 물리고 앉힌 사안이다(사용자 지적,
        # 2026-08-05). 민방위 훈련은 일시와 방식이 사전에 공지되고 주민 참여를
        # 전제로 시행되므로 계획과 결과가 이 세부조항의 ``excludes``("이미 공지된
        # 일반 안전수칙과 통계")에 정면으로 걸린다. 게다가 비상대비는
        # ``clause_data`` 제2호 시나리오("을지연습·충무계획 등 국가 비상사태
        # 대비계획", 기관 풀 국방부·국가정보원)와 만나 제3호 라벨을 단 제2호
        # 문서를 만든다. 부서축의 `비상대비정책국 비상대비훈련과`도 같이 물렸다.
        #
        # 이쪽은 반대다. 알려지면 그 사람들이 그대로 노출되는 자리라 제3호
        # 한가운데이고, 명단이 아니라 **규모와 파악 수준**을 값으로 두어 제6호
        # ``welfare_pii``와도 갈린다.
        SubjectCase(
            subject="보호 대상자 소재 파악 미달 구역 확인",
            document_form=DocumentForm.REPORT,
            document_name="보호 대상자 소재 파악 현황보고",
            contents=(
                "구역별 대상 규모와 파악률, 목표에 못 미치는 구역",
                "미달 구역의 원인과 그 구역에서 대피가 닿지 않는 범위",
                "직전 기준 시점과 비교한 파악률 변화와 그 변화가 생긴 사유",
                "집계에서 뺀 대상 구분과 뺀 이유",
                "공개되면 그 대상자가 그대로 노출되는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="보호 대상자 소재 파악 미달 구역 확인",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="소재 파악 보완 계획(안)",
            contents=(
                "미달 구역과 보완 목표, 우선순위를 그렇게 정한 사유",
                "확인 방법과 소요, 파악될 때까지 그 구역에 두는 대체 보호 조치",
                "미달 구역 수와 아직 소재가 확인되지 않은 대상의 규모",
                "목표 파악률을 그 수준으로 잡은 근거와 접은 대안",
                "확정 전까지 대외에 나가지 않아야 하는 항목과 그것이 알려지면 누가 노출되는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="보호 대상자 소재 파악 미달 구역 확인",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="소재 파악 실무협의 회의록",
            contents=(
                "파악 책임 분담과 방법을 두고 갈린 근거와 기관별 발언",
                "합의된 방식과 미합의로 남은 구역",
                "협의에 올라온 구역 수와 각 안을 낸 기관",
                "기관별 판단이 갈린 지점이 어느 대상 산정 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
    ),
)


#: 국토교통부 역할구성. 뒤 두 값이 이 기관의 것이고, 둘 다 **적대자와 같은
#: 사람을 가리킨다** — 판정 결과를 먼저 쥐는 점검·진단기관과 그 판정을 맞는
#: 관리주체다. 행정안전부에서 수탁 업체를 축에 앉힌 것과 같은 배치다.
_MOLIT_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 과 단독 검토",
        "본부-지방국토관리청 합동 검토",
        "점검·진단기관이 참여하는 확인 회의",
        "시설물 관리주체와의 협의",
    ),
    guidance=MappingProxyType(
        {
            "담당 과 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 과장 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 기관 의견을 끌어들이지 않는다."
            ),
            "본부-지방국토관리청 합동 검토": (
                "본부가 요구한 사항을 먼저 적고, 관리청이 올린 현장 의견을 적은 "
                "다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 끝낸다. 현장 "
                "의견은 본부 판단과 다른 내용을 담아야 한다."
            ),
            "점검·진단기관이 참여하는 확인 회의": (
                "행정청이 확인하려는 항목과 그 사유를 먼저 적고, 기관이 제시한 "
                "판정 근거와 산출 과정을 적은 다음, 행정청 확인과 어긋난 지점을 "
                "짚고, 재확인이 필요한 것과 그대로 받아들인 것으로 끝낸다. "
                "기관 설명은 행정청 판단과 다른 내용을 담아야 한다."
            ),
            "시설물 관리주체와의 협의": (
                "행정청이 통보하려는 판정과 그 근거를 먼저 적고, 관리주체가 "
                "제기한 이견과 이행상의 어려움을 적은 다음, 어긋난 지점을 짚고, "
                "조정된 것과 행정청이 물러서지 않은 것으로 끝낸다. 관리주체 "
                "의견은 행정청 판단과 다른 내용을 담아야 한다."
            ),
        }
    ),
)


#: 국토교통부 / 제3호 ``property``. 여섯 번째 C트랙 템플릿이고, ``life_body``의
#: 정면이다.
#:
#: **결정 2가 그은 선을 반대편에서 지킨다.** 이쪽은 구조물 등급 판정·진단 결과·
#: 사용제한 조치만 다루고 사람이 그 안에 있을 때 생기는 피해는 행정안전부 몫이다.
#: 촉발계기 `정기 안전점검·정밀안전진단 결과`가 여기 있는 것도 그 결정이다 —
#: 저쪽에서 뺀 값이 이쪽 전용으로 온다. 적대자도 갈렸다: 취약 지점을 쥐는 쪽이
#: 저쪽, **판정 결과를 흘리는 쪽**이 이쪽이다.
#:
#: **사안은 행정안전부에서 배운 축을 그대로 쓴다** — 업무가 아니라 taxonomy의
#: ``includes``다. 여기서는 그것이 "판정 확정 전 재해위험 구조물 정보"와 "공개 시
#: 재산 피해를 유발할 미공개 행정조치" 둘이라, 사안 여섯이 전부 **확정 전 판정과
#: 통보 전 조치**에 선다. 이미 고시된 안전등급과 공시 정보는 이 세부조항의
#: ``excludes``이므로 확정 뒤는 사안이 되지 않는다.
#:
#: 조항이 새는 자리 넷. 판정이 값·거래에 미칠 영향(제8호 ``real_estate_``
#: ``speculation`` 정면) / 그 안에 있는 사람의 대피·구조(제3호 ``life_body``) /
#: 보수·보강 공사의 계약 방식과 낙찰(제5호 ``bid_contract``) / 점검기관이 낸
#: 산출 방법 자체(제7호 ``technology_patent``).
_PROPERTY_MOLIT = CTrackTemplate(
    subclause_key=SubclauseKey.PROPERTY,
    agency="국토교통부",
    persona_context="시설물 안전 판정과 유지관리 감독 업무",
    adversaries=(
        Adversary(
            who="판정 결과를 먼저 쥐는 점검·진단 수탁기관 관계자",
            position="intermediary",
            what_they_gain=(
                "확정 전 판정이 행정청 밖으로 먼저 나가면, 판정이 행정 절차를 "
                "거쳐 알려진다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="판정 대상 시설의 소유자·관리주체",
            position="external",
            what_they_gain=(
                "확정 전 등급과 예정된 조치를 미리 알면, 조치가 서기 전에 그 "
                "시설에 걸린 부담이 다른 사람에게 넘어가지 않는다는 전제가 "
                "무력화된다."
            ),
        ),
        Adversary(
            who="책임 소재를 다투게 될 시공·유지관리 관계자",
            position="external",
            what_they_gain=(
                "확인이 끝나기 전의 결과가 먼저 알려지면, 책임이 확인을 거쳐 "
                "가려진다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 판정 자료를 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "확정 전 판정과 조치 시점이 인가 밖으로 퍼지면, 조치와 통보가 "
                "정해진 순서로 이뤄진다는 전제가 무력화된다."
            ),
        ),
    ),
    # 2026-08-05에 대조한 현행 부서다(molit.go.kr 부서 안내). 기술안전정책관은
    # 건설정책국 소속이라 국 이름으로 적었다.
    departments=(
        "건설정책국 시설안전과",
        "건설정책국 건설안전과",
        "건설정책국 기술정책과",
        "건설정책국 지하안전팀",
        "도로국 도로시설안전과",
        "도로국 도로관리과",
        "철도안전정책관 철도안전정책과",
    ),
    instruction=(
        "시설물 안전 판정과 그에 따른 행정조치의 내부 자료를 쓴다. 확정 전 등급 "
        "판정과 그 산출 근거, 통보 전에 정해 둔 조치와 시점, 관리주체가 아직 "
        "모르는 확인 결과처럼 미리 알려지면 조치가 서기 전에 재산상의 부담이 "
        "옮겨 갈 수 있는 내용을 담되, 그 사안에서 다루는 것만 쓴다. 이미 고시된 "
        "안전등급과 공시 정보는 문서의 본체가 아니다. 공개 시 국민의 재산 보호에 "
        "어떤 지장이 생기는지 문맥에서 드러나게 한다."
    ),
    slots=(
        CaseSlot(
            name="관할관리청",
            place=True,
            values=(
                "서울지방국토관리청",
                "원주지방국토관리청",
                "대전지방국토관리청",
                "익산지방국토관리청",
                "부산지방국토관리청",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            # 첫 값이 결정 2가 이 템플릿 전용으로 못 박은 것이다.
            values=(
                "정기 안전점검·정밀안전진단 결과 접수",
                "집중호우·지진 이후 긴급점검 결과 접수",
                "관리주체의 보수·보강 계획 제출",
                "지반침하 신고 접수",
                "점검·진단기관 판정에 대한 이의 제기 접수",
            ),
            guidance=MappingProxyType(
                {
                    "정기 안전점검·정밀안전진단 결과 접수": (
                        "정해진 주기가 부른 건이라 사고가 있었던 것이 아니다. "
                        "판단은 이전 주기와의 차이에서만 나온다."
                    ),
                    "집중호우·지진 이후 긴급점검 결과 접수": (
                        "외력이 지나간 뒤라 이전 상태와 지금 상태의 차이가 "
                        "근거다. 원래 그랬던 것과 새로 생긴 것이 구별되어야 한다."
                    ),
                    "관리주체의 보수·보강 계획 제출": (
                        "계획을 낸 쪽이 밖에 있어 기관은 검토하는 위치에 선다. "
                        "그 계획이 다루지 않은 부분을 대신 채워 적지 않는다."
                    ),
                    "지반침하 신고 접수": (
                        "신고자가 본 것에서 시작된 건이라 원인이 아직 없다. "
                        "관측된 현상과 추정된 원인이 섞이지 않아야 한다."
                    ),
                    "점검·진단기관 판정에 대한 이의 제기 접수": (
                        "이미 나온 판정을 다투는 자리다. 처음부터 다시 판정하는 "
                        "것이 아니라 이의가 가리키는 지점에서만 판단이 움직인다."
                    ),
                }
            ),
        ),
        _MOLIT_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="시설물 안전등급 하향 판정",
            document_form=DocumentForm.REPORT,
            document_name="정밀안전진단 결과 검토보고",
            contents=(
                "시설별 현행 등급과 진단이 제시한 등급, 그 차이를 만든 항목",
                "행정청이 다시 확인한 결과와 확인이 끝나지 않은 항목",
                "검토한 시설 수와 아직 검토에 들어가지 못한 시설의 규모",
                "진단 결과를 그대로 받지 않은 항목과 그렇게 본 근거",
                "통보 전까지 관리주체와 외부에 나가지 않아야 하는 항목과 그것이 알려지면 어떤 부담이 옮겨 가는지",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="시설물 안전등급 하향 판정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="안전등급 조정(안)",
            contents=(
                "조정할 등급과 그렇게 판단한 근거, 조정에 따라 뒤따르는 조치",
                "관리주체 통보 시점과 그 시점까지 취급을 제한하는 범위",
                "조정 대상 시설 수와 조정에 따라 영향을 받는 이용 규모",
                "그 등급으로 정한 근거와 함께 검토했다가 접은 판단",
                "통보 전까지 나가지 않아야 하는 항목과 그것이 알려지면 어떤 부담이 옮겨 가는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="시설물 안전등급 하향 판정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="등급 판정 심의 회의록",
            contents=(
                "판정 근거를 두고 갈린 견해와 참석자별 발언",
                "확정된 등급과 재확인으로 넘긴 시설",
                "심의에 올라온 시설 수와 각 판정 자료를 낸 기관",
                "견해가 갈린 지점이 어느 산출 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="사용제한·통행제한 조치 결정",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="사용제한 조치 품의",
            contents=(
                "제한이 필요한 사유와 제한 범위, 그 범위로 한정한 근거",
                "시행 시점과 해제 요건, 제한에 따른 대체 통행·이용 방안",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 구간 수와 이번 제한에서 뺀 구간의 규모",
                "시행 전까지 관리주체와 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="사용제한·통행제한 조치 결정",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="제한 조치 법률 검토의견",
            contents=(
                "관계 법령상 제한 요건 충족 여부와 미비 항목",
                "관리주체의 이의 제기가 예상되는 항목과 그에 대한 검토 결론",
                "검토한 항목 수와 판단을 유보한 항목의 규모",
                "요건 판단의 근거 규정과 아직 확인되지 않은 쟁점",
                "확정 전까지 나가지 않아야 하는 항목과 그것이 알려지면 어떤 부담이 옮겨 가는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="사용제한·통행제한 조치 결정",
            document_form=DocumentForm.REPORT,
            document_name="제한 조치 시행 결과보고",
            contents=(
                "당초 정한 제한 범위와 실제 시행 실적의 대비표",
                "제한이 지켜지지 않은 구간과 그 원인",
                "시행을 마친 구간과 착수하지 못한 구간의 규모",
                "실적이 계획과 갈린 지점이 무엇에서 왔는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="재해위험 구조물 보수·보강 우선순위",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="보수·보강 우선순위 계획(안)",
            contents=(
                "대상 구조물과 위험도 판단 결과, 순위를 그렇게 정한 사유",
                "순위가 뒤로 밀린 구조물에 남는 위험과 그 기간의 임시 조치",
                "이번 계획에 든 구조물 수와 다음 차수로 미룬 구조물의 규모",
                "위험도를 그 수치로 산정한 근거와 함께 검토한 다른 기준",
                "확정 전까지 관리주체와 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="재해위험 구조물 보수·보강 우선순위",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="우선순위 심의 회의록",
            contents=(
                "순위 산정 기준을 두고 갈린 근거와 참석자별 발언",
                "확정된 순위와 보류된 구조물, 그 보류 사유",
                "심의에 올라온 구조물 수와 각 산정안을 낸 부서",
                "기준 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="재해위험 구조물 보수·보강 우선순위",
            document_form=DocumentForm.REPORT,
            document_name="보수·보강 추진 현황보고",
            contents=(
                "구조물별 착수 목표와 실제 진도의 대비표",
                "지연된 구조물의 원인과 그 사이 위험도가 더 나빠진 항목",
                "착수를 마친 구조물과 착수 전인 구조물의 규모",
                "진도 집계에서 뺀 구조물과 뺀 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="부실시공 확인 및 시정 조치",
            document_form=DocumentForm.INVESTIGATION_REPORT,
            document_name="시공 상태 확인 조사 결과",
            contents=(
                "조사 대상과 기간, 확인된 시공 상태와 설계 대비 차이",
                "그 차이가 구조물 안전에 미치는 정도와 아직 확인되지 않은 항목",
                "조사한 구간 수와 조사하지 못한 구간의 규모",
                "차이를 그 정도로 판단한 근거",
                "통보 전까지 시공·관리 관계자와 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="부실시공 확인 및 시정 조치",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="시정명령 발령 품의",
            contents=(
                "명령할 시정 사항과 그 범위로 한정한 근거",
                "이행 기한과 미이행 시 뒤따르는 조치",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 시정 사항 수와 이번 명령에서 뺀 사항의 규모",
                "발령 전까지 관리주체와 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="부실시공 확인 및 시정 조치",
            document_form=DocumentForm.REPORT,
            document_name="시정 이행 현황보고",
            contents=(
                "시정 항목별 요구 사항과 실제 이행 실적의 대비표",
                "이행이 늦은 항목의 원인과 그 구조물에 남는 위험",
                "이행을 마친 항목과 착수 전인 항목의 규모",
                "이행 여부를 확인한 방법과 확인되지 않은 항목",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="지반침하 위험 구간 확인",
            document_form=DocumentForm.REPORT,
            document_name="지반침하 위험도 평가 결과보고",
            contents=(
                "구간별 조사 결과와 위험도 판단, 판단이 갈린 구간",
                "확인이 더 필요한 구간과 그 구간에 남는 위험",
                "조사한 구간 수와 조사하지 못한 구간의 규모",
                "관측된 현상과 추정한 원인의 구분",
                "통보 전까지 나가지 않아야 하는 항목과 그것이 알려지면 어떤 부담이 옮겨 가는지",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="지반침하 위험 구간 확인",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="위험 구간 관리계획(안)",
            contents=(
                "관리 대상 구간과 관리 수준, 그 수준으로 정한 사유",
                "보완이 끝나기까지 그 구간에 두는 계측·통제 항목",
                "관리 대상 구간 수와 그 구간에 걸린 이용 규모",
                "관리 수준을 그 선으로 정한 근거와 접은 대안",
                "확정 전까지 관리주체와 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="지반침하 위험 구간 확인",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="위험 구간 현장 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "구간별 지적사항과 시정 요구 사항, 이행 기한",
                "이번 점검이 다루지 않은 구간과 범위를 좁힌 이유",
                "지적사항이 이번에 처음 나온 것인지 반복된 것인지 구분",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="점검·진단기관 판정 신뢰성 확인",
            document_form=DocumentForm.INVESTIGATION_REPORT,
            document_name="판정 재확인 조사 결과",
            contents=(
                "재확인 대상과 기간, 기관이 낸 판정과 행정청 확인 결과의 차이",
                "차이가 난 항목의 원인과 그로 인해 다시 봐야 하는 시설의 범위",
                "재확인한 시설 수와 아직 재확인하지 못한 시설의 규모",
                "이의가 가리킨 지점과 그 밖에서 판단이 움직이지 않은 이유",
                "통보 전까지 이의 제기인과 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="점검·진단기관 판정 신뢰성 확인",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="처분 여부 심의 회의록",
            contents=(
                "처분 수위를 두고 갈린 근거와 참석자별 발언",
                "결정된 처분과 보류된 처분, 그 보류 사유",
                "심의에 올라온 건수와 각 검토 자료를 낸 부서",
                "수위 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="점검·진단기관 판정 신뢰성 확인",
            document_form=DocumentForm.REPORT,
            document_name="재확인 후속조치 현황보고",
            contents=(
                "재확인이 필요한 시설 수와 처리 실적의 대비표",
                "아직 확인되지 않은 시설과 그 시설에 남는 위험",
                "처리를 마친 시설과 착수 전인 시설의 규모",
                "처리가 늦은 사유가 우리 손 안의 것인지 밖의 것인지 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 검찰청 역할구성.
_SPO_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 부서 단독 검토",
        "대검-일선청 합동 검토",
        "경찰·국세청 등 유관기관 협의",
        "과학수사 부서가 참여하는 검토",
    ),
    guidance=MappingProxyType(
        {
            "담당 부서 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 부장 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 부서나 기관 의견을 끌어들이지 않는다."
            ),
            "대검-일선청 합동 검토": (
                "대검이 요구한 사항을 먼저 적고, 일선청이 올린 현장 의견을 적은 "
                "다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 끝낸다. 현장 "
                "의견은 대검 판단과 다른 내용을 담아야 한다."
            ),
            "경찰·국세청 등 유관기관 협의": (
                "협의를 요청한 사유를 먼저 적고, 기관별 입장 표명을 적은 다음, "
                "입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 것으로 "
                "끝낸다. 기관마다 서로 다른 소관 사정이 드러나야 한다."
            ),
            "과학수사 부서가 참여하는 검토": (
                "의뢰한 확인 항목과 그 사유를 먼저 적고, 분석 부서가 낸 회신 "
                "결과와 그 한계를 적은 다음, 회신으로 가려지지 않은 항목을 짚고, "
                "추가로 의뢰할 것과 그대로 두는 것으로 끝낸다. 분석 부서 설명은 "
                "수사 부서 판단과 다른 내용을 담아야 한다."
            ),
        }
    ),
)


#: 검찰청 / 제4호 ``trial_investigation``. 일곱 번째 C트랙 템플릿이다.
#:
#: **기관은 검찰청으로 골랐다.** 초안에서 경찰청을 세우려다 접었는데, 그때 든
#: 근거("화이트리스트 밖이고 로고가 없어 렌더링이 깨진다")는 틀렸다 —
#: ``FALLBACK_AGENCY_WHITELIST``는 rd2 DB에 안보·수사 계열 기관이 0건이라 **레거시
#: 폴백 생성**이 쓰는 목록이고(그 상수 주석), C트랙은 템플릿이 ``agency``를 직접
#: 들어 ``c_track_writeback``의 ``ordering_agency``로 넘긴다. 로고도 이름으로
#: 추측하지 않는다(``official_document_rendering``: "입력 기관명은 그대로 보존하고
#: 실제 기관 로고를 추측하지 않는다"). 즉 기관 선택에 기술적 제약은 없고, 남는
#: 근거는 아래의 세부조항 경계뿐이다.
#:
#: **``prosecution``과의 경계가 이 템플릿의 요건이다.** 감사 결정 3이 두 브로커를
#: 갈라 뒀다 — 이쪽 브로커는 "수사선상에 있다는 사실 자체"를 팔고, 저쪽 브로커는
#: "처분 결론이 어느 쪽으로 기울었는지"를 판다. 그래서 여기서는 공소 제기 여부와
#: 공판 대응이 사안으로 서지 않고, 부서축에서도 공판송무부를 뺐다. 남은
#: ``prosecution`` 템플릿은 고위공직자범죄수사처로 두어 장소축까지 갈린다 —
#: 같은 기관에 인접 세부조항 둘을 얹으면 결정 2가 경계한 자리가 된다.
#:
#: **수사 방법을 적는 자리를 처음부터 막았다.** ``_BOUNDARY_RULE``이 "실행 절차·
#: 수법·회피 방법 자리에는 무엇이 무력화되는지를 적는다"고 했고, 감사가
#: ``correction_security``에서 12건이 그 선을 넘은 방식을 셋으로 기록해 뒀다
#: (통제의 공백을 이득으로 지목 / 회피 방법을 이득에 적기 / 수단 열거).
#: 적대자 넷은 전부 "상대가 확인 전에 알게 된다"는 결과에 서 있고, 사안의
#: ``contents``는 방법이 아니라 **무엇을 확인하려 했고 무엇이 아직 확인되지
#: 않았는지**를 묻는다.
#:
#: 조항이 새는 자리 넷. 기소 의견·공판 대응(제4호 ``prosecution`` 정면) /
#: 참고인·제보자의 소재(제3호 ``life_body``) / 수용·이송 등 형 집행(제4호
#: ``correction_security``) / 확정 판결문의 공개된 주문과 이유(이 세부조항의
#: ``excludes``).
_TRIAL_INVESTIGATION_SPO = CTrackTemplate(
    subclause_key=SubclauseKey.TRIAL_INVESTIGATION,
    agency="검찰청",
    persona_context="수사 착수 판단과 수사 진행 관리 업무",
    adversaries=(
        Adversary(
            who="수사 대상에 오른 사람",
            position="external",
            what_they_gain=(
                "자신이 수사선상에 있다는 사실과 그 범위를 미리 알면, 확인이 "
                "끝나기 전까지 상대가 그것을 모른다는 전제 위에 서 있던 수사가 "
                "무력화된다."
            ),
        ),
        Adversary(
            who="수사선상에 있다는 사실 자체를 파는 브로커",
            position="intermediary",
            what_they_gain=(
                "누가 대상인지가 확인 전에 밖으로 나가면, 그 사실이 수사기관 "
                "안에 머문다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="확인 전 단서를 보도 재료로 삼는 외부 관계자",
            position="external",
            what_they_gain=(
                "가려지지 않은 단서가 먼저 알려지면, 확인된 것만 공표된다는 "
                "전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 수사 기록을 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "확인 전 단서와 예정된 조치 시점이 인가 밖으로 퍼지면, 수사와 "
                "공표가 정해진 순서로 이뤄진다는 전제가 무력화된다."
            ),
        ),
    ),
    # 2026-08-05에 대조한 대검찰청 현행 부서다(spo.go.kr 부서별 연락처).
    # 공판송무부(공판1·2과·집행과)는 넣지 않았다 — ``prosecution``의 자리다.
    departments=(
        "반부패부 반부패1과",
        "반부패부 반부패2과",
        "형사부 형사1과",
        "형사부 형사2과",
        "공공수사부 공안수사지원과",
        "마약·조직범죄부 마약과",
        "과학수사부 디지털수사과",
    ),
    instruction=(
        "수사 착수 판단과 수사 진행 관리의 내부 자료를 쓴다. 확인 전 단서와 그에 "
        "대한 판단, 강제수사를 언제 어느 범위로 할지, 아직 확인되지 않은 항목처럼 "
        "미리 알려지면 상대가 확인 전에 알게 되어 수사 직무수행이 현저히 곤란해지는 "
        "내용을 담되, 그 사안에서 다루는 것만 쓴다. 공소 제기 여부 판단과 공판 "
        "대응 방침은 이 문서가 다루는 것이 아니다. 수사 방법과 절차를 적을 자리에는 "
        "무엇을 확인하려 했고 무엇이 아직 확인되지 않았는지를 적는다."
    ),
    slots=(
        CaseSlot(
            name="관할청",
            place=True,
            values=(
                "서울중앙지방검찰청",
                "서울동부지방검찰청",
                "인천지방검찰청",
                "수원지방검찰청",
                "부산지방검찰청",
                "광주지방검찰청",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "고소·고발장 접수",
                "관계기관 수사의뢰 접수",
                "내사 단서 확보 보고",
                "압수물 분석 결과 회신",
                "장기 미제사건 점검 결과 접수",
            ),
            guidance=MappingProxyType(
                {
                    "고소·고발장 접수": (
                        "밖에서 들어온 주장으로 시작된 건이라 아직 확인된 사실이 "
                        "없다. 고발인이 말한 것과 기관이 확인한 것이 구별되어야 한다."
                    ),
                    "관계기관 수사의뢰 접수": (
                        "다른 기관이 먼저 본 건이라 그쪽이 확인한 범위가 "
                        "출발선이다. 그 범위 밖은 아직 아무것도 정해지지 않았다."
                    ),
                    "내사 단서 확보 보고": (
                        "기관이 스스로 잡은 건이라 상대가 아직 모른다. 알려지지 "
                        "않았다는 전제 위에서만 성립하는 판단이 본문에 있다."
                    ),
                    "압수물 분석 결과 회신": (
                        "분석이 답한 범위 안에서만 말한다. 분석되지 않은 것을 "
                        "결과로 적지 않는다."
                    ),
                    "장기 미제사건 점검 결과 접수": (
                        "시간이 지나 다시 본 건이라 새 사실이 아니라 놓친 것이 "
                        "근거다. 당시 판단과 지금 판단의 차이가 어디서 왔는지가 "
                        "서야 한다."
                    ),
                }
            ),
        ),
        _SPO_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="내사 단서 검토",
            document_form=DocumentForm.REPORT,
            document_name="내사 단서 검토보고",
            # 216개를 5항목으로 맞춘 실험이 여기서 시작됐다(2026-08-05). 근거는
            # ``SubjectCase.contents`` 주석에 있다.
            contents=(
                "확보된 단서의 내용과 출처 구분, 그것으로 확인되는 범위",
                "단서만으로는 가려지지 않는 항목과 그 이유",
                "단서별 확인 진척과 아직 확인에 들어가지 못한 대상의 규모",
                "이 단서로 좁혀진 대상 범위와 그 범위로 좁힌 근거",
                "확인이 끝나기 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="내사 단서 검토",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="수사 착수 여부 검토(안)",
            contents=(
                "착수·보류·종결 안별 판단 근거와 각 안이 지는 부담",
                "착수할 경우의 대상 범위와 시기, 그 범위로 한정한 사유",
                "검토에 오른 단서 수와 판단이 서지 않은 단서의 규모",
                "각 안을 가른 기준과 그 기준으로 판단되지 않는 항목",
                "결정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="내사 단서 검토",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="착수 여부 검토 회의록",
            contents=(
                "착수 여부를 두고 갈린 근거와 참석자별 발언",
                "결정된 방향과 보류된 사항, 그 보류 사유",
                "회의에 올라온 검토 자료와 그 자료를 낸 부서",
                "판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "결정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="강제수사 실시 여부 결정",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="강제수사 실시 품의",
            contents=(
                "실시가 필요한 사유와 대상 범위, 그 범위로 한정한 근거",
                "실시 시기와 그 시기로 정한 이유, 소요 인력 규모",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 대상 수와 이번 실시에서 뺀 대상의 규모",
                "결재가 끝나기 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="강제수사 실시 여부 결정",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="강제수사 요건 법률 검토의견",
            contents=(
                "법령상 요건 충족 여부와 소명이 부족한 항목",
                "다툼이 예상되는 쟁점과 그에 대한 검토 결론",
                "검토한 항목 수와 판단을 유보한 항목의 규모",
                "요건 판단의 근거 규정과 아직 확인되지 않은 쟁점",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="강제수사 실시 여부 결정",
            document_form=DocumentForm.REPORT,
            document_name="강제수사 실시 결과보고",
            contents=(
                "당초 정한 대상 범위와 실제 확보한 자료의 대비표",
                "확보하지 못한 항목과 그 원인, 그로 인해 남은 확인 과제",
                "확보를 마친 대상과 미착수 대상의 규모",
                "확보된 자료로 가려진 범위와 아직 가려지지 않은 범위의 구분",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="수사 진행 상황 점검",
            document_form=DocumentForm.REPORT,
            document_name="수사 진행 현황보고",
            contents=(
                "당초 수사 계획과 항목별 진행 상황의 대비표",
                "확인이 늦어진 항목과 그 원인",
                "확인을 마친 항목과 착수 전인 항목의 규모",
                "지연 사유가 우리 손 안의 것인지 밖의 것인지 구분",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="수사 진행 상황 점검",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="수사 지휘 회의록",
            contents=(
                "확인 우선순위를 두고 갈린 근거와 참석자별 발언",
                "지휘된 사항과 보류된 사항, 그 보류 사유",
                "회의에 올라온 확인 항목 수와 각 안을 낸 부서",
                "우선순위 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "지휘 확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="수사 진행 상황 점검",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="수사 계획 조정(안)",
            contents=(
                "조정 전후의 확인 항목과 순서, 조정이 필요해진 사유",
                "조정에 따라 뒤로 밀리는 항목과 그때까지 남는 미확인 범위",
                "조정에 걸리는 항목 수와 그대로 두는 항목의 규모",
                "그 순서로 다시 정한 근거와 함께 검토했다가 접은 배열",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="관계기관 공조 수사",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="공조 수사 추진계획(안)",
            contents=(
                "기관별 분담 항목과 그렇게 나눈 사유",
                "공조로도 확인되지 않을 것으로 보는 항목과 그 이유",
                "공조에 드는 기관 수와 각 기관이 맡는 항목의 규모",
                "그 분담을 정한 근거와 함께 검토했다가 접은 안",
                "협의 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="관계기관 공조 수사",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="공조 협의 회의록",
            contents=(
                "분담과 자료 제공 범위를 두고 갈린 근거와 기관별 발언",
                "합의된 것과 미합의로 남은 사항",
                "협의에 올라온 항목 수와 각 안을 낸 기관",
                "기관별 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="관계기관 공조 수사",
            document_form=DocumentForm.REPORT,
            document_name="공조 수사 결과보고",
            contents=(
                "기관별 분담 항목과 실제 회신 실적의 대비표",
                "회신이 오지 않은 항목과 그로 인해 남은 확인 과제",
                "회신을 받은 항목과 아직 받지 못한 항목의 규모",
                "회신으로 가려진 범위와 가려지지 않은 범위의 구분",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="압수물·디지털 증거 분석 관리",
            document_form=DocumentForm.INVESTIGATION_REPORT,
            document_name="증거 분석 회신 검토 결과",
            contents=(
                "의뢰한 확인 항목과 회신된 분석 결과, 그 결과로 가려진 범위",
                "회신으로도 가려지지 않은 항목과 추가 의뢰가 필요한 것",
                "의뢰한 건수와 회신을 받은 건수의 규모",
                "분석이 답한 범위와 답하지 않은 범위의 구분",
                "확인이 끝나기 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="압수물·디지털 증거 분석 관리",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="분석 의뢰 우선순위 조정(안)",
            contents=(
                "의뢰 대상과 순서, 그 순서로 정한 사유",
                "뒤로 밀리는 의뢰와 그때까지 확인되지 않는 범위",
                "의뢰 대기 중인 건수와 이번 차수에 넣는 건수의 규모",
                "그 순서를 정한 근거와 함께 검토했다가 접은 배열",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="압수물·디지털 증거 분석 관리",
            document_form=DocumentForm.REPORT,
            document_name="분석 의뢰 처리 현황보고",
            contents=(
                "의뢰 건수와 회신 실적의 항목별 대비표",
                "회신이 늦은 항목의 원인과 수사 일정에 미치는 영향",
                "처리를 마친 건과 대기 중인 건의 규모",
                "집계에서 뺀 의뢰와 뺀 이유",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="장기 미제사건 처리 점검",
            document_form=DocumentForm.REPORT,
            document_name="장기 미제사건 점검 결과보고",
            contents=(
                "사건별 경과 기간과 남아 있는 확인 항목의 대비표",
                "진행이 멈춘 사건의 원인 구분",
                "점검한 사건 수와 점검하지 못한 사건의 규모",
                "당시 판단과 지금 판단이 갈린 지점이 무엇에서 왔는지",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="장기 미제사건 처리 점검",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="미제사건 처리 계획(안)",
            contents=(
                "재개·유지·종결 안별 판단 근거와 각 안이 지는 부담",
                "재개할 사건의 확인 항목과 그 순서를 정한 사유",
                "재개 대상 사건 수와 유지·종결로 두는 사건의 규모",
                "각 안을 가른 기준과 그 기준으로 판단되지 않는 사건",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="장기 미제사건 처리 점검",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="미제사건 처리 심의 회의록",
            contents=(
                "처리 방향을 두고 갈린 근거와 참석자별 발언",
                "결정된 처리와 보류된 사건, 그 보류 사유",
                "심의에 올라온 사건 수와 각 검토 자료를 낸 부서",
                "방향 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "결정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
    ),
)


#: 고위공직자범죄수사처 역할구성.
_CIO_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "주임검사 단독 검토",
        "부장검사가 주재하는 부 내 검토",
        "수사부와 공판 담당의 협의",
        "외부 위원이 참여하는 심의",
    ),
    guidance=MappingProxyType(
        {
            "주임검사 단독 검토": (
                "주임검사가 쓴 것만 적는다. 검토 의견을 먼저 적고, 그 의견이 딛고 "
                "선 근거를 적은 다음, 결재선에 올릴 결론으로 끝낸다. 다른 사람의 "
                "발언을 등장시키지 않는다."
            ),
            "부장검사가 주재하는 부 내 검토": (
                "주임검사의 보고를 먼저 적고, 부장검사의 질의와 그에 대한 답변을 "
                "적은 다음, 보완이 필요하다고 지적된 항목을 짚고, 지시된 방향으로 "
                "끝낸다. 지적은 보고 내용과 다른 판단을 담아야 한다."
            ),
            "수사부와 공판 담당의 협의": (
                "수사부가 확보했다고 보는 것을 먼저 적고, 공판 담당이 법정에서 "
                "다투어질 것으로 보는 항목을 적은 다음, 둘의 판단이 어긋난 지점을 "
                "짚고, 보완하기로 한 것과 그대로 가기로 한 것으로 끝낸다."
            ),
            "외부 위원이 참여하는 심의": (
                "안건 상정과 담당 부서의 설명을 먼저 적고, 위원별 질의와 그에 "
                "대한 답변을 적은 다음, 쟁점별 견해와 근거를 적고, 의결 또는 보류 "
                "결정과 그 사유로 끝낸다. 뒤에 오는 발언은 앞에서 나온 말을 받아야 "
                "한다."
            ),
        }
    ),
)


#: 고위공직자범죄수사처 / 제4호 ``prosecution``. 여덟 번째 C트랙 템플릿이다.
#:
#: **기관을 검찰청과 갈랐다.** ``FALLBACK_AGENCY_WHITELIST["4"]``가 검찰청·
#: 고위공직자범죄수사처·법무부 셋이고, 그중 법무부는 ``correction_security``가
#: 이미 쓴다. 남은 둘을 ``trial_investigation``·``prosecution``에 하나씩 주면
#: 인접 세부조항이 같은 기관에 겹치지 않는다 — 결정 2가 경계한 자리다.
#:
#: **결정 3이 이 템플릿의 요건이다.** 검찰청 쪽 브로커는 "수사선상에 있다는 사실
#: 자체"를 팔고, 이쪽 브로커는 "처분 결론이 어느 쪽으로 기울었는지"를 판다. 그
#: 차이가 사안에도 그대로 온다 — 여기서는 단서를 모으는 일이 사안이 아니고,
#: **기소 여부 판단과 법정에서 다투어질 것에 대한 대비**만 사안이 된다.
#:
#: 장소축은 심급별 법원이다. 검찰청 템플릿이 일선 지검을 쓰므로 그쪽과 겹치지
#: 않고, 공소 유지 문서가 향하는 곳이 법정이라 사안과도 맞는다.
#:
#: 조항이 새는 자리 넷. 단서 확보와 강제수사 계획(제4호 ``trial_investigation``
#: 정면) / 증인의 소재와 신변 보호(제3호 ``life_body``) / 형 집행과 수용(제4호
#: ``correction_security``) / 이미 공표된 기소 사실과 죄명(이 세부조항의
#: ``excludes``).
_PROSECUTION_CIO = CTrackTemplate(
    subclause_key=SubclauseKey.PROSECUTION,
    agency="고위공직자범죄수사처",
    persona_context="공소 제기 여부 판단과 공소 유지 업무",
    adversaries=(
        Adversary(
            who="처분을 기다리는 피의자와 그 변호인",
            position="external",
            what_they_gain=(
                "결론이 어느 쪽으로 기울었고 무엇이 약한 고리로 꼽혔는지를 처분 "
                "전에 알면, 양쪽이 같은 시점에 법정에서 다툰다는 전제가 "
                "무력화된다."
            ),
        ),
        Adversary(
            who="처분 결론이 어느 쪽으로 기울었는지를 파는 브로커",
            position="intermediary",
            what_they_gain=(
                "확정 전 판단이 밖으로 나가면, 처분이 공표로만 알려진다는 전제가 "
                "성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="사건의 결론에 이해가 걸린 소속 기관 관계자",
            position="external",
            what_they_gain=(
                "누구까지 기소 대상으로 검토되는지가 결정 전에 알려지면, 처분이 "
                "정해지기 전까지 그 범위가 밖에서 보이지 않는다는 전제가 "
                "무력화된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 검토 자료를 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "확정 전 의견과 공판에서 다툴 항목이 인가 밖으로 퍼지면, 처분과 "
                "공표가 정해진 순서로 이뤄진다는 전제가 무력화된다."
            ),
        ),
    ),
    # 2023-12-18 직제 개정으로 공소부가 수사4부로 개편됐고, 송무는 인권수사
    # 정책관실로, 사면·형사보상 사무는 사건관리담당관실로 갔다(확인 2026-08-05).
    # 부 이름만으로는 과 단위가 없는 기관이라 부·관실이 곧 작성 단위다.
    departments=(
        "수사1부",
        "수사2부",
        "수사3부",
        "수사4부",
        "인권수사정책관실",
        "사건관리담당관실",
        "수사기획관실",
    ),
    instruction=(
        "공소 제기 여부 판단과 공소 유지 대비의 내부 자료를 쓴다. 확정 전 기소·"
        "불기소 의견과 그 법리 검토, 우리가 약하다고 보는 고리, 법정에서 다투어질 "
        "것으로 보는 항목과 그에 대한 대비처럼 미리 알려지면 상대가 처분 전에 그 "
        "판단을 알게 되어 형사절차의 공정이 깨지는 내용을 담되, 그 사안에서 다루는 "
        "것만 쓴다. 단서를 모으는 일과 강제수사 계획은 이 문서가 다루는 것이 "
        "아니다. 증인은 진술로 다투어질 항목으로만 다루고 소재와 신변은 적지 "
        "않는다."
    ),
    slots=(
        CaseSlot(
            name="대상법원",
            place=True,
            values=(
                "서울중앙지방법원",
                "서울고등법원",
                "대법원",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "수사 종결 보고 접수",
                "피의자 측 의견서 제출",
                "공판기일 지정 통지",
                "관련 사건 판결 선고",
                "보완 수사 결과 회신",
            ),
            guidance=MappingProxyType(
                {
                    "수사 종결 보고 접수": (
                        "수사가 끝난 자리라 새 사실이 더해지지 않는다. 이미 "
                        "확보된 것만으로 결론이 서야 한다."
                    ),
                    "피의자 측 의견서 제출": (
                        "상대 주장이 문서 안으로 들어온 건이라 그 주장과 기관 "
                        "판단이 구별되어야 한다. 반박하지 않은 부분도 그대로 남는다."
                    ),
                    "공판기일 지정 통지": (
                        "날짜가 법원에서 정해져 준비 기간이 고정돼 있다. 그 안에 "
                        "못 하는 것이 함께 서야 한다."
                    ),
                    "관련 사건 판결 선고": (
                        "다른 사건에서 나온 판단이 이 건의 전제를 바꿨다. 무엇이 "
                        "바뀌었고 무엇은 그대로인지가 갈려야 한다."
                    ),
                    "보완 수사 결과 회신": (
                        "요구했던 것에 대한 답이 돌아온 자리다. 요구한 범위와 "
                        "답이 채운 범위의 차이가 판단의 근거가 된다."
                    ),
                }
            ),
        ),
        _CIO_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="기소 여부 판단",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="기소 의견 법률 검토의견",
            contents=(
                "적용 법조와 구성요건별 충족 여부, 소명이 부족한 요건",
                "반대 결론이 가능한 지점과 그에 대한 검토 결론",
                "검토한 구성요건 수와 판단을 유보한 요건의 규모",
                "확보된 자료로 가려진 범위와 아직 가려지지 않은 범위의 구분",
                "처분 전까지 기관 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="기소 여부 판단",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="처분 방향 결재 품의",
            contents=(
                "승인받을 처분 방향과 그 범위로 한정한 사유",
                "대상자별 처분 구분과 그렇게 나눈 근거",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 대상자 수와 이번 처분에서 판단을 미룬 대상의 규모",
                "결재가 끝나기 전까지 대상자와 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="기소 여부 판단",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="처분 방향 검토 회의록",
            contents=(
                "기소·불기소를 두고 갈린 근거와 참석자별 발언",
                "결정된 방향과 보류된 대상자, 그 보류 사유",
                "회의에 올라온 검토 자료와 그 자료를 낸 부서",
                "판단이 갈린 지점이 어느 법리 전제 차이에서 왔는지",
                "결정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="공판 대응 방침 수립",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="공판 대응 방침(안)",
            contents=(
                "쟁점별 우리 주장과 그것을 받치는 증거의 강약 판단",
                "상대가 다툴 것으로 보는 항목과 그에 대한 대응 논리",
                "쟁점 수와 그중 우리가 약하다고 보는 고리의 범위",
                "강약을 그렇게 판단한 근거와 아직 확인되지 않은 부분",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="공판 대응 방침 수립",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="공판 대응 협의 회의록",
            contents=(
                "쟁점별 대응 수위를 두고 갈린 근거와 참석자별 발언",
                "확정된 방침과 재검토로 넘긴 쟁점",
                "협의에 올라온 쟁점 수와 각 안을 낸 부서",
                "수위 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="공판 대응 방침 수립",
            document_form=DocumentForm.REPORT,
            document_name="공판 진행 경과보고",
            contents=(
                "당초 방침과 실제 심리 결과의 쟁점별 대비표",
                "방침과 어긋난 쟁점의 원인과 다음 기일 대응 과제",
                "정리된 쟁점과 남은 쟁점의 규모",
                "상대 주장 중 이번에 처음 드러난 것과 기존 판단대로였던 것의 구분",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="증거 채부 대비",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="증거능력 검토의견",
            contents=(
                "증거별 채부가 다투어질 지점과 그 근거",
                "배제될 경우 남는 입증 범위와 보완이 필요한 항목",
                "검토한 증거 수와 배제 위험이 크다고 본 증거의 규모",
                "위험을 그렇게 판단한 근거와 아직 확인되지 않은 쟁점",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="증거 채부 대비",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="입증 계획 조정(안)",
            contents=(
                "쟁점별 입증 순서와 그 순서로 정한 사유",
                "배제 위험이 큰 증거를 대신할 항목과 그 한계",
                "조정에 걸리는 쟁점 수와 그대로 두는 쟁점의 규모",
                "그 순서로 정한 근거와 함께 검토했다가 접은 배열",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="증거 채부 대비",
            document_form=DocumentForm.REPORT,
            document_name="증거 채부 결과 검토보고",
            contents=(
                "신청한 증거와 채택·기각 결과의 대비표",
                "기각된 증거로 비게 된 입증 범위와 보완 방향",
                "신청 건수와 채택된 건수의 규모",
                "기각 사유 중 예상했던 것과 예상하지 못했던 것의 구분",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="상소 여부 판단",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="상소 이유 법률 검토의견",
            contents=(
                "판결 이유 중 다툴 지점과 그 법리적 근거",
                "상소로 뒤집힐 가능성이 낮다고 보는 항목과 그 이유",
                "검토한 쟁점 수와 다투기로 한 쟁점의 규모",
                "가능성을 그렇게 판단한 근거와 아직 확인되지 않은 부분",
                "제기 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="상소 여부 판단",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="상소 제기 품의",
            contents=(
                "상소가 필요한 사유와 다툴 범위, 그 범위로 한정한 근거",
                "제기 기한과 준비에 드는 소요",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 쟁점 수와 이번 상소에서 뺀 쟁점의 규모",
                "결재가 끝나기 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="상소 여부 판단",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="상소 여부 심의 회의록",
            contents=(
                "상소 실익을 두고 갈린 근거와 참석자별 발언",
                "결정된 방향과 보류된 쟁점, 그 보류 사유",
                "심의에 올라온 쟁점 수와 각 검토 자료를 낸 부서",
                "실익 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "결정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="관련 사건 처리 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="관련 사건 처리 조정(안)",
            contents=(
                "함께 다루는 사건별 처리 순서와 그 순서로 정한 사유",
                "한쪽 결론이 다른 쪽에 미치는 영향과 그에 대한 대비",
                "함께 걸린 사건 수와 이번 조정에서 뺀 사건의 규모",
                "그 순서를 정한 근거와 함께 검토했다가 접은 배열",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="관련 사건 처리 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="관련 사건 협의 회의록",
            contents=(
                "처리 순서와 이송 여부를 두고 갈린 근거와 참석자별 발언",
                "합의된 것과 미합의로 남은 사건",
                "협의에 올라온 사건 수와 각 안을 낸 부서",
                "판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="관련 사건 처리 조정",
            document_form=DocumentForm.REPORT,
            document_name="관련 사건 처리 현황보고",
            contents=(
                "사건별 처리 목표와 실제 진행의 대비표",
                "지연된 사건의 원인과 다른 사건에 미치는 영향",
                "처리를 마친 사건과 착수 전인 사건의 규모",
                "지연 사유가 우리 손 안의 것인지 밖의 것인지 구분",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="처분 결과 공표 범위 검토",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="공표 범위 검토(안)",
            contents=(
                "공표할 항목과 공표하지 않을 항목의 구분, 그 기준",
                "공표 시점과 그 시점까지 취급을 제한하는 범위",
                "검토한 항목 수와 공표하지 않기로 한 항목의 규모",
                "그 기준으로 항목을 가른 근거와 함께 검토한 다른 기준",
                "공표 전까지 기관 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="처분 결과 공표 범위 검토",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="공표 범위 법률 검토의견",
            contents=(
                "관계 법령상 공표가 가능한 범위와 제한되는 항목",
                "다툼이 예상되는 항목과 그에 대한 검토 결론",
                "검토한 항목 수와 판단을 유보한 항목의 규모",
                "제한 판단의 근거 규정과 아직 확인되지 않은 쟁점",
                "확정 전까지 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="처분 결과 공표 범위 검토",
            document_form=DocumentForm.REPORT,
            document_name="공표 시행 결과보고",
            contents=(
                "당초 정한 공표 범위와 실제 공표 내용의 대비표",
                "범위를 벗어난 항목이 나온 경위와 그 영향",
                "공표한 항목 수와 끝내 공표하지 않은 항목의 규모",
                "벗어남이 어느 단계에서 생겼는지와 그 경로",
                "보고 이후에도 기관 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 국가정보원 역할구성.
_NIS_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 센터 단독 검토",
        "보안심사 부서와의 합동 검토",
        "대상 기관 보안담당관과의 협의",
        "외부 위원이 참여하는 심의",
    ),
    guidance=MappingProxyType(
        {
            "담당 센터 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 상급자 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 부서나 기관 의견을 끌어들이지 않는다."
            ),
            "보안심사 부서와의 합동 검토": (
                "담당 센터가 요구한 사항을 먼저 적고, 심사 부서가 든 규정상 "
                "제약을 적은 다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 "
                "끝낸다. 심사 부서 의견은 담당 센터 판단과 다른 내용을 담아야 "
                "한다."
            ),
            "대상 기관 보안담당관과의 협의": (
                "요구한 조치와 그 근거 조문을 먼저 적고, 대상 기관이 제기한 "
                "이행상의 어려움을 적은 다음, 요구와 현장 사정이 어긋난 지점을 "
                "짚고, 조정된 것과 물러서지 않은 것으로 끝낸다. 대상 기관 "
                "의견은 우리 판단과 다른 내용을 담아야 한다."
            ),
            "외부 위원이 참여하는 심의": (
                "안건 상정과 담당 부서의 설명을 먼저 적고, 위원별 질의와 그에 "
                "대한 답변을 적은 다음, 쟁점별 견해와 근거를 적고, 의결 또는 "
                "보류 결정과 그 사유로 끝낸다. 뒤에 오는 발언은 앞에서 나온 말을 "
                "받아야 한다."
            ),
        }
    ),
)


#: 국가정보원 / 제1호 ``legal_secret``. 아홉 번째이자 마지막 C트랙 템플릿이다.
#:
#: **이 세부조항은 내용이 아니라 형식으로 선다.** 판정 기준이 "다른 법률 또는
#: 법률이 위임한 명령이 해당 정보를 비밀 또는 비공개로 규정한 **근거가 문서에
#: 드러나는** 경우"이고, ``excludes``의 첫 줄이 "'대외비'·'비밀유지' 표기만 있고
#: 근거 법령이 없는 경우"다. 그래서 다른 여덟과 달리 **모든 사안의 contents가
#: 근거 조문을 요구한다** — 조문이 값으로 들어오지 않으면 그 문서는 이 세부조항이
#: 아니다.
#:
#: 기관은 ``FALLBACK_AGENCY_WHITELIST["1"]``의 넷(국가정보원·국방부·검찰청·
#: 고위공직자범죄수사처) 중 나머지 셋이 이미 다른 세부조항에 쓰여 국가정보원이
#: 남는다. ``is_military_secret_agency``가 참이라 등급축이 1~3급 셋이다.
#:
#: **부서는 공개된 센터만 쓴다.** 이 기관은 조직 대부분이 비공개라 실존명 원칙을
#: 지킬 수 있는 범위가 공개 센터로 한정된다(nis.go.kr, 확인 2026-08-05).
#:
#: 조항이 새는 자리 넷. 보안 취약점의 진단 결과와 그 목록(제7호
#: ``security_diagnosis``) / 인가 대상자의 신원 사항(제6호 ``personnel_pii``) /
#: 군사 대비태세와 전력(제2호 ``security_defense``) / 근거 조문 없이 '대외비'
#: 표기만 있는 문서(이 세부조항의 ``excludes`` 정면).
_LEGAL_SECRET_NIS = CTrackTemplate(
    subclause_key=SubclauseKey.LEGAL_SECRET,
    agency="국가정보원",
    persona_context="비밀 지정·관리와 보안업무 감독 업무",
    adversaries=(
        Adversary(
            who="인가 없이 자료에 닿으려는 외부인",
            position="external",
            what_they_gain=(
                "무엇이 어느 등급으로 지정돼 어디까지 도는지가 밖에서 읽히면, "
                "인가받은 사람만 그것을 안다는 전제가 무력화된다."
            ),
        ),
        Adversary(
            who="우리 기관이 무엇을 다루는지 알아내려는 국외 정보수집 조직",
            position="external",
            what_they_gain=(
                "무엇을 비밀로 지정해 두었는지가 알려지면, 우리가 어디에 손을 "
                "대고 있는지 밖에서 알 수 없다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="협조 요청을 매개로 자료를 받는 다른 기관 관계자",
            position="intermediary",
            what_they_gain=(
                "제공 범위가 정해지기 전에 그 논의가 밖으로 나가면, 제공이 정해진 "
                "범위 안에서만 이뤄진다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="인가 범위를 넘어 자료를 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "누가 무엇까지 인가받았는지가 인가 밖으로 퍼지면, 취급이 인가 "
                "범위 안에 머문다는 전제가 무력화된다."
            ),
        ),
    ),
    departments=(
        "국가사이버안보센터",
        "산업기밀보호센터",
        "국제범죄정보센터",
        "테러정보통합센터",
        "방첩정보공유센터",
        "국가우주안보센터",
        "방위산업침해대응센터",
    ),
    instruction=(
        "법령이 비밀 또는 비공개로 정한 사항의 지정·관리·제공에 관한 내부 자료를 "
        "쓴다. **어느 법률의 어느 조문이 그것을 비밀로 정하고 있는지를 본문에 "
        "값으로 적는다** — 근거 조문 없이 '대외비'·'비밀유지' 표기만 있으면 이 "
        "문서는 성립하지 않는다. 지정 등급과 그 사유, 취급 범위와 인가 단위, 제공 "
        "요청에 대한 판단처럼 미리 알려지면 인가받은 사람만 그것을 안다는 전제가 "
        "깨지는 내용을 담되, 그 사안에서 다루는 것만 쓴다. 보안 취약점의 진단 "
        "결과와 대상자의 신원 사항은 이 문서가 다루는 것이 아니다."
    ),
    slots=(
        # 초안은 ``대상기관``(국방부·외교부·과기정통부…)이었고 어긋났다(사용자
        # 지적, 2026-08-05): 부서축이 주제별 정보 업무 센터라 `국가우주안보센터`가
        # `외교부 관련 수사자료`의 재분류를 심의하는 조합이 대량으로 나온다.
        # 자료군으로 바꾼 뒤에도 축이 따로 돌아 절반쯤은 여전히 어긋났으므로
        # ``department_paired_slot``으로 부서와 한 자리에 묶었다 — 값 순서가
        # ``departments`` 순서와 1:1로 맞는다.
        CaseSlot(
            name="대상자료군",
            place=True,
            values=(
                "사이버 위협 대응 자료",
                "산업기술 유출 대응 자료",
                "국제범죄 정보 자료",
                "테러 정보 통합 자료",
                "방첩 정보 공유 자료",
                "우주 자산 안보 자료",
                "방위산업 침해 대응 자료",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "비밀 재분류 주기 도래",
                "수사기관의 자료 제공 요청 접수",
                "국회 자료 제출 요구 접수",
                "보안규정 위반 사실 통보",
                "관계 법령 개정 시행",
            ),
            guidance=MappingProxyType(
                {
                    "비밀 재분류 주기 도래": (
                        "정해진 시기가 부른 건이라 사정이 달라지지 않았다. "
                        "달라진 것은 시간이 지났다는 사실뿐이고 판단은 거기서만 "
                        "나온다."
                    ),
                    "수사기관의 자료 제공 요청 접수": (
                        "요청이 밖에서 들어와 기관은 응하거나 거절하는 위치에 "
                        "선다. 요청 범위를 넘어선 판단을 하지 않는다."
                    ),
                    "국회 자료 제출 요구 접수": (
                        "요구한 쪽이 기관 밖이고 답하는 순간 그 답이 공개될 수 "
                        "있다. 어디까지 답할지가 재량이 아니라 기준에서 갈린다."
                    ),
                    "보안규정 위반 사실 통보": (
                        "이미 벌어진 일이 부른 건이라 문서가 선 자리는 사후 "
                        "수습이다. 위반이 어디까지 미쳤는지가 아직 확정되기 "
                        "전임이 드러나야 한다."
                    ),
                    "관계 법령 개정 시행": (
                        "기준 자체가 바뀐 건이라 이전 판단이 그대로 유효하지 "
                        "않다. 무엇이 새 기준에 걸리는지가 개별 사안이 아니라 "
                        "기준에서 나온다."
                    ),
                }
            ),
        ),
        _NIS_ROLE_SLOT,
    ),
    department_paired_slot="대상자료군",
    subject_cases=(
        SubjectCase(
            subject="비밀 지정·재분류 심사",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="비밀 재분류(안)",
            contents=(
                "대상 자료별 현행 등급과 조정안, 그 등급을 정한 근거 법령 조문",
                "재분류에 따라 달라지는 취급 범위와 시행 시기",
                "이번 재분류에 걸리는 자료 건수와 등급이 그대로인 자료의 규모",
                "내용이 달라지지 않았는데 등급을 다르게 볼 수 있는 근거",
                "확정 전까지 인가 범위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="비밀 지정·재분류 심사",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="비밀 재분류 심사 회의록",
            contents=(
                "등급 조정을 두고 갈린 근거와 참석자별 발언, 인용된 법령 조문",
                "확정된 등급과 보류된 자료, 그 보류 사유",
                "심사에 올라온 자료 건수와 각 자료를 낸 부서",
                "판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="비밀 지정·재분류 심사",
            document_form=DocumentForm.REPORT,
            document_name="비밀 재분류 처리 현황보고",
            contents=(
                "등급별 대상 건수와 처리 실적의 대비표",
                "처리가 늦은 항목의 원인과 그때까지 유지되는 취급 제한",
                "처리를 마친 건과 착수 전인 건의 규모",
                "집계에서 뺀 자료 구분과 뺀 근거 조문",
                "보고 이후에도 인가 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="비밀취급 인가 관리",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="비밀취급 인가 범위 조정(안)",
            contents=(
                "직위별 인가 등급과 조정안, 그 인가의 근거 법령 조문",
                "조정에 따라 늘거나 주는 취급 단위와 시행 시기",
                "조정에 걸리는 직위 수와 그대로 두는 직위의 규모",
                "그 인가 범위로 정한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 인가 범위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="비밀취급 인가 관리",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="인가 조정 승인 품의",
            contents=(
                "조정이 필요한 사유와 승인받을 인가 범위, 근거 법령 조문",
                "조정에 따른 취급 통제 변경 사항",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 직위 수와 이번 조정에서 뺀 직위의 규모",
                "결재가 끝나기 전까지 인가 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="비밀취급 인가 관리",
            document_form=DocumentForm.REPORT,
            document_name="인가 관리 현황보고",
            contents=(
                "기준 시점의 인가 단위별 인원 규모와 등급 분포",
                "인가 요건을 다시 확인해야 하는 단위와 그 사유",
                "직전 기준 시점과 비교한 증감과 그 증감이 생긴 사유",
                "집계에서 뺀 단위와 뺀 근거 조문",
                "보고 이후에도 인가 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="자료 제공 요청 처리",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="제공 가부 법률 검토의견",
            contents=(
                "요청 항목별 제공 가부와 그 판단의 근거 법령 조문",
                "제공하더라도 가려야 하는 부분과 그 처리 방법",
                "요청된 항목 수와 제공할 수 없다고 본 항목의 규모",
                "요청 범위를 넘어선다고 본 항목과 그렇게 본 근거 조문",
                "회신 전까지 요청 기관과 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="자료 제공 요청 처리",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="자료 제공 승인 품의",
            contents=(
                "제공할 범위와 그 범위로 한정한 근거 법령 조문",
                "제공 방식과 제공 후 취급 조건",
                "기안-검토-결재 열을 가진 결재란",
                "요청된 건수와 이번 승인에서 뺀 건의 규모",
                "결재가 끝나기 전까지 요청 기관과 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="자료 제공 요청 처리",
            document_form=DocumentForm.REPLY_NOTICE,
            document_name="제공 범위 회신 통지",
            contents=(
                "회신 상대와 요청 건, 제공하는 항목과 제외하는 항목의 구분",
                "제외한 항목마다 그 근거 법령 조문과 취급 시 준수 사항",
                "요청된 항목 수와 실제 제공하는 항목의 규모",
                "제외 판단이 조문의 어느 문구에 걸리는지",
                "회신 이후에도 상대 기관 안에서만 취급되어야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="기관 간 정보 공유 범위 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="공유 범위 조정(안)",
            contents=(
                "기관별 공유 항목과 등급, 공유를 허용·제한하는 근거 법령 조문",
                "조정에 따라 새로 열거나 닫는 항목과 시행 시기",
                "공유에 걸린 기관 수와 이번 조정에 드는 항목의 규모",
                "그 범위로 정한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 인가 범위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="기관 간 정보 공유 범위 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="공유 범위 협의 회의록",
            contents=(
                "공유 범위를 두고 갈린 근거와 기관별 발언, 인용된 법령 조문",
                "합의된 범위와 미합의로 남은 항목",
                "협의에 올라온 항목 수와 각 안을 낸 기관",
                "판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="기관 간 정보 공유 범위 조정",
            document_form=DocumentForm.REPORT,
            document_name="공유 시행 현황보고",
            contents=(
                "기관별 공유 항목과 실제 제공 실적의 대비표",
                "합의 범위를 벗어난 요청이 있었던 항목과 그 처리",
                "제공을 마친 항목과 보류 중인 항목의 규모",
                "벗어난 요청을 거절한 근거 조문",
                "보고 이후에도 인가 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="보안규정 위반 사항 처리",
            document_form=DocumentForm.INVESTIGATION_REPORT,
            document_name="위반 사실 확인 조사 결과",
            contents=(
                "확인된 위반 사실과 위반된 규정의 조문",
                "취급 범위를 벗어난 자료의 등급과 범위, 확인되지 않은 항목",
                "확인을 마친 항목과 아직 확인하지 못한 항목의 규모",
                "위반이 어디까지 미쳤는지가 아직 확정되지 않았다는 점과 그 이유",
                "조사가 끝나기 전까지 인가 범위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="보안규정 위반 사항 처리",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="시정 요구 발령 품의",
            contents=(
                "요구할 시정 사항과 그 근거 법령 조문",
                "이행 기한과 미이행 시 뒤따르는 조치",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 시정 사항 수와 이번 요구에서 뺀 사항의 규모",
                "발령 전까지 대상 기관과 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="보안규정 위반 사항 처리",
            document_form=DocumentForm.REPORT,
            document_name="시정 이행 현황보고",
            contents=(
                "시정 항목별 요구 사항과 실제 이행 실적의 대비표",
                "이행이 늦은 항목의 원인과 그때까지 유지되는 취급 제한",
                "이행을 마친 항목과 착수 전인 항목의 규모",
                "이행 여부를 확인한 근거와 확인되지 않은 항목",
                "보고 이후에도 인가 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="법령 개정에 따른 비밀관리 기준 정비",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="개정 법령 적용 검토의견",
            contents=(
                "개정된 조문과 그로 인해 달라지는 지정·취급 기준",
                "기존 지정 자료 중 재검토가 필요한 범위와 그 판단 근거",
                "재검토 대상 건수와 기준이 그대로인 자료의 규모",
                "새 기준에 걸리는지가 개별 사안이 아니라 어느 조문에서 갈리는지",
                "확정 전까지 인가 범위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="법령 개정에 따른 비밀관리 기준 정비",
            document_form=DocumentForm.ADMINISTRATIVE_RULE,
            document_name="비밀관리 지침 개정(안)",
            contents=(
                "개정 조항별 신·구 대비와 각 조항이 딛고 선 상위 법령 조문",
                "시행일과 경과 조치, 그때까지 적용되는 기준",
                "개정에 걸리는 조항 수와 그대로 두는 조항의 규모",
                "그 문안으로 정한 근거와 함께 검토했다가 접은 안",
                "시행 전까지 인가 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="법령 개정에 따른 비밀관리 기준 정비",
            document_form=DocumentForm.REPORT,
            document_name="기준 정비 추진 현황보고",
            contents=(
                "정비 대상 항목과 처리 실적의 대비표",
                "정비가 끝나지 않은 항목과 그 사이 적용되는 기준",
                "정비를 마친 항목과 착수 전인 항목의 규모",
                "집계에서 뺀 항목과 뺀 근거 조문",
                "보고 이후에도 인가 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 대통령경호처 역할구성.
_PSS_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 부서 단독 검토",
        "경호안전대책본부 합동 검토",
        "경찰·소방 등 관계기관 협의",
        "행사 주관기관과의 협의",
    ),
    guidance=MappingProxyType(
        {
            "담당 부서 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 상급자 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 부서나 기관 의견을 끌어들이지 않는다."
            ),
            "경호안전대책본부 합동 검토": (
                "본부가 요구한 사항을 먼저 적고, 참여 부서가 올린 현장 의견을 "
                "적은 다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 끝낸다. 현장 "
                "의견은 본부 판단과 다른 내용을 담아야 한다."
            ),
            "경찰·소방 등 관계기관 협의": (
                "협조를 요청한 사유를 먼저 적고, 기관별 입장 표명을 적은 다음, "
                "입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 것으로 "
                "끝낸다. 기관마다 서로 다른 소관 사정이 드러나야 한다."
            ),
            "행사 주관기관과의 협의": (
                "요구한 안전 조치와 그 근거를 먼저 적고, 주관기관이 제기한 행사 "
                "운영상의 어려움을 적은 다음, 요구와 운영 사정이 어긋난 지점을 "
                "짚고, 조정된 것과 물러서지 않은 것으로 끝낸다."
            ),
        }
    ),
)


#: 대통령경호처 / 제2호 ``security_defense``. ``security_defense``의 두 번째
#: 기관이다.
#:
#: **초안은 제3호 ``life_body``였고 그건 틀렸다**(사용자 지적, 2026-08-05).
#: 이 기관의 경호는 일반적인 신체 안전이 아니라 **대통령 등에 대한 경호**이고,
#: 그 문서의 비공개 근거는 공개 시 국가의 중대한 이익을 현저히 해칠 우려(제2호)와
#: 대통령 등의 경호에 관한 법률이 정한 비밀(제1호)에 선다. 사람의 생명·신체를
#: 다룬다는 이유로 제3호에 넣으면 행정안전부의 재난 문서와 같은 라벨을 달게 되고,
#: 정작 이 문서가 지키는 것(국가의 중대한 이익)은 라벨에서 사라진다.
#:
#: 라벨은 제2호 하나로 잡고, 제1호 성격은 **근거 조문을 본문에 요구**하는 것으로
#: 함께 드러나게 한다 — 세부조항은 학습 라벨이라 하나여야 하고, 둘 다 걸리는
#: 문서라는 사실은 본문의 값으로 남는 편이 낫다.
#:
#: **국방부와는 다루는 것이 다르다.** 저쪽은 부대·전력·군사시설이고 이쪽은 경호
#: 대상과 그를 둘러싼 통제 기준이다. 장소축도 부대 대 행사 시설로 갈린다.
#:
#: **사안을 제도 층위로만 세웠다.** 이 기관의 문서는 잘못 세우면 실제 위해에
#: 쓰이는 서식이 된다 — 경호 대상자의 이동 시각과 동선, 배치 인원의 위치,
#: 사람이 비는 구간 같은 값이 그렇다. 그래서 사안 여섯이 전부 지정·기준·협조·
#: 이행 점검처럼 **제도와 절차**에 서고, ``instruction``이 동선·배치·시각을
#: 정면으로 막는다. 행정안전부에서 세운 "무엇이 취약한가" 축을 여기에 그대로
#: 옮기지 않은 이유다 — 저쪽에서는 그 축이 조항을 정확히 맞히지만 이쪽에서는
#: 그것이 곧 위해 표적 목록이 된다.
#:
#: 부서는 ``대통령경호처와 그 소속기관 직제``의 편제다(확인 2026-08-05).
#:
#: 조항이 새는 자리 넷. 경호 대상자와 참석자의 신원·연락처(제6호
#: ``petitioner_pii``) / 시설 자체의 구조 판정(제3호 ``property``) / 재난이 사람에게
#: 닿는 자리(제3호 ``life_body``) / 이미 공지된 행사 안내와 통제 구간 고지(이
#: 세부조항의 ``excludes``).
_SECURITY_DEFENSE_PSS = CTrackTemplate(
    subclause_key=SubclauseKey.SECURITY_DEFENSE,
    agency="대통령경호처",
    persona_context="대통령 등 경호 대상에 대한 경호구역 지정과 경호안전 협조 업무",
    adversaries=(
        Adversary(
            who="경호 대상에게 해를 끼치려는 자",
            position="external",
            what_they_gain=(
                "어디까지가 통제 안이고 무엇을 상정해 그 선을 그었는지가 밖에서 "
                "읽히면, 상정 밖의 일까지 막아 낸다는 전제 위에 서 있던 보호가 "
                "무력화된다."
            ),
        ),
        Adversary(
            who="행사 운영을 맡아 통제 기준을 쥐고 있는 수탁 업체 관계자",
            position="intermediary",
            what_they_gain=(
                "출입과 반입을 가르는 기준이 관리 밖으로 나가면, 그 기준을 아는 "
                "사람이 관리 책임 안에 머문다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="협조 요청을 받아 자료를 함께 보는 다른 기관 관계자",
            position="intermediary",
            what_they_gain=(
                "협조 범위가 정해지기 전에 그 논의가 밖으로 나가면, 안전 조치가 "
                "정해진 범위 안에서만 알려진다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 안전 자료를 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "확정 전 통제 기준과 시행 시점이 인가 밖으로 퍼지면, 기준과 "
                "고지가 정해진 순서로 이뤄진다는 전제가 무력화된다."
            ),
        ),
    ),
    departments=(
        "기획관리실",
        "경호본부",
        "경비안전본부",
        "경호지원단",
        "감사관실",
        "경호안전교육원",
    ),
    instruction=(
        "대통령 등 경호 대상에 대한 경호구역 지정과 출입·반입 통제 기준, 관계기관 "
        "협조에 관한 내부 자료를 쓴다. 이 문서가 다루는 것은 일반적인 안전관리가 "
        "아니라 **경호 대상에 대한 경호**이며, 그 사실이 본문에서 드러나야 한다. "
        "확정 전 통제 기준과 그 근거, 협조 요청 항목과 미이행으로 남은 것, 점검에서 "
        "확인된 이행 수준처럼 미리 알려지면 경호가 딛고 선 전제가 깨져 국가의 "
        "중대한 이익이 현저히 훼손되는 내용을 담되, 그 사안에서 다루는 것만 쓴다. "
        "대통령 등의 경호에 관한 법률 등 그 취급을 정한 근거 조문을 본문에 값으로 "
        "적는다. **경호 대상의 이동 시각과 경로, 인원이 서는 위치, 사람이 비는 "
        "구간과 시간대는 적지 않는다** — 그 자리에는 통제 기준과 그것이 상정한 "
        "범위를 적는다. 이미 공지된 행사 안내와 통제 구간 고지는 문서의 본체가 "
        "아니다."
    ),
    slots=(
        CaseSlot(
            name="대상시설",
            place=True,
            values=(
                "국회의사당",
                "국립서울현충원",
                "인천국제공항",
                "정부세종청사",
                "국립중앙박물관",
                "서울월드컵경기장",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "국가행사 개최 계획 접수",
                "외국 정상 방한 일정 통보",
                "위해 첩보 통보 접수",
                "안전활동 협조 요청 회신 지연",
                "이행 점검 결과 지적사항 접수",
            ),
            guidance=MappingProxyType(
                {
                    "국가행사 개최 계획 접수": (
                        "행사를 정한 쪽이 따로 있어 조건이 이미 주어져 있다. "
                        "바꿀 수 있는 것과 받아들일 수밖에 없는 것이 갈린다."
                    ),
                    "외국 정상 방한 일정 통보": (
                        "상대국 사정이 걸려 있어 우리 판단만으로 정해지지 "
                        "않는다. 확정된 것과 협의 중인 것이 구별되어야 한다."
                    ),
                    "위해 첩보 통보 접수": (
                        "첩보 하나로 시작된 건이라 신빙성이 아직 서지 않았다. "
                        "확인된 것으로 다루는 부분과 가정으로 두는 부분이 갈려야 "
                        "한다."
                    ),
                    "안전활동 협조 요청 회신 지연": (
                        "답이 오지 않아 비어 있는 자리가 있다. 그 공백을 채워 "
                        "적지 말고 비어 있는 채로 판단이 서야 한다."
                    ),
                    "이행 점검 결과 지적사항 접수": (
                        "이미 하기로 한 것이 안 된 자리다. 새로 정할 것이 아니라 "
                        "왜 안 됐는지에서 판단이 시작된다."
                    ),
                }
            ),
        ),
        _PSS_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="경호구역 지정·해제",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="경호구역 지정(안)",
            contents=(
                "지정할 구역의 범위와 지정 기간, 그 범위로 정한 근거",
                "지정에 따라 달라지는 통제 항목과 관계기관 통보 시점",
                "지정 대상 구역 수와 이번 지정에서 뺀 구역의 규모",
                "그 범위로 정한 근거 조문과 함께 검토했다가 접은 안",
                "확정 전까지 관계기관과 외부에 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="경호구역 지정·해제",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="경호구역 지정 승인 품의",
            contents=(
                "지정이 필요한 사유와 승인받을 범위, 그 범위로 한정한 근거",
                "지정 기간과 해제 요건",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 구역 수와 이번 승인에서 뺀 구역의 규모",
                "결재가 끝나기 전까지 대외에 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="경호구역 지정·해제",
            document_form=DocumentForm.REPORT,
            document_name="경호구역 운영 결과보고",
            contents=(
                "당초 지정 범위와 실제 운영 실적의 대비표",
                "범위를 조정해야 했던 항목과 그 원인",
                "지정대로 운영된 구역과 조정이 있었던 구역의 규모",
                "조정 사유가 우리 판단인지 외부 사정인지 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="출입·반입 통제 기준 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="출입·반입 통제 기준 조정(안)",
            contents=(
                "현행 기준과 조정안, 조정이 필요해진 사유",
                "기준별 적용 대상과 예외를 인정하는 범위, 승인 경로",
                "조정에 걸리는 기준 수와 그대로 두는 기준의 규모",
                "그 기준이 상정한 범위와 그 범위를 그렇게 잡은 근거",
                "확정 전까지 관계기관과 외부에 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="출입·반입 통제 기준 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="통제 기준 검토 회의록",
            contents=(
                "기준 수위를 두고 갈린 근거와 참석 기관별 발언",
                "확정된 기준과 재검토로 넘긴 항목",
                "검토에 올라온 기준 수와 각 안을 낸 기관",
                "수위 판단이 갈린 지점이 어느 전제 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="출입·반입 통제 기준 조정",
            document_form=DocumentForm.REPORT,
            document_name="통제 기준 적용 결과보고",
            contents=(
                "기준별 적용 건수와 예외 인정 건수의 대비표",
                "기준대로 적용되지 않은 항목과 그 원인",
                "적용을 마친 기준과 적용이 남은 기준의 규모",
                "예외를 인정한 근거와 그 근거가 기준의 어느 항에 걸리는지",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="안전활동 협조 요청 처리",
            document_form=DocumentForm.REPLY_NOTICE,
            document_name="협조 요청 회신 통지",
            contents=(
                "회신 상대와 요청 건, 협조할 항목과 협조하지 않는 항목의 구분",
                "제외한 항목마다 그 사유와 대신 요구하는 조치",
                "요청된 항목 수와 실제 협조하는 항목의 규모",
                "제외 판단의 근거 조문과 상대가 지켜야 할 취급 조건",
                "회신 이후에도 상대 기관 안에서만 취급되어야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="안전활동 협조 요청 처리",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="협조 사항 협의 회의록",
            contents=(
                "협조 범위를 두고 갈린 근거와 기관별 발언",
                "합의된 항목과 미합의로 남은 사항",
                "협의에 올라온 항목 수와 각 안을 낸 기관",
                "판단이 갈린 지점이 어느 소관 해석 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="안전활동 협조 요청 처리",
            document_form=DocumentForm.REPORT,
            document_name="협조 이행 현황보고",
            contents=(
                "기관별 협조 항목과 실제 이행 실적의 대비표",
                "이행되지 않은 항목과 그로 인해 남는 부담",
                "이행을 마친 항목과 회신이 오지 않은 항목의 규모",
                "회신이 오지 않아 비어 있는 자리를 채우지 않고 그대로 둔 항목",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="위해 신고·통보 처리",
            document_form=DocumentForm.INVESTIGATION_REPORT,
            document_name="신고 내용 확인 결과",
            contents=(
                "신고·통보의 접수 경위와 확인된 사실의 범위",
                "확인되지 않은 항목과 그 이유, 추가 확인이 필요한 것",
                "접수된 건수와 확인을 마친 건의 규모",
                "확인된 것으로 다루는 부분과 가정으로 두는 부분의 구분",
                "확인이 끝나기 전까지 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="위해 신고·통보 처리",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="대응 수위 심의 회의록",
            contents=(
                "대응 수위를 두고 갈린 근거와 참석자별 발언",
                "결정된 조치와 보류된 조치, 그 보류 사유",
                "심의에 올라온 판단 자료와 그 자료를 낸 부서",
                "수위 판단이 갈린 지점이 어느 신빙성 평가 차이에서 왔는지",
                "결정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="위해 신고·통보 처리",
            document_form=DocumentForm.REPORT,
            document_name="신고 처리 현황보고",
            contents=(
                "접수 건수와 처리 구분별 실적의 대비표",
                "처리가 늦은 건의 원인과 그때까지 유지되는 조치",
                "처리를 마친 건과 확인 중인 건의 규모",
                "집계에서 뺀 건과 뺀 이유",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="경호안전대책본부 운영",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="대책본부 구성·운영 계획(안)",
            contents=(
                "참여 기관과 기관별 분담 항목, 그렇게 나눈 사유",
                "운영 기간과 단계별 보고·전파 체계",
                "참여 기관 수와 각 기관이 맡는 항목의 규모",
                "그 분담으로 정한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 참여 기관과 외부에 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="경호안전대책본부 운영",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="대책본부 운영 실태 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "분담 항목별 지적사항과 시정 요구 사항, 이행 기한",
                "이번 점검이 다루지 않은 항목과 범위를 좁힌 이유",
                "지적사항이 이번에 처음 나온 것인지 반복된 것인지 구분",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="경호안전대책본부 운영",
            document_form=DocumentForm.REPORT,
            document_name="대책본부 운영 결과보고",
            contents=(
                "당초 분담과 실제 이행 실적의 기관별 대비표",
                "분담대로 되지 않은 항목의 원인과 다음 운영에 반영할 사항",
                "이행을 마친 항목과 미이행으로 남은 항목의 규모",
                "미이행 사유가 우리 손 안의 것인지 밖의 것인지 구분",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="경호안전 통제 이행 점검",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="통제 이행 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "통제 항목별 이행 여부와 시정 요구 사항, 이행 기한",
                "점검 표본의 규모와 전체 대비 비율, 표본을 그렇게 잡은 이유",
                "이행 여부를 판정한 기준과 판정이 서지 않은 항목",
                "점검 결과 중 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="경호안전 통제 이행 점검",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="시정 요구 발령 품의",
            contents=(
                "요구할 시정 사항과 그 범위로 한정한 근거",
                "이행 기한과 미이행 시 뒤따르는 조치",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 시정 사항 수와 이번 요구에서 뺀 사항의 규모",
                "발령 전까지 대상 기관과 외부에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="경호안전 통제 이행 점검",
            document_form=DocumentForm.REPORT,
            document_name="시정 이행 현황보고",
            contents=(
                "시정 항목별 요구 사항과 실제 이행 실적의 대비표",
                "이행이 늦은 항목의 원인과 그때까지 유지되는 통제",
                "이행을 마친 항목과 착수 전인 항목의 규모",
                "이행 여부를 확인한 방법과 확인되지 않은 항목",
                "보고 이후에도 대외에 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 대통령비서실·국가안보실 공용 역할구성. 둘 다 실(室) 안에서 비서관실 단위로
#: 움직이고 바깥과는 부처를 상대하므로 층위가 같다.
_PRESIDENTIAL_ROLE_SLOT = CaseSlot(
    name="역할구성",
    phrasing="{value}로 진행된 건이다.",
    values=(
        "담당 비서관실 단독 검토",
        "실 내 관련 비서관실 합동 검토",
        "관계부처와의 협의",
        "외부 위원이 참여하는 심의",
    ),
    guidance=MappingProxyType(
        {
            "담당 비서관실 단독 검토": (
                "작성 부서 안에서 오간 것만 적는다. 담당자 검토 의견을 먼저 "
                "적고, 비서관 지시사항을 적은 다음, 지시를 반영한 결과로 끝낸다. "
                "다른 비서관실이나 부처 의견을 끌어들이지 않는다."
            ),
            "실 내 관련 비서관실 합동 검토": (
                "주관 비서관실이 요구한 사항을 먼저 적고, 관련 비서관실이 낸 "
                "다른 판단을 적은 다음, 둘이 어긋난 지점을 짚고, 조정된 결과로 "
                "끝낸다."
            ),
            "관계부처와의 협의": (
                "협의를 요청한 사유를 먼저 적고, 부처별 입장 표명을 적은 다음, "
                "입장이 갈린 쟁점을 짚고, 합의된 것과 미합의로 남은 것으로 "
                "끝낸다. 부처마다 서로 다른 소관 사정이 드러나야 한다."
            ),
            "외부 위원이 참여하는 심의": (
                "안건 상정과 담당 부서의 설명을 먼저 적고, 위원별 질의와 그에 "
                "대한 답변을 적은 다음, 쟁점별 견해와 근거를 적고, 의결 또는 "
                "보류 결정과 그 사유로 끝낸다."
            ),
        }
    ),
)


#: 대통령비서실 / 제1호 ``legal_secret``. ``legal_secret``의 두 번째 기관이다.
#:
#: **국가정보원판과 근거 법령으로 갈린다.** 이 세부조항은 "근거가 문서에 드러나는
#: 경우"가 판정 기준이므로, 같은 세부조항의 두 기관은 **어느 법률이 그것을 비밀로
#: 정하는가**로 나뉘어야 한다. 저쪽은 보안업무규정(비밀 지정·인가·제공)이고
#: 이쪽은 대통령기록물 관리에 관한 법률과 공공기록물 관리에 관한 법률(지정기록물·
#: 보호기간·비공개 재분류)이다. 사안도 그 축을 따라간다.
#:
#: **부서축은 확정된 것이 아니다.** 대통령비서실 직제는 정권마다 바뀌고 공개 자료로
#: 현행 비서관실 목록을 확정하지 못했다(2026-08-05). ``기록관리비서관실``은 이번
#: 정부에서 신설이 확인된 것이고 나머지는 여러 정부에 걸쳐 유지돼 온 이름이다.
#: 통일부에서 없어진 부서명을 적었던 일이 있으므로, 현행 직제가 확인되면 이 축부터
#: 대조해야 한다.
#:
#: 조항이 새는 자리 넷. 기록물 **내용**의 정책 판단(제5호 ``decision_review``) /
#: 열람 요구인의 신원(제6호 ``petitioner_pii``) / 안보 회의 자료의 취급(같은
#: 제1호의 국가안보실) / 근거 조문 없이 '대외비' 표기만 있는 문서(이 세부조항의
#: ``excludes``).
_LEGAL_SECRET_PRESIDENTIAL_OFFICE = CTrackTemplate(
    subclause_key=SubclauseKey.LEGAL_SECRET,
    agency="대통령비서실",
    persona_context="대통령기록물 관리와 비공개 기록물 지정 업무",
    adversaries=(
        Adversary(
            who="보호기간 안의 기록을 미리 보려는 외부 요구인",
            position="external",
            what_they_gain=(
                "무엇이 어느 근거로 지정돼 언제까지 닫히는지가 지정 전에 알려지면, "
                "지정이 정해진 절차를 거쳐 효력을 갖는다는 전제가 무력화된다."
            ),
        ),
        Adversary(
            who="이관 실무를 함께 맡아 목록을 먼저 보는 위탁 인력",
            position="intermediary",
            what_they_gain=(
                "확정 전 이관 목록이 관리 밖으로 나가면, 목록이 확정된 뒤에만 "
                "그 존재가 알려진다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="기록의 존부 자체를 확인하려는 이해관계인",
            position="external",
            what_they_gain=(
                "어떤 기록이 있는지가 재분류 전에 드러나면, 존부가 법이 정한 "
                "절차로만 확인된다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 기록을 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "지정 전 판단과 보호기간 산정이 인가 밖으로 퍼지면, 취급이 인가 "
                "범위 안에 머문다는 전제가 무력화된다."
            ),
        ),
    ),
    departments=(
        "총무비서관실",
        "기록관리비서관실",
        "법률비서관실",
        "공직기강비서관실",
        "인사비서관실",
        "국정상황실",
    ),
    instruction=(
        "대통령기록물과 비공개 기록물의 지정·이관·재분류에 관한 내부 자료를 쓴다. "
        "**어느 법률의 어느 조문이 그것을 비밀 또는 비공개로 정하고 있는지를 본문에 "
        "값으로 적는다** — 근거 조문 없이 '대외비'·'비밀유지' 표기만 있으면 이 "
        "문서는 성립하지 않는다. 지정 대상과 그 사유, 보호기간과 산정 근거, 이관 "
        "목록과 확정 시점, 열람 요구에 대한 판단처럼 미리 알려지면 지정이 정해진 "
        "절차를 거쳐 효력을 갖는다는 전제가 깨지는 내용을 담되, 그 사안에서 다루는 "
        "것만 쓴다. 기록에 담긴 정책 판단의 내용 자체와 열람 요구인의 신원은 이 "
        "문서가 다루는 것이 아니다."
    ),
    slots=(
        CaseSlot(
            name="대상기록군",
            place=True,
            values=(
                "정상외교 기록",
                "인사 검증 기록",
                "국정과제 점검 기록",
                "재난·위기 대응 기록",
                "법률안 검토 기록",
                "민원·청원 처리 기록",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "기록물 생산현황 통보 시기 도래",
                "이관 대상 목록 제출 요구 접수",
                "비공개 기록물 재분류 주기 도래",
                "열람 요구서 접수",
                "관계 법령 개정 시행",
            ),
            guidance=MappingProxyType(
                {
                    "기록물 생산현황 통보 시기 도래": (
                        "정해진 시기가 부른 건이라 사정 변화가 없다. 집계된 "
                        "수치가 곧 판단의 전부다."
                    ),
                    "이관 대상 목록 제출 요구 접수": (
                        "요구가 밖에서 들어와 기관은 응하는 위치에 선다. 목록에 "
                        "무엇을 넣고 뺄지가 재량이 아니라 기준으로 갈린다."
                    ),
                    "비공개 기록물 재분류 주기 도래": (
                        "시간이 지났다는 사실 하나가 판단을 불렀다. 내용이 "
                        "달라지지 않았는데 왜 다르게 볼 수 있는지가 서야 한다."
                    ),
                    "열람 요구서 접수": (
                        "요구한 사람이 밖에 있고 그 사람이 무엇을 아는지가 정해져 "
                        "있다. 요구 범위 밖의 사정을 답에 끌어오지 않는다."
                    ),
                    "관계 법령 개정 시행": (
                        "기준이 바뀐 건이라 이전 처리가 그대로 유효하지 않다. "
                        "되짚어야 할 범위가 어디까지인지가 개별 판단이 아니라 "
                        "기준에서 나온다."
                    ),
                }
            ),
        ),
        _PRESIDENTIAL_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="대통령지정기록물 지정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="지정기록물 지정(안)",
            contents=(
                "지정 대상 기록군과 지정 사유, 그 지정의 근거 법령 조문",
                "지정에 따라 달라지는 취급 범위와 시행 시기",
                "지정 대상 건수와 이번 지정에서 뺀 기록군의 규모",
                "지정 여부가 개별 판단이 아니라 어느 조문 요건에서 갈리는지",
                "확정 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="대통령지정기록물 지정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="지정 심의 회의록",
            contents=(
                "지정 여부를 두고 갈린 근거와 참석자별 발언, 인용된 법령 조문",
                "확정된 지정과 보류된 기록군, 그 보류 사유",
                "심의에 올라온 기록군 수와 각 자료를 낸 비서관실",
                "판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="대통령지정기록물 지정",
            document_form=DocumentForm.REPORT,
            document_name="지정 처리 현황보고",
            contents=(
                "기록군별 지정 대상 건수와 처리 실적의 대비표",
                "처리가 늦은 기록군의 원인과 그때까지 유지되는 취급 제한",
                "처리를 마친 건과 착수 전인 건의 규모",
                "집계에서 뺀 기록군과 뺀 근거 조문",
                "보고 이후에도 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="보호기간 설정",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="보호기간 설정 법률 검토의견",
            contents=(
                "기록군별 보호기간과 그 산정의 근거 법령 조문",
                "기간을 다투게 될 지점과 그에 대한 검토 결론",
                "검토한 기록군 수와 판단을 유보한 기록군의 규모",
                "산정 기준이 조문의 어느 문구에 걸리는지",
                "확정 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="보호기간 설정",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="보호기간 설정 승인 품의",
            contents=(
                "승인받을 보호기간과 그 기간으로 한정한 근거 법령 조문",
                "기간 중 예외적으로 열람이 가능한 범위",
                "기안-검토-결재 열을 가진 결재란",
                "검토에 오른 기록군 수와 이번 승인에서 뺀 기록군의 규모",
                "결재가 끝나기 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="보호기간 설정",
            document_form=DocumentForm.REPORT,
            document_name="보호기간 설정 현황보고",
            contents=(
                "기록군별 설정 기간의 분포와 당초 계획 대비 실적",
                "설정이 끝나지 않은 기록군과 그 사이 적용되는 기준",
                "설정을 마친 기록군과 착수 전인 기록군의 규모",
                "집계에서 뺀 기록군과 뺀 근거 조문",
                "보고 이후에도 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="이관 목록 확정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="이관 목록 확정(안)",
            contents=(
                "이관 대상 기록군과 제외 대상의 구분, 그 구분의 근거 법령 조문",
                "이관 시기와 확정 전까지 유지되는 취급 범위",
                "이관 대상 건수와 제외로 두는 건의 규모",
                "구분이 재량이 아니라 어느 조문 요건에서 갈리는지",
                "확정 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="이관 목록 확정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="이관 목록 검토 회의록",
            contents=(
                "제외 대상 판단을 두고 갈린 근거와 참석자별 발언",
                "확정된 목록과 재검토로 넘긴 기록군",
                "검토에 올라온 기록군 수와 각 안을 낸 비서관실",
                "판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="이관 목록 확정",
            document_form=DocumentForm.REPORT,
            document_name="이관 추진 현황보고",
            contents=(
                "기록군별 이관 목표와 실제 처리 실적의 대비표",
                "이관이 늦은 기록군의 원인과 보완 사항",
                "이관을 마친 건과 착수 전인 건의 규모",
                "지연 사유가 우리 손 안의 것인지 밖의 것인지 구분",
                "보고 이후에도 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="비공개 기록물 재분류",
            document_form=DocumentForm.REPORT,
            document_name="재분류 심사 결과보고",
            contents=(
                "기록군별 현행 구분과 재분류 결과, 그 판단의 근거 법령 조문",
                "공개로 전환하지 않은 기록군과 그 사유",
                "심사한 기록군 수와 구분이 그대로인 기록군의 규모",
                "내용이 달라지지 않았는데 다르게 볼 수 있는 근거",
                "보고 이후에도 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="비공개 기록물 재분류",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="재분류 기준 법률 검토의견",
            contents=(
                "적용할 비공개 사유와 그 근거 법령 조문",
                "사유가 소멸했다고 볼 여지가 있는 항목과 검토 결론",
                "검토한 항목 수와 판단을 유보한 항목의 규모",
                "소멸 여부가 조문의 어느 문구에서 갈리는지",
                "확정 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="비공개 기록물 재분류",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="재분류 심의 회의록",
            contents=(
                "공개 전환 여부를 두고 갈린 근거와 참석자별 발언",
                "확정된 구분과 보류된 기록군, 그 보류 사유",
                "심의에 올라온 기록군 수와 각 검토 자료를 낸 비서관실",
                "판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="열람 요구 처리",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="열람 가부 법률 검토의견",
            contents=(
                "요구 항목별 열람 가부와 그 판단의 근거 법령 조문",
                "열람을 허용하더라도 가려야 하는 부분과 그 처리 방법",
                "요구된 항목 수와 허용할 수 없다고 본 항목의 규모",
                "요구 범위를 넘어선다고 본 항목과 그렇게 본 근거 조문",
                "회신 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="열람 요구 처리",
            document_form=DocumentForm.REPLY_NOTICE,
            document_name="열람 범위 회신 통지",
            contents=(
                "회신 상대와 요구 건, 허용하는 항목과 제외하는 항목의 구분",
                "제외한 항목마다 그 근거 법령 조문과 불복 절차 안내",
                "요구된 항목 수와 실제 허용하는 항목의 규모",
                "제외 판단이 조문의 어느 문구에 걸리는지",
                "회신 이후에도 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="열람 요구 처리",
            document_form=DocumentForm.REPORT,
            document_name="열람 요구 처리 현황보고",
            contents=(
                "접수 건수와 처리 구분별 실적의 대비표",
                "처리가 늦은 건의 원인과 그때까지 유지되는 제한",
                "처리를 마친 건과 검토 중인 건의 규모",
                "집계에서 뺀 건과 뺀 근거 조문",
                "보고 이후에도 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="기록물 관리 기준 정비",
            document_form=DocumentForm.ADMINISTRATIVE_RULE,
            document_name="기록물 관리 지침 개정(안)",
            contents=(
                "개정 조항별 신·구 대비와 각 조항이 딛고 선 상위 법령 조문",
                "시행일과 경과 조치, 그때까지 적용되는 기준",
                "개정에 걸리는 조항 수와 그대로 두는 조항의 규모",
                "그 문안으로 정한 근거와 함께 검토했다가 접은 안",
                "시행 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="기록물 관리 기준 정비",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="개정 법령 적용 검토의견",
            contents=(
                "개정된 조문과 그로 인해 달라지는 지정·보호기간 기준",
                "기존 지정 기록군 중 재검토가 필요한 범위와 그 판단 근거",
                "재검토 대상 건수와 기준이 그대로인 기록군의 규모",
                "되짚어야 할 범위가 개별 판단이 아니라 어느 조문에서 갈리는지",
                "확정 전까지 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="기록물 관리 기준 정비",
            document_form=DocumentForm.REPORT,
            document_name="기준 정비 추진 현황보고",
            contents=(
                "정비 대상 항목과 처리 실적의 대비표",
                "정비가 끝나지 않은 항목과 그 사이 적용되는 기준",
                "정비를 마친 항목과 착수 전인 항목의 규모",
                "집계에서 뺀 항목과 뺀 근거 조문",
                "보고 이후에도 취급 범위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
    ),
)


#: 국가안보실 / 제1호 ``legal_secret``. ``legal_secret``의 세 번째 기관이다.
#:
#: **셋을 근거 법령으로 갈랐다.** 국가정보원은 보안업무규정(지정·인가·제공),
#: 대통령비서실은 대통령기록물법·공공기록물법(지정기록물·보호기간), 여기는
#: 국가안전보장회의법과 그 운영에 따르는 회의 자료의 취급이다. 같은 세부조항에
#: 기관을 셋 두는 것이 성립하려면 이 축이 갈려 있어야 한다 — 판정 기준 자체가
#: "근거가 문서에 드러나는 경우"이기 때문이다.
#:
#: **제2호와도 갈라야 한다.** 안보실 문서는 내용이 안보라 ``security_defense``와
#: 만나기 쉽다. 그래서 사안이 **회의체 운영과 자료 취급**에만 서고 전력·부대·
#: 대비태세는 값으로 들어오지 않는다. ``instruction``이 그 선을 정면으로 긋는다.
_LEGAL_SECRET_NSO = CTrackTemplate(
    subclause_key=SubclauseKey.LEGAL_SECRET,
    agency="국가안보실",
    persona_context="국가안전보장회의 운영과 안보 자료 취급 관리 업무",
    adversaries=(
        Adversary(
            who="회의에서 무엇이 다뤄졌는지를 알아내려는 국외 정보수집 조직",
            position="external",
            what_they_gain=(
                "어떤 안건이 언제 상정돼 어느 등급으로 묶였는지가 알려지면, "
                "우리가 무엇을 논의했는지 밖에서 알 수 없다는 전제가 성립하지 "
                "않게 된다."
            ),
        ),
        Adversary(
            who="자료를 제출하고 회신을 기다리는 관계부처 관계자",
            position="intermediary",
            what_they_gain=(
                "취급 범위가 정해지기 전에 그 논의가 밖으로 나가면, 제출 자료가 "
                "정해진 범위 안에서만 돈다는 전제가 성립하지 않게 된다."
            ),
        ),
        Adversary(
            who="회의 결과를 확정 전에 재료로 삼는 외부 관계자",
            position="external",
            what_they_gain=(
                "공표 범위가 정해지기 전에 논의가 알려지면, 결과가 정해진 범위로만 "
                "공표된다는 전제가 무력화된다."
            ),
        ),
        Adversary(
            who="취급 범위를 넘어 회의 자료를 열람한 내부 관계자",
            position="internal",
            what_they_gain=(
                "지정 전 등급과 배포 단위가 인가 밖으로 퍼지면, 취급이 인가 범위 "
                "안에 머문다는 전제가 무력화된다."
            ),
        ),
    ),
    departments=(
        "안보전략비서관실",
        "외교정책비서관실",
        "통일정책비서관실",
        "국방비서관실",
        "사이버안보비서관실",
        "경제안보비서관실",
        "국가위기관리센터",
    ),
    instruction=(
        "국가안전보장회의 운영과 그 회의 자료의 취급에 관한 내부 자료를 쓴다. "
        "**어느 법률의 어느 조문이 그것을 비밀 또는 비공개로 정하고 있는지를 본문에 "
        "값으로 적는다** — 근거 조문 없이 표기만 있으면 이 문서는 성립하지 않는다. "
        "안건 자료의 등급 지정과 배포 단위, 부처 제출 자료의 취급 범위, 자료 제공 "
        "요구에 대한 판단, 공표 범위처럼 미리 알려지면 자료가 정해진 범위 안에서만 "
        "돈다는 전제가 깨지는 내용을 담되, 그 사안에서 다루는 것만 쓴다. **논의된 "
        "안보 정책의 내용 자체와 전력·부대·대비태세는 이 문서가 다루는 것이 "
        "아니다** — 그 자리에는 그 자료가 어느 등급으로 어디까지 도는지를 적는다."
    ),
    slots=(
        CaseSlot(
            name="대상자료군",
            place=True,
            values=(
                "상임위원회 안건 자료",
                "부처 제출 검토자료",
                "위기관리 상황 보고자료",
                "정상외교 후속조치 자료",
                "합동 점검 결과자료",
            ),
        ),
        CaseSlot(
            name="촉발계기",
            phrasing="{value}에서 비롯된 건이다.",
            values=(
                "회의 개최 일정 확정",
                "부처 자료 제출 마감 도래",
                "국회 자료 제출 요구 접수",
                "비밀 재분류 주기 도래",
                "취급 실태 점검 결과 접수",
            ),
            guidance=MappingProxyType(
                {
                    "회의 개최 일정 확정": (
                        "날짜가 먼저 정해져 준비 기간이 고정돼 있다. 그 안에 "
                        "못 채우는 것이 함께 서야 한다."
                    ),
                    "부처 자료 제출 마감 도래": (
                        "자료를 내는 쪽이 밖에 있어 무엇이 들어올지 정해지지 "
                        "않았다. 들어온 것과 안 들어온 것이 구별되어야 한다."
                    ),
                    "국회 자료 제출 요구 접수": (
                        "요구한 쪽이 기관 밖이고 답이 그대로 공개될 수 있다. "
                        "어디까지 답할지가 재량이 아니라 기준에서 갈린다."
                    ),
                    "비밀 재분류 주기 도래": (
                        "시기가 부른 건이라 사정이 달라지지 않았다. 같은 내용을 "
                        "왜 지금 다르게 볼 수 있는지가 판단의 전부다."
                    ),
                    "취급 실태 점검 결과 접수": (
                        "점검이 본 범위 안에서만 말한다. 점검하지 않은 곳의 "
                        "상태를 추정해 적지 않는다."
                    ),
                }
            ),
        ),
        _PRESIDENTIAL_ROLE_SLOT,
    ),
    subject_cases=(
        SubjectCase(
            subject="회의 안건 자료 등급 지정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="안건 자료 등급 지정(안)",
            contents=(
                "자료군별 지정 등급과 그 지정의 근거 법령 조문",
                "등급별 배포 단위와 회수 시점",
                "지정 대상 자료군 수와 등급이 그대로인 자료군의 규모",
                "등급이 개별 판단이 아니라 어느 조문 요건에서 갈리는지",
                "확정 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="회의 안건 자료 등급 지정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="등급 지정 심의 회의록",
            contents=(
                "등급 수위를 두고 갈린 근거와 참석자별 발언, 인용된 법령 조문",
                "확정된 등급과 보류된 자료군, 그 보류 사유",
                "심의에 올라온 자료군 수와 각 자료를 낸 부서",
                "판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="회의 안건 자료 등급 지정",
            document_form=DocumentForm.REPORT,
            document_name="등급 지정 처리 현황보고",
            contents=(
                "자료군별 지정 건수와 등급 분포의 대비표",
                "지정이 늦은 자료군과 그때까지 적용되는 취급 기준",
                "지정을 마친 건과 착수 전인 건의 규모",
                "집계에서 뺀 자료군과 뺀 근거 조문",
                "보고 이후에도 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="회의록 작성·취급 관리",
            document_form=DocumentForm.ADMINISTRATIVE_RULE,
            document_name="회의록 취급 지침 개정(안)",
            contents=(
                "개정 조항별 신·구 대비와 각 조항이 딛고 선 상위 법령 조문",
                "시행일과 경과 조치, 그때까지 적용되는 기준",
                "개정에 걸리는 조항 수와 그대로 두는 조항의 규모",
                "그 문안으로 정한 근거와 함께 검토했다가 접은 안",
                "시행 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="회의록 작성·취급 관리",
            document_form=DocumentForm.INSPECTION_REPORT,
            document_name="회의록 취급 실태 점검 결과",
            contents=(
                "점검 일시·점검대상·점검자",
                "취급 항목별 지적사항과 시정 요구 사항, 이행 기한",
                "점검 표본의 규모와 전체 대비 비율, 표본을 그렇게 잡은 이유",
                "지적사항이 이번에 처음 나온 것인지 반복된 것인지 구분",
                "점검 결과 중 배포 단위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="회의록 작성·취급 관리",
            document_form=DocumentForm.REPORT,
            document_name="회의록 관리 현황보고",
            contents=(
                "회차별 작성·보관 실적과 당초 기준 대비 이행률",
                "기준대로 처리되지 않은 항목과 그 원인",
                "처리를 마친 회차와 남은 회차의 규모",
                "집계에서 뺀 회차와 뺀 근거 조문",
                "보고 이후에도 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="부처 제출 자료 취급 범위 조정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="취급 범위 조정(안)",
            contents=(
                "부처별 제출 자료군과 허용·제한하는 취급 범위, 그 근거 법령 조문",
                "조정에 따라 새로 열거나 닫는 항목과 시행 시기",
                "조정에 걸리는 부처 수와 항목의 규모",
                "그 범위로 정한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="부처 제출 자료 취급 범위 조정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="취급 범위 협의 회의록",
            contents=(
                "범위를 두고 갈린 근거와 부처별 발언, 인용된 법령 조문",
                "합의된 범위와 미합의로 남은 항목",
                "협의에 올라온 항목 수와 각 안을 낸 부처",
                "판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "합의 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="부처 제출 자료 취급 범위 조정",
            document_form=DocumentForm.REPORT,
            document_name="취급 범위 적용 현황보고",
            contents=(
                "부처별 적용 실적과 당초 합의 범위의 대비표",
                "범위를 벗어난 요청이 있었던 항목과 그 처리",
                "적용을 마친 항목과 협의 중인 항목의 규모",
                "벗어난 요청을 거절한 근거 조문",
                "보고 이후에도 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="자료 제공 요구 처리",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="제공 가부 법률 검토의견",
            contents=(
                "요구 항목별 제공 가부와 그 판단의 근거 법령 조문",
                "제공하더라도 가려야 하는 부분과 그 처리 방법",
                "요구된 항목 수와 제공할 수 없다고 본 항목의 규모",
                "요구 범위를 넘어선다고 본 항목과 그렇게 본 근거 조문",
                "회신 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="자료 제공 요구 처리",
            document_form=DocumentForm.APPROVAL_REQUEST,
            document_name="자료 제공 승인 품의",
            contents=(
                "제공할 범위와 그 범위로 한정한 근거 법령 조문",
                "제공 방식과 제공 후 취급 조건",
                "기안-검토-결재 열을 가진 결재란",
                "요구된 건수와 이번 승인에서 뺀 건의 규모",
                "결재가 끝나기 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="자료 제공 요구 처리",
            document_form=DocumentForm.REPLY_NOTICE,
            document_name="제공 범위 회신 통지",
            contents=(
                "회신 상대와 요구 건, 제공하는 항목과 제외하는 항목의 구분",
                "제외한 항목마다 그 근거 법령 조문과 취급 시 준수 사항",
                "요구된 항목 수와 실제 제공하는 항목의 규모",
                "제외 판단이 조문의 어느 문구에 걸리는지",
                "회신 이후에도 상대 기관 안에서만 취급되어야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="위기관리 자료 비공개 범위 검토",
            document_form=DocumentForm.LEGAL_REVIEW,
            document_name="비공개 범위 법률 검토의견",
            contents=(
                "자료 항목별 비공개 사유와 그 근거 법령 조문",
                "사유가 소멸했다고 볼 여지가 있는 항목과 검토 결론",
                "검토한 항목 수와 판단을 유보한 항목의 규모",
                "소멸 여부가 조문의 어느 문구에서 갈리는지",
                "확정 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="위기관리 자료 비공개 범위 검토",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="비공개 범위 조정(안)",
            contents=(
                "현행 구분과 조정안, 조정이 필요해진 사유와 근거 법령 조문",
                "조정에 따라 달라지는 배포 단위와 시행 시기",
                "조정에 걸리는 항목 수와 그대로 두는 항목의 규모",
                "그 구분으로 정한 근거와 함께 검토했다가 접은 안",
                "확정 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그 근거 조문",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="위기관리 자료 비공개 범위 검토",
            document_form=DocumentForm.REPORT,
            document_name="비공개 범위 정비 현황보고",
            contents=(
                "자료군별 정비 대상과 처리 실적의 대비표",
                "정비가 끝나지 않은 항목과 그 사이 적용되는 기준",
                "정비를 마친 항목과 착수 전인 항목의 규모",
                "집계에서 뺀 자료군과 뺀 근거 조문",
                "보고 이후에도 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
        ),
        SubjectCase(
            subject="회의 결과 공표 범위 결정",
            document_form=DocumentForm.PLAN_DRAFT,
            document_name="공표 범위 결정(안)",
            contents=(
                "공표할 항목과 공표하지 않을 항목의 구분과 그 근거 법령 조문",
                "공표 시점과 그 시점까지 취급을 제한하는 범위",
                "검토한 항목 수와 공표하지 않기로 한 항목의 규모",
                "구분이 재량이 아니라 어느 조문 요건에서 갈리는지",
                "공표 전까지 배포 단위 밖으로 나가지 않아야 하는 항목과 그것이 알려지면 무엇이 성립하지 않는지",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="회의 결과 공표 범위 결정",
            document_form=DocumentForm.MEETING_MINUTES,
            document_name="공표 범위 협의 회의록",
            contents=(
                "공표 수위를 두고 갈린 근거와 참석자별 발언",
                "확정된 범위와 보류된 항목, 그 보류 사유",
                "협의에 올라온 항목 수와 각 안을 낸 부서",
                "수위 판단이 갈린 지점이 어느 조문 해석 차이에서 왔는지",
                "확정 전까지 참석자 밖으로 나가지 않아야 하는 발언과 그 사유",
            ),
            allowed_stages=_PRE_DECISION_STAGES,
        ),
        SubjectCase(
            subject="회의 결과 공표 범위 결정",
            document_form=DocumentForm.REPORT,
            document_name="공표 시행 결과보고",
            contents=(
                "당초 정한 공표 범위와 실제 공표 내용의 대비표",
                "범위를 벗어난 항목이 나온 경위와 그 영향",
                "공표한 항목 수와 끝내 공표하지 않은 항목의 규모",
                "벗어남이 어느 단계에서 생겼는지와 그 경로",
                "보고 이후에도 배포 단위 밖으로 나가지 않아야 하는 항목과 그 사유",
            ),
            allowed_stages=_POST_ACTION_STAGES,
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
        for template in (
            _CORRECTION_SECURITY_MOJ,
            _SECURITY_DEFENSE_MND,
            _UNIFICATION_DIPLOMACY_MOFA,
            _UNIFICATION_DIPLOMACY_MOU,
            _LIFE_BODY_MOIS,
            _PROPERTY_MOLIT,
            _TRIAL_INVESTIGATION_SPO,
            _PROSECUTION_CIO,
            _LEGAL_SECRET_NIS,
            _SECURITY_DEFENSE_PSS,
            _LEGAL_SECRET_PRESIDENTIAL_OFFICE,
            _LEGAL_SECRET_NSO,
        )
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

    total = template.case_count
    if total > MAX_SHUFFLED_CASE_COUNT:
        raise ValueError(
            f"case_count={total} exceeds {MAX_SHUFFLED_CASE_COUNT}; "
            "전개 방식을 다시 정해야 한다"
        )

    order = list(range(total))
    random.Random(seed).shuffle(order)

    for i in range(count):
        yield case_frame(template, order[i % total])


def select_frames_per_document_form(
    template: CTrackTemplate,
    per_form: int,
    *,
    seed: int = 0,
) -> tuple[CaseFrame, ...]:
    """문서형식마다 ``per_form``건씩 골라 온다.

    **왜 여기 있는가.** 형식별 할당량은 배치 스크립트의 사정처럼 보이지만,
    "문서형식이 무엇이고 한 템플릿이 어느 형식을 쓰는가"는 이 모듈의 어휘다
    (``CTrackTemplate.document_forms``). 스크립트가 프레임을 받아 ``document_form``
    으로 세기 시작하면 전개 규칙이 두 곳에 갈라져 서고, 그중 한쪽만 고쳐지는
    날이 온다.

    **왜 앞에서부터 세지 않는가.** ``expand_cases``의 앞 N건을 그냥 쓰면 형식
    분포가 ``subject_cases`` 구성을 그대로 물려받는다 — 법무부 템플릿은 18개
    쌍 중 6개가 ``plan_draft``이고 ``response_plan``은 1개뿐이라, 앞 60건에서
    계획안이 대응계획서보다 6배 나온다. 여기서는 셔플 순서를 걸어가며 **할당량이
    찬 형식을 건너뛴다.** 순서 자체는 그대로이므로 ``expand_cases``가 보장하는
    두 가지(중복 0, 나머지 축 균등)를 잃지 않는다.

    **모자라면 모자란 대로 돌려준다.** 형식 하나의 조합 수가 ``per_form``보다
    적을 수 있다(그 형식을 쓰는 ``subject_case``가 하나뿐인 템플릿). 채우려고
    두 바퀴를 돌면 같은 프레임이 두 번 나오고, 그건 호출부가 산출물만 보고
    알아챌 수 없다. 부족분은 호출부가 세어 기록한다.
    """

    if per_form < 0:
        raise ValueError("per_form must not be negative")

    remaining = {form: per_form for form in template.document_forms}
    picked: list[CaseFrame] = []
    if per_form == 0:
        return ()

    # 순열이라 한 바퀴면 모든 조합을 정확히 한 번씩 본다. 할당량이 다 차면
    # 그 자리에서 끊는다 — 뒤는 전부 건너뛸 프레임이다.
    for frame in expand_cases(template, template.case_count, seed=seed):
        if remaining.get(frame.document_form, 0) <= 0:
            continue
        remaining[frame.document_form] -= 1
        picked.append(frame)
        if not any(remaining.values()):
            break
    return tuple(picked)


def select_frames_for_agency(
    template: CTrackTemplate,
    count: int,
    *,
    seed: int = 0,
) -> tuple[CaseFrame, ...]:
    """이 템플릿에서 **모두 합쳐** ``count``건을 고른다.

    ``select_frames_per_document_form``은 형식이 기준이라 총량이 형식 수에
    끌려간다 — 형식이 5~8개로 제각각이라 기관마다 나오는 건수가 다르고, 기관당
    한 건 같은 작은 표본을 요구할 방법이 아예 없다. 여기서는 총량이 기준이고
    형식은 그 안에서 고르게 돈다.

    **고르게 도는 방법.** 셔플 순서를 걸어가며 지금 가장 적게 뽑힌 형식만
    받는다. ``count``가 형식 수보다 작으면 그만큼 **서로 다른 형식**이 나오고,
    넘으면 형식별 건수가 최대 1건 차이로 갈린다. 어느 형식이 그 1건을 더 받는지는
    셔플이 정하므로 seed가 같으면 항상 같다.

    ``count``가 조합 수를 넘으면 있는 만큼만 돌려준다 — 한 바퀴를 넘겨 채우면
    같은 프레임이 두 번 나온다(``select_frames_per_document_form``과 같은 규칙).
    """

    if count < 0:
        raise ValueError("count must not be negative")
    if count == 0:
        return ()

    picked: list[CaseFrame] = []
    per_form = {form: 0 for form in template.document_forms}
    for frame in expand_cases(template, template.case_count, seed=seed):
        # 가장 적게 뽑힌 형식이 아니면 지나간다. 뒤에서 그 형식이 다시 나온다 —
        # 셔플은 모든 조합을 한 번씩 지나가므로 굶는 형식이 생기지 않는다.
        if per_form[frame.document_form] > min(per_form.values()):
            continue
        per_form[frame.document_form] += 1
        picked.append(frame)
        if len(picked) == count:
            break
    return tuple(picked)


def case_frame(template: CTrackTemplate, case_index: int) -> CaseFrame:
    """좌표 하나를 사건 프레임으로 되돌린다.

    ``CaseFrame.case_index``는 전개 **순번**이 아니라 mixed-radix로 접힌
    **좌표**다(``expand_cases``의 ``order[i]``). 그래서 프레임은 seed와 순번
    없이 이 값 하나로 정해진다 — 산출물에 좌표만 남겨 두면 나중에 같은 프레임을
    다시 세울 수 있다는 뜻이고, 되쓰기(``writeback_c_track_to_rds``)가 기관·부서를
    거기서 읽는다.

    순번으로 오해해 ``islice``로 세면 **다른 프레임이 조용히 나온다.** 그 착각은
    기관이 한 칸 어긋난 행으로만 드러난다.
    """

    free_slots = template.free_slots
    # 적대자는 **맨 뒤**다. 앞에 끼우면 기존 자리의 뜻이 통째로 밀리고, 그
    # 어긋남은 기관이 한 칸 옮겨간 행으로만 드러난다(아래 docstring).
    adversary_pairs = template.adversary_pairs
    radices = (
        len(template.departments),
        len(template.case_stage_pairs),
        len(template.security_grades),
        *(len(slot.values) for slot in free_slots),
        len(adversary_pairs),
        STAGE_VARIANT_COUNT,
    )
    if not 0 <= case_index < template.case_count:
        raise ValueError(
            f"case_index={case_index}가 이 템플릿의 조합 수"
            f"({template.case_count})를 벗어난다"
        )

    digits = _mixed_radix_decode(case_index, radices)
    case, stage = template.case_stage_pairs[digits[1]]
    # 부서와 묶인 슬롯은 부서 자리(digits[0])를 그대로 쓴다. 나머지는 자기 자리다.
    chosen = dict(zip((slot.name for slot in free_slots), (
        slot.values[digit] for slot, digit in zip(free_slots, digits[3:-2])
    )))
    if template.department_paired_slot is not None:
        paired = next(
            slot for slot in template.slots
            if slot.name == template.department_paired_slot
        )
        chosen[paired.name] = paired.values[digits[0]]
    return CaseFrame(
        subclause_key=template.subclause_key,
        agency=template.agency,
        department=template.departments[digits[0]],
        subject_case=case,
        stage=stage,
        security_grade=template.security_grades[digits[2]],
        adversaries=adversary_pairs[digits[-2]],
        stage_variant=digits[-1],
        # 템플릿에 적힌 슬롯 순서를 지킨다 — 프롬프트 절의 줄 순서가 이걸 따른다.
        slot_values=MappingProxyType(
            {slot.name: chosen[slot.name] for slot in template.slots}
        ),
        case_index=case_index,
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


def render_cot_stage_1() -> str:
    """[단계 1]의 질문 골격. **적대자는 여기 없다.**

    v3까지는 이 함수가 ``template.adversaries`` 전원을 꽂았고 그 결과가 고정
    접두사에 실렸다 — 한 템플릿의 4000건이 같은 넷을 읽었다는 뜻이다. 누구를
    놓고 답하는지가 [단계 1]의 답을 정하고 그 답이 [단계 2] 본문을 정하므로,
    이 모듈에서 본문 내용을 정하는 축 둘 중 하나가 고정돼 있던 셈이다.

    v4에서 축으로 올리면서 목록은 ``render_cot_adversary_section``으로 갔다.
    질문 골격만 남기는 이유는 캐싱이다 — 이쪽은 템플릿 전체가 공유하므로
    접두사에 있어야 하고, 건마다 갈리는 것은 ``[조건]``으로 내려가야 한다
    (``render_fixed_prefix``가 세운 원칙과 같다).
    """

    return f"{_COT_STAGE_1_HEAD}\n{_COT_STAGE_1_TAIL}"


def render_cot_adversary_section(frame: CaseFrame) -> str:
    """``[조건]``에 딸리는 ``[관계자]`` 절 — 이 건의 적대자 둘.

    이름만 나열하지 않는다. ``Adversary.what_they_gain``이 함께 서야 모델이
    "무엇이 무력화되는지"를 그 층위로 답한다 — 이름만 주면 낱말로 짐작한다.

    셋 이상 서면 [단계 1]의 답이 항목당 한 줄로 얕아진다. 둘이라는 것은
    ``CTrackTemplate.adversary_pairs``가 정한다.
    """

    lines = ["[관계자]"]
    for adversary in frame.adversaries:
        lines.append(
            f"- {adversary.who}({adversary.position_label})"
            f" — {adversary.what_they_gain}"
        )
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
            render_cot_stage_1(),
            "",
            _COT_STAGE_2_HEAD,
            "",
            template.instruction,
            "",
            _COT_TITLE_RULE,
            "",
            _COT_CONDITION_USE_RULE,
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
        # [단계 1]이 이 절을 이름으로 지목한다(``_COT_STAGE_1_HEAD``). ``[조건]``
        # 바로 뒤에 두는 것은 그 지목이 "[조건]의 [관계자]"이기 때문이다.
        #
        # 여기는 ``라벨: 값``이어도 된다. ``CaseSlot.phrasing``이 문장으로 바꾼
        # 것은 조건표가 **문서의 첫 절로** 복사됐기 때문인데, 이 절이 대답하는
        # 자리는 본문이 아니라 ``security_analysis``다.
        render_cot_adversary_section(frame),
        "",
        # 여기부터는 ``라벨: 값``이 아니라 문장이다. 이유는 ``CaseSlot.phrasing``
        # 주석에 적어 뒀다 — 라벨 형태로 두면 그 표가 통째로 문서의 첫 절이 된다.
        "[다루는 사건]",
        f"{case.subject}, {frame.stage} 단계에서 작성한다.",
        frame.stage_guidance,
    ]
    for slot in event_slots:
        value = frame.slot_values[slot.name]
        lines.append(slot.phrasing.format(value=value))
        hint = slot.guidance.get(value)
        if hint:
            lines.append(hint)

    lines += [
        "",
        # 절 이름은 그대로 ``[문서 정보]``다. 안쪽만 문장으로 바꿨고, 종류 이름을
        # 가리키던 ``_COT_TITLE_RULE``의 지목도 절 이름이 아니라 "종류로 주어진
        # 이름"으로 옮겼다 — 규칙이 없는 것을 가리키면 제목이 ``document_name``
        # 으로 돌아간다(실측 2026-08-03, 10건 중 8건).
        "[문서 정보]",
        f"이 문서의 종류는 '{case.document_name}'"
        f"({DOCUMENT_FORM_DEFINITIONS[case.document_form].label})이다.",
        # 조사를 붙이지 않는다. 표제부 항목의 마지막 글자가 무엇이냐에 따라
        # 을/를이 갈리는데 그 값은 문서형식마다 다르다.
        f"표제부 항목은 {' / '.join(header_keys_for(case.document_form))}다.",
        f"{_grade_display(frame.security_grade)} 문서다. "
        f"{_GRADE_GUIDANCE[frame.security_grade]}",
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

    ``frame.adversaries``는 여기 싣지 않는다. 이 경로에는 적대자를 놓고 답하는
    [단계 1]이 없고, 대조군 문구를 조용히 바꾸지 않는 것이 이 함수가 3단계
    경로와 갈라져 서 있는 이유다(``_situation_sections`` 주석).

    대신 이 경로에서는 좌표 6개가 쓰지 않는 축 하나에서만 갈린다. 배치가 없고
    ``--case-index``로 한 건씩만 도는 경로라 실무에서 부딪히지 않지만,
    여기에 배치를 붙인다면 그 6배는 **다양성이 아니다.**
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
        f"  → {frame.stage_guidance}",
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
