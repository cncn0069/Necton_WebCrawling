"""세세한 ``DocumentForm``을 ``doc_type`` 컬럼의 6칸으로 몰아주는 어댑터.

**왜 두 어휘를 쓰는가.** 생성 쪽은 형식이 잘게 나뉘어야 한다 — 형식마다 표제부가
다르고(``document_form.HEADER_KEYS_BY_FORM``), 그 표제부가 곧 생성기 지시이자
채점 대상이다. 회의록의 ``회차/개최일시/장소/참석자``와 수사보고서의
``사건번호/작성일자/조사대상자/소속``을 한 칸으로 합치면 생성이 무너진다.

반대로 소비 쪽은 그만큼 잘게 필요하지 않다. 그래서 **생성은 17종, 저장은 6칸**
으로 두고 그 사이를 이 표가 잇는다. 순서가 중요하다 — 몰아주기는 행을 조립하는
**마지막**에 일어난다. 앞단에서 미리 합치면 생성기가 잃은 구분을 되살릴 방법이
없지만, 마지막에 합치면 원래 형식이 ``batch_json["document_form"]``과 렌더
envelope의 ``source_classification``에 그대로 남아 언제든 되짚을 수 있다.

**왜 한글인가.** ``storage/naming.py``는 "한글 doc_type을 영어 코드로 매핑하는
단일 진실 공급원"이고, 2026-07-09에 이 컬럼과 ``data/`` 폴더명에서 한글을
걷어낸 이력이 있다. 여기 6칸은 그 규약의 **의도된 예외**다 — 저장값 자체가
한글이어야 한다는 결정(2026-08-05 사용자)이고, 적용 범위는 생성분
(``data_origin='G'``) 뿐이다. 수집분은 지금처럼 영어 코드로 남는다.

그래서 이 컬럼에는 두 어휘가 섞인다. 섞인 것을 가르는 기준은 낱말이 아니라
``data_origin``이다 — ``WHERE data_origin='G'``면 아래 6개 중 하나이고,
``'O'``면 ``naming.py``의 영어 코드다. 낱말로 가르려 들면 ``회의록``처럼
양쪽에 같은 뜻으로 존재하는 값에서 갈리지 않는다.

**한 방향만 함수다.** ``form → 칸``은 정해지지만 역방향은 정해지지 않는다
(공문 한 칸에 여섯 형식이 들어간다). 그래서 이 표는 원래 값을 **대체하지 않고
덮어쓸 뿐**이며, 원래 형식을 읽어야 하는 쪽은 위의 두 자리를 본다.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from rd2.source_generation.classification_taxonomy import DocumentForm

#: ``doc_type`` 컬럼에 실제로 들어가는 6개 값. 문자열을 직접 쓰지 말고 이
#: 상수를 참조한다 — 오타 하나가 일곱 번째 칸을 만든다.
DOC_TYPE_OFFICIAL_LETTER = "공문"
DOC_TYPE_STATUS_REPORT = "현황보고서"
DOC_TYPE_MEETING_MINUTES = "회의록"
DOC_TYPE_AUDIT_REPORT = "감사보고서"
DOC_TYPE_POLICY_DOCUMENT = "정책문서"
DOC_TYPE_REGULATION = "규정"

#: 6칸 전부. 저장된 값이 이 안에 있는지 확인하는 쪽이 쓴다.
DOC_TYPE_BUCKETS: tuple[str, ...] = (
    DOC_TYPE_OFFICIAL_LETTER,
    DOC_TYPE_STATUS_REPORT,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_AUDIT_REPORT,
    DOC_TYPE_POLICY_DOCUMENT,
    DOC_TYPE_REGULATION,
)

#: 문서형식 → 칸. 17종 전부 적는다 — 빠지면 아래에서 import 시점에 터진다.
#:
#: 묶는 기준은 낱말이 아니라 **그 문서가 무엇을 하는 물건인가**다:
#:
#:     공문       개별 사안을 기안·결재·회신하는 처리 문서
#:     현황보고서 진행된 일의 상태를 보고하는 문서
#:     회의록     합의 과정 자체가 내용인 문서
#:     감사보고서 대상을 확인한 결과가 내용인 문서 (감사·점검·조사)
#:     정책문서   앞으로 할 일을 정하는 문서 (계획·대응·정책)
#:     규정       조문 형식으로 효력을 갖는 규범
DOC_TYPE_BY_FORM: Mapping[DocumentForm, str] = MappingProxyType(
    {
        # --- 공문 ---
        DocumentForm.OFFICIAL_LETTER: DOC_TYPE_OFFICIAL_LETTER,
        DocumentForm.APPROVAL_REQUEST: DOC_TYPE_OFFICIAL_LETTER,
        DocumentForm.REPLY_NOTICE: DOC_TYPE_OFFICIAL_LETTER,
        DocumentForm.PERSONNEL_MATERIAL: DOC_TYPE_OFFICIAL_LETTER,
        DocumentForm.BID_MATERIAL: DOC_TYPE_OFFICIAL_LETTER,
        # 법률검토서는 6칸 중 제 자리가 없다. 검토 **의견을 회신하는** 문서라는
        # 점에서 공문에 가장 가깝지만, 이건 합친 것이 아니라 남는 것을 넣은
        # 것이다 — 칸을 늘리면 가장 먼저 나올 후보다(C트랙 216건 중 16건).
        DocumentForm.LEGAL_REVIEW: DOC_TYPE_OFFICIAL_LETTER,
        # ``other``는 목록 밖 형식이라 어느 칸도 관측이 아니다. 6칸 안에 넣는
        # 것이 이 어댑터의 약속이므로 가장 넓은 칸으로 보내되, C트랙에서는
        # 애초에 나오지 않는다(``SubjectCase.__post_init__``이 거부한다).
        DocumentForm.OTHER: DOC_TYPE_OFFICIAL_LETTER,
        # --- 현황보고서 ---
        DocumentForm.REPORT: DOC_TYPE_STATUS_REPORT,
        # --- 회의록 ---
        DocumentForm.MEETING_MINUTES: DOC_TYPE_MEETING_MINUTES,
        # --- 감사보고서 ---
        DocumentForm.AUDIT_MATERIAL: DOC_TYPE_AUDIT_REPORT,
        # 점검(대상이 사물)과 조사(대상이 사람)를 감사와 한 칸에 넣는다.
        # ``check_document_form``이 보는 구분은 표제부에 그대로 남는다 —
        # 점검보고서는 ``점검일시/점검대상/점검자``, 수사보고서는 ``사건번호/
        # 조사대상자/소속``이라 본문만 봐도 셋이 갈린다.
        DocumentForm.INSPECTION_REPORT: DOC_TYPE_AUDIT_REPORT,
        DocumentForm.INVESTIGATION_REPORT: DOC_TYPE_AUDIT_REPORT,
        # --- 정책문서 ---
        DocumentForm.POLICY_MATERIAL: DOC_TYPE_POLICY_DOCUMENT,
        # 계획안을 공문이 아니라 이쪽에 두는 이유: 계획안은 개별 사안의 처리가
        # 아니라 앞으로 할 일을 정하는 문서다. 공문으로 보내면 C트랙 216건 중
        # 공문 한 칸이 90건(42%)이 되어 칸을 나눈 뜻이 없어진다.
        DocumentForm.PLAN_DRAFT: DOC_TYPE_POLICY_DOCUMENT,
        DocumentForm.RESPONSE_PLAN: DOC_TYPE_POLICY_DOCUMENT,
        # 보도자료는 정책 내용을 대외로 알리는 문서다. 수집 코퍼스에는 전용
        # 코드(``press_release``)가 있지만 6칸에는 없으므로 이쪽으로 온다.
        DocumentForm.PRESS_RELEASE: DOC_TYPE_POLICY_DOCUMENT,
        # --- 규정 ---
        DocumentForm.ADMINISTRATIVE_RULE: DOC_TYPE_REGULATION,
    }
)

_unmapped = tuple(form for form in DocumentForm if form not in DOC_TYPE_BY_FORM)
if _unmapped:  # pragma: no cover - import 시점 가드
    raise RuntimeError(
        "DOC_TYPE_BY_FORM에 빠진 문서형식: "
        + ", ".join(form.value for form in _unmapped)
    )

_stray = tuple(sorted(set(DOC_TYPE_BY_FORM.values()) - set(DOC_TYPE_BUCKETS)))
if _stray:  # pragma: no cover - import 시점 가드
    raise RuntimeError("6칸 밖으로 나간 doc_type: " + ", ".join(_stray))


def doc_type_for_form(document_form: DocumentForm) -> str:
    """이 문서형식이 들어갈 ``doc_type`` 칸.

    ``dict.get``이 아니라 ``[]``로 읽는다 — 빠진 형식은 조용히 NULL이 되는 게
    아니라 ``KeyError``로 드러나야 한다. 위 import 가드가 이미 막지만, 표를
    직접 손댈 사람에게 두 번 말해 둔다.
    """

    return DOC_TYPE_BY_FORM[document_form]
