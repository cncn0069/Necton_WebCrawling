"""통합 문서 스키마 (RD-2 v1.1 기준).

O(공개)/C(기밀)/S(민감) 세 트랙이 공유하는 단일 스키마.
설계 문서: ~/.gstack/projects/CODE/안정현-design-*.md 참고.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, computed_field, model_validator


class CsoClassification(str, Enum):
    O = "O"
    C = "C"
    S = "S"


class DataOrigin(str, Enum):
    """실제 수집한 원본(O)인지 LLM으로 생성한 데이터(G)인지.

    ``CsoClassification.O``와 글자가 겹치지만 의미가 다르다 — 저기 O는 "공개 트랙",
    여기 O는 "원본"이다. 두 값은 서로 독립적이라(생성 문서도 O/C/S 어느 트랙이든
    될 수 있다) 한쪽으로 다른 쪽을 추론하면 안 된다.
    """

    ORIGINAL = "O"
    GENERATED = "G"


class DisclosureStatus(str, Enum):
    OPEN = "공개"
    PARTIAL = "부분공개"
    CLOSED = "비공개"


class Document(BaseModel):
    # 필요한 메타정보 (RD-2 v1.1)
    title: str = Field(description="문서 제목")
    ordering_agency: str = Field(description="기관(발주기관)")
    department: str | None = Field(default=None, description="생성부서")
    unit_task: str | None = Field(default=None, description="단위업무")
    production_date: date | None = Field(default=None, description="생산일자")
    disclosure_status: DisclosureStatus = Field(description="공개여부")
    subject_category: str | None = Field(default=None, description="분류체계(주제 분류)")
    content_summary: str | None = Field(default=None, description="내용(요약)")
    body_text: str | None = Field(
        default=None,
        description=(
            "문서 본문. 정보공개포털 등 사전정보공개 목록은 국장급 이상 결재문서가 "
            "아니면 본문을 제공하지 않고 정보공개청구를 안내하는 메시지만 반환한다 "
            "— 그 경우 이 필드는 None이고 메타데이터만으로 레코드를 저장한다."
        ),
    )
    body_file_path: str | None = Field(
        default=None, description="본문 파일 경로(대표 파일, files_root 기준 상대경로)"
    )
    table_of_contents: str | None = Field(
        default=None,
        description=(
            "목차. 비공개/부분공개 문서에도 실제 목차(섹션 제목)가 그대로 제공되는 "
            "출처가 있어(예: PRISM) 공개여부와 무관하게 존재하면 그대로 저장한다."
        ),
    )
    other_file_paths: list[str] = Field(
        default_factory=list,
        description=(
            "같은 문서에 딸린 나머지 파일 경로(심의신청서/계약서 등 대표 파일이 "
            "아닌 첨부). 대표 파일 하나만 body_file_path에 들어가고 나머지는 "
            "여기 전부 저장된다 — 파일 자체는 전부 로컬에 저장되어 있으므로 "
            "데이터 손실은 없다."
        ),
    )
    non_disclosure_reason: str | None = Field(
        default=None, description="비공개사유 (비공개/부분공개일 때만)"
    )
    cso_classification: CsoClassification = Field(description="C/S/O 분류")
    cso_sub_clause: str | None = Field(
        default=None,
        description=(
            "C/S/O 분류 하부조항(정보공개법 제9조 호수, 숫자만 — 예: '5', 복수 조항이면 "
            "'3,5'). 이 값이 있으면 disclosure_status보다 우선해 C/S를 결정한다"
            "(1~4호=C, 5~8호=S) — disclosure_status는 조항 정보를 제공하지 않는 "
            "소스에서만 근사치로 쓴다."
        ),
    )
    performing_agency: str | None = Field(default=None, description="수행기관명")
    start_date: date | None = Field(default=None, description="시작일")
    end_date: date | None = Field(default=None, description="종료일")

    # 수집 메타데이터 (스키마 확장 — RD-2에 "필요한 메타정보는 추가 가능"이라 명시됨)
    source: str = Field(description="출처 (예: 정보공개포털, PRISM, synthetic 등)")
    source_url: str | None = Field(default=None, description="원문 URL (합성 문서는 없음)")
    doc_type: str | None = Field(default=None, description="문서종류 (공문/계약/회의/보도자료 등)")

    # 실제 수집(O) vs LLM 합성(C/S) 구분 플래그. cso_classification과 별개로 각
    # 수집기/생성기가 직접 설정한다 — 부분공개 문서처럼 cso_classification=C/S이면서
    # is_synthetic=False인 실제 수집 문서(원문정보 어댑터)가 존재할 수 있으므로,
    # 트랙과의 강제 일치 검증은 수집 단계(RD-2)의 책임이 아니다.
    is_synthetic: bool = Field(description="LLM 합성 생성 문서 여부")

    # 생성(G) 행에만 채워지는 자리. 수집(O) 행은 넷 다 None이다.
    #
    # 지금까지 생성 provenance는 배치 산출물(JSONL)에만 있었고 DB에 남는 흔적은
    # source_url 문자열 안의 원문 id 하나뿐이었다 —
    # "synthetic://source-generation/seoul_opengov-19510/6-personnel_pii".
    # 문자열을 파싱해야 원문에 닿고, 어떤 입력으로 만든 본문인지는 DB만 봐서는
    # 알 수 없었다. 학습셋을 만들 때 그 셋(입력 프롬프트·입력 본문·출력 본문)이
    # 원문 행과 함께 필요해 컬럼으로 올린다.
    input_prompt: str | None = Field(
        default=None, description="생성기에 넣은 프롬프트 (생성 문서만)"
    )
    content: str | None = Field(
        default=None,
        description=(
            "프롬프트 입력으로 실제 들어간 원문 본문. 원문 전체가 아니라 "
            "자르고 난 뒤의 값이다(현재 최소 경로는 앞 40쪽) — 무엇을 보고 "
            "만들었는지는 원문 파일이 아니라 이 값이 답한다."
        ),
    )
    generated_text: str | None = Field(
        default=None,
        description=(
            "생성기가 낸 문서 IR을 그대로 직렬화한 JSON (생성 문서만). PDF "
            "앞에서 실제로 나오는 산출물이 이 JSON이고 블록·표 구조가 여기 "
            "남는다 — 거기서 파생된 평문은 body_text에 있다."
        ),
    )
    ref_id: int | None = Field(
        default=None,
        description=(
            "본문을 참조한 원문 documents.id. 민감(S) 생성 문서가 어느 공개 "
            "원문에서 나왔는지를 가리킨다 — 수집 문서는 None."
        ),
    )
    batch_json: str | None = Field(
        default=None,
        description=(
            "PDF 렌더에 필요한 값들을 담은 JSON (생성 문서만). 본문은 넣지 "
            "않는다 — 같은 행의 generated_text가 이미 문서 IR을 통째로 갖고 "
            "있어 두 칸에 같은 본문을 두면 둘이 갈라질 자리가 생긴다. 여기 "
            "들어가는 것은 본문 밖에서 서식을 정하는 값이다: 문서형식(템플릿 "
            "선택), 비밀등급 표기, 표제부에 찍히는 기관·부서, 그리고 어느 "
            "템플릿·사건 프레임에서 나왔는지의 좌표."
        ),
    )

    @computed_field(description="생성 문서면 '1', 수집 문서면 '0'")
    @property
    def generated_yn(self) -> str:
        """DB에 저장되는 1/0 플래그. ``is_synthetic``에서 파생된다.

        **int가 아니라 한 글자 문자열이다.** 컬럼이 ``BINARY(1)``이고 기본값이
        ``x'30'``(= ASCII '0')이라, 기존 행과 새 행이 같은 바이트를 갖게 하려면
        여기서도 ASCII를 넣어야 한다. int 1을 넘기면 드라이버·서버 변환에 따라
        0x01이 들어갈 수 있고, 그러면 ``WHERE generated_yn = '1'``이 그 행을
        놓친다.
        """
        return "1" if self.is_synthetic else "0"

    @computed_field(description="원본 수집(O) / LLM 생성(G) 구분")
    @property
    def data_origin(self) -> DataOrigin:
        """O/G 플래그. ``is_synthetic``에서 파생된다.

        **DB 컬럼이 아니다.** RDS(ingest_data.documents)에는 이 컬럼이 없고 같은
        사실을 ``generated_yn``이 담는다 — 두 DB의 스키마를 같게 두려고
        2026-08-04에 컬럼 목록에서 뺐다. 값 자체는 JSONL 산출물·리포트가 계속
        읽으므로 필드는 남긴다."""
        return DataOrigin.GENERATED if self.is_synthetic else DataOrigin.ORIGINAL

    @model_validator(mode="after")
    def _require_non_disclosure_reason_when_not_open(self) -> "Document":
        """비공개/부분공개 문서는 비공개사유가 있어야 한다 (RD-2 v1.1)."""
        if (
            self.disclosure_status != DisclosureStatus.OPEN
            and not self.non_disclosure_reason
        ):
            raise ValueError(
                "non_disclosure_reason is required when disclosure_status is "
                f"{self.disclosure_status.value}"
            )
        return self
