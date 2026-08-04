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

    definition = SUBCLAUSE_DEFINITIONS[template.subclause_key]
    clause = CLAUSES[template.clause_no.value]

    return "\n".join(
        [
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
            "",
            "[비공개 근거]",
            "공개 시 이 세부유형의 보호 대상에 생길 구체적인 지장이 본문에서 "
            "확인되게 한다. 어떤 정보가 누구에게 알려지면 무엇이 무력화되는지 "
            "경로가 드러나야 하며, 근거 없이 '민감함'·'대외비임'으로 끝내지 않는다.",
            "",
            _TITLE_RULE,
            "",
            _DOCUMENT_STYLE_RULES,
            "",
            _BLOCK_STRUCTURE_RULES,
        ]
    )


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
