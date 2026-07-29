"""실제 P1/P2를 호출해 조항별·행정상태별 대표 생성물 20건을 만든다.

입력은 실사 때 보존한 ``tmp/live_source_generation/source_snapshot.json``이며,
출력은 케이스별 프롬프트·P1·P2·GeneratedDocumentIR과 전체 CSV/JSONL이다.
P1 성공 산출물을 먼저 저장하므로 P2 실패 후 재실행해도 P1을 다시 호출하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rd2.administrative_status import ADMIN_STATUS_TEXT_POLICIES, AdminStatus  # noqa: E402
from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    ClauseNumber,
    SubclauseKey,
    SUBCLAUSE_LABELS,
)
from rd2.source_generation.contracts import (  # noqa: E402
    CallReceipt,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationProvenance,
    GenerationTarget,
    Pass1Result,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
    TargetClassification,
)
from rd2.source_generation.document_select import (  # noqa: E402
    SelectionConfig,
    prepare_document_selection,
    render_selected_source,
)
from rd2.source_generation.legacy_synthetic import (  # noqa: E402
    FullySyntheticContext,
    FullySyntheticDocumentGenerator,
)
from rd2.source_generation.pipeline import (  # noqa: E402
    OpenAIResponsesGateway,
    RetryingGateway,
    PipelineConfig,
    StructuredCallError,
    StructuredOutputGateway,
    _assessment_scope,
    _generation_plan_json,
    execute_pass1,
    execute_pass2,
)
from rd2.source_generation.prompts import (  # noqa: E402
    build_prompt_bundle,
    render_pass1_user_prompt,
    render_pass2_user_prompt,
)


@dataclass(frozen=True)
class PreviewCase:
    case_id: str
    sample_axis: str
    sample_value: str
    target: GenerationTarget
    sensitive_seed: str | None = None
    required_content_markers: tuple[str, ...] = ()


CLAUSE_CASES = (
    (
        ClauseNumber.CLAUSE_1,
        SubclauseKey.LEGAL_SECRET,
        TargetClassification.C,
        (
            "2026년 가구이동 표본조사 원자료 검토 건. 비공개 근거는 통계법 "
            "제33조(비밀의 보호)이다. 조사표 ID A-017의 응답값은 월평균 소득 "
            "3,840,000원, 금융부채 72,000,000원, 가구원 수 3명이다. 원자료 "
            "열람자는 통계분석팀 김가람 주무관과 박해준 연구원으로 제한하며, "
            "외부 제공본에서는 조사표 ID와 세부 응답값을 제외한다."
        ),
        ("통계법 제33조", "A-017", "3,840,000원", "외부 제공본"),
    ),
    (
        ClauseNumber.CLAUSE_2,
        SubclauseKey.SECURITY_DEFENSE,
        TargetClassification.C,
        (
            "서해 수자원통합센터 경계 취약점 점검 건. B동 북측 울타리 120m "
            "구간은 야간 영상 식별률이 38%이고, 순찰 공백은 02:10~02:21이다. "
            "비상발전기실 출입문 센서 ID SG-04는 최근 30일간 오경보 7회를 "
            "기록했다. 개선안은 열화상 카메라 2대 추가, 순찰 간격 20분에서 "
            "10분으로 단축, SG-04 교체이며 시행 목표일은 2026-08-15이다."
        ),
        ("SG-04", "02:10", "38%", "순찰"),
    ),
    (
        ClauseNumber.CLAUSE_3,
        SubclauseKey.PROPERTY,
        TargetClassification.C,
        (
            "긴급 주거지원 대상자 보호 검토 건. 대상자 식별번호 V-203은 현재 "
            "임시거처 G-03에 머물고 있으며, 상대방이 2026-07-22과 07-24 두 "
            "차례 거주지를 탐문했다. 등하교 동행 시간은 07:40과 16:20이고, "
            "비상연락 담당은 사례관리자 C-11이다. 임시거처 위치·동행 시간·"
            "연락망 공개 시 대상자의 생명·신체 안전과 재산 보호에 직접 위험이 있다."
        ),
        ("V-203", "G-03", "07:40", "생명"),
    ),
    (
        ClauseNumber.CLAUSE_4,
        SubclauseKey.TRIAL_INVESTIGATION,
        TargetClassification.C,
        (
            "조사사건 2026-조사-014의 진행계획. 참고인 R-08 면담은 "
            "2026-08-03 14:00, 계약담당자 R-12 면담은 2026-08-04 10:30으로 "
            "예정되어 있다. 확보 전 자료는 서버 접속기록 4건과 결재 초안 2건이며, "
            "R-08의 '평가표가 사전에 공유됐다'는 진술은 아직 교차 확인되지 않았다. "
            "면담 전 질문목록과 증거 확보 순서를 외부에 공개하면 조사 수행에 지장이 있다."
        ),
        ("2026-조사-014", "R-08", "2026-08-03", "조사 수행"),
    ),
    (
        ClauseNumber.CLAUSE_5,
        SubclauseKey.BID_CONTRACT,
        TargetClassification.S,
        (
            "민원통합플랫폼 구축 용역의 공고 전 평가안. 기술적합성 35점, "
            "이행계획 25점, 보안대책 20점, 가격평가 20점으로 총 100점이다. "
            "기술평가 통과선은 75점, 우선협상대상자 협상 상한은 380,000,000원이다. "
            "평가위원 후보는 E-04, E-11, E-17이며 공고 전에는 배점표·통과선·"
            "위원 후보·협상 상한을 공개하지 않는다."
        ),
        ("기술적합성", "75점", "380,000,000원", "공고 전"),
    ),
    (
        ClauseNumber.CLAUSE_6,
        SubclauseKey.PERSONNEL_PII,
        TargetClassification.S,
        (
            "2026년 전산직 채용 면접 결과. 지원자 P-104는 필기 86점, 면접 "
            "82점, 종합 84.4점이며 연락처는 010-73**-1204이다. 지원자 P-117은 "
            "필기 79점, 면접 91점, 종합 83.8점이며 연락처는 010-42**-7711이다. "
            "P-104의 면접 편의 제공 항목은 키보드 보조장치이며, P-117의 "
            "가족관계 증빙은 인사담당자 "
            "H-02만 열람하고, 공개본에는 개인별 점수와 연락처를 제외한다."
        ),
        ("P-104", "84.4", "P-117", "연락처"),
    ),
    (
        ClauseNumber.CLAUSE_7,
        SubclauseKey.UNIT_COST,
        TargetClassification.S,
        (
            "정수장 필터모듈 공급사 M-21의 단가 협상자료. 모듈 1개당 재료비 "
            "42,800원, 노무비 18,500원, 물류비 4,700원, 일반관리비 3,400원으로 "
            "산정원가는 69,400원이다. 업체 제안단가는 72,000원, 기관 목표단가는 "
            "70,200원이며 연간 예정수량은 12,000개다. 원가 구성과 목표단가는 "
            "계약 체결 전 외부에 제공하지 않는다."
        ),
        ("M-21", "69,400원", "70,200원", "계약 체결 전"),
    ),
    (
        ClauseNumber.CLAUSE_8,
        SubclauseKey.REAL_ESTATE_SPECULATION,
        TargetClassification.S,
        (
            "가람역세권 정비예정구역 사전 검토. 검토구역은 A-17·A-18·B-03 "
            "필지 총 84,200㎡이고 추정보상비는 18,400,000,000원이다. 기준일 "
            "공시지가는 ㎡당 612,000원이며 주민공람 예정일은 2026-09-21이다. "
            "공람 전 필지목록·보상 추정액·경계도면이 공개되면 토지거래 집중과 "
            "가격 급등을 유발할 우려가 있다."
        ),
        ("A-17", "84,200㎡", "18,400,000,000원", "공람 전"),
    ),
)

ADMIN_CONTENT_MARKERS: dict[AdminStatus, tuple[str, ...]] = {
    AdminStatus.APPROVAL_PENDING: ("기안", "최종 결재"),
    AdminStatus.RELEASE_NOT_DUE: ("공개 예정일", "대외 공개 전"),
    AdminStatus.DRAFT: ("작성 중", "확정되지"),
    AdminStatus.INTERNAL_REVIEW: ("담당 부서", "검토 결과"),
    AdminStatus.ATTACHMENT_MISSING: ("붙임", "등록되지"),
    AdminStatus.DISCLOSURE_REVIEW: ("정보공개 범위", "심사"),
    AdminStatus.AGENCY_CONSULT: ("관계기관", "회신"),
    AdminStatus.DEIDENTIFY_PENDING: ("비식별", "외부 제공"),
    AdminStatus.SYSTEM_REGISTRATION_ERROR: ("전자문서 시스템", "오류"),
    AdminStatus.DOCUMENT_DISPOSITION: ("문서 분류", "보존"),
    AdminStatus.PETITION_IN_PROGRESS: ("민원", "사실관계"),
    AdminStatus.AUDIT_IN_PROGRESS: ("감사", "처분 결과"),
}


class PreviewSyntheticGenerator(FullySyntheticDocumentGenerator):
    """P1이 source-free 경로를 택해도 C/S 전체 조항을 실제 LLM으로 생성한다."""

    def __init__(self, gateway: StructuredOutputGateway, model: str) -> None:
        self.gateway = gateway
        self.model = model
        self.last_prompt = ""
        self.last_response_id = ""

    def generate(
        self,
        *,
        target: GenerationTarget,
        context: FullySyntheticContext,
    ) -> GeneratedDocumentIR:
        clause = target.clause_no.value if target.clause_no else ""
        subclause = target.subclause_key.value if target.subclause_key else ""
        status_context = [
            ADMIN_STATUS_TEXT_POLICIES[status].detail
            for status in target.administrative_statuses
        ]
        self.last_prompt = json.dumps(
            {
                "instruction": (
                    "원문을 사용하지 않는 완전 합성 대한민국 공공문서를 작성하라. "
                    "상태명이나 정답용 고정 문구를 억지로 쓰지 말고 상황과 문맥으로 "
                    "드러내라. 허용된 5개 block 종류만 사용하라."
                ),
                "classification": target.classification.value,
                "clause_no": clause,
                "subclause_key": subclause,
                "subclause_label": (
                    SUBCLAUSE_LABELS[target.subclause_key]
                    if target.subclause_key
                    else ""
                ),
                "administrative_status_context": status_context,
                "scenario_id": context.scenario_id,
                "ordering_agency": context.ordering_agency,
                "production_date": context.production_date,
            },
            ensure_ascii=False,
            indent=2,
        )
        call = self.gateway.parse(
            model=self.model,
            system_prompt=(
                "당신은 학습용 합성 공공문서 생성기다. 실존 개인정보·기밀을 쓰지 "
                "않고 입력 목표에 맞는 GeneratedDocumentIR만 반환한다."
            ),
            user_prompt=self.last_prompt,
            response_model=GeneratedDocumentIR,
            max_output_tokens=8_000,
        )
        self.last_response_id = call.response_id
        if not isinstance(call.parsed, GeneratedDocumentIR):
            raise ValueError("synthetic preview generator returned wrong contract")
        return call.parsed


def preview_cases() -> tuple[PreviewCase, ...]:
    cases = [
        PreviewCase(
            case_id=f"clause-{clause.value}",
            sample_axis="clause",
            sample_value=clause.value,
            target=GenerationTarget(
                classification=classification,
                clause_no=clause,
                subclause_key=subclause,
                generation_mode=GenerationMode.COUNTERFACTUAL,
            ),
            sensitive_seed=sensitive_seed,
            required_content_markers=required_content_markers,
        )
        for (
            clause,
            subclause,
            classification,
            sensitive_seed,
            required_content_markers,
        ) in CLAUSE_CASES
    ]
    cases.extend(
        PreviewCase(
            case_id=f"admin-{index:02d}-{status.name.lower()}",
            sample_axis="admin_status",
            sample_value=status.value,
            target=GenerationTarget(
                classification=TargetClassification.S,
                administrative_statuses=(status,),
                generation_mode=GenerationMode.COUNTERFACTUAL,
            ),
            required_content_markers=ADMIN_CONTENT_MARKERS[status],
        )
        for index, status in enumerate(AdminStatus, start=1)
    )
    return tuple(cases)


def _synthetic_public_snapshot() -> SourceDocumentSnapshot:
    """외부 모델 전송에 안전한 실존 정보 없는 공개형 기준 문서."""
    texts = (
        "지역 행정서비스 처리시간 개선안",
        "조사범위: 민원창구 5개소, 처리사례 1,240건, 이용자 설문 360명",
        "현황: 평균 대기시간 23분, 동일정보 반복입력 비율 42%, 재방문율 18%",
        (
            "대안 A: 통합신청 화면 도입, 구축비 186,000,000원, "
            "2개 민원창구에서 4개월간 시범운영"
        ),
        (
            "대안 B: 예약시간제 확대, 월 운영비 12,500,000원, "
            "예상 대기시간 15분"
        ),
        (
            "검토의견: 대안 A를 우선 추진하고 시범운영 후 평균 대기시간 "
            "20% 이상 단축 여부를 평가"
        ),
    )
    blocks = tuple(
        SourceTextBlock(block_id=f"p1:b{index}", text=text)
        for index, text in enumerate(texts)
    )
    source_payload = "\n".join(texts).encode("utf-8")
    return SourceDocumentSnapshot(
        source_document_id="synthetic-public-policy-source-001",
        source="preview_synthetic",
        manifest_key="preview/synthetic-public-policy-source-001",
        source_sha256=hashlib.sha256(source_payload).hexdigest(),
        pages=(SourcePage(page_number=1, blocks=blocks),),
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _content_quality_issues(
    case: PreviewCase,
    document: GeneratedDocumentIR,
) -> tuple[str, ...]:
    text = f"{document.title}\n{document.body_text}"
    issues = [
        f"missing required content marker: {marker}"
        for marker in case.required_content_markers
        if marker not in text
    ]
    meta_patterns = (
        "내용을 포함하고 있습니다",
        "내용이 포함되어 있습니다",
        "상황을 다룹니다",
        "문서입니다",
        "보고서입니다",
    )
    issues.extend(
        f"meta-description phrase present: {pattern}"
        for pattern in meta_patterns
        if pattern in text
    )
    issues.extend(
        f"explicit administrative status label present: {status.value}"
        for status in case.target.administrative_statuses
        if status.value in text
    )
    concrete_facts = set(
        re.findall(
            r"\d[\d,]*(?:\.\d+)?",
            text,
        )
    )
    if len(concrete_facts) < 2:
        issues.append("fewer than two concrete numeric facts")
    if not any(block.kind != "paragraph" for block in document.blocks):
        issues.append("no structured content block")
    return tuple(issues)


def _repair_generated_document(
    *,
    case: PreviewCase,
    document: GeneratedDocumentIR,
    issues: tuple[str, ...],
    gateway: StructuredOutputGateway,
    model: str,
) -> tuple[GeneratedDocumentIR, dict[str, object]]:
    user_prompt = json.dumps(
        {
            "instruction": (
                "현재 문서를 실제 내용 중심으로 다시 작성하라. 문서가 무엇을 "
                "포함한다고 소개하지 말고, 사건·평가·수치·결정 내용을 직접 쓴다. "
                "required_content_markers는 문맥에 맞게 모두 본문에 그대로 포함한다. "
                "단위가 있는 구체 수치를 2개 이상 쓰고 paragraph 이외의 block을 "
                "최소 1개 사용한다. 현재 문서의 유효한 사실은 유지한다."
            ),
            "target": case.target.model_dump(mode="json"),
            "source_facts": case.sensitive_seed or "(현재 문서의 공개 업무 사실을 유지)",
            "required_content_markers": list(case.required_content_markers),
            "quality_issues": list(issues),
            "current_document": document.model_dump(
                mode="json",
                exclude_computed_fields=True,
            ),
            "forbidden_literal_status_labels": [
                status.value
                for status in case.target.administrative_statuses
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    call = gateway.parse(
        model=model,
        system_prompt=(
            "당신은 대한민국 공공문서 본문 편집자다. 실존 개인정보나 기밀은 "
            "추가하지 않고 제공된 가상 사실만 사용한다. 설명문이 아니라 실제 "
            "내용이 채워진 GeneratedDocumentIR만 반환한다. 행정상태 라벨은 "
            "정답 메타데이터이므로 본문에 그대로 쓰지 말고 관찰 가능한 업무 "
            "사실과 문맥으로만 표현한다."
        ),
        user_prompt=user_prompt,
        response_model=GeneratedDocumentIR,
        max_output_tokens=8_000,
    )
    if not isinstance(call.parsed, GeneratedDocumentIR):
        raise ValueError("quality repair returned wrong contract")
    return call.parsed, {
        "response_id": call.response_id,
        "request_id": call.request_id,
        "token_usage": (
            call.token_usage.model_dump(mode="json")
            if call.token_usage is not None
            else None
        ),
        "prompt": user_prompt,
    }


def _load_p1(case_dir: Path) -> tuple[Pass1Result, CallReceipt, GenerationProvenance] | None:
    path = case_dir / "p1_stage.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("failure") is not None:
        return None
    result_payload = payload["result"]
    generated_document = result_payload.get("generated_document")
    if isinstance(generated_document, dict):
        generated_document.pop("body_text", None)
    return (
        Pass1Result.model_validate(result_payload),
        CallReceipt.model_validate(payload["receipt"]),
        GenerationProvenance.model_validate(payload["provenance"]),
    )


def _case_summary(case: PreviewCase, case_dir: Path) -> dict[str, str]:
    result_path = case_dir / "pipeline_result.json"
    if not result_path.exists():
        return {
            "case_id": case.case_id,
            "sample_axis": case.sample_axis,
            "sample_value": case.sample_value,
            "status": "not_run",
        }
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    result = payload.get("pass1_result") or {}
    target = result.get("generation_target") or {}
    generated = result.get("generated_document") or {}
    pass2 = payload.get("pass2_assessment") or {}
    comparison = payload.get("comparison") or {}
    failure = payload.get("failure") or {}
    quality_path = case_dir / "content_quality.json"
    quality = (
        json.loads(quality_path.read_text(encoding="utf-8"))
        if quality_path.exists()
        else {}
    )
    return {
        "case_id": case.case_id,
        "sample_axis": case.sample_axis,
        "sample_value": case.sample_value,
        "status": "ok" if not failure else "failed",
        "requested_classification": case.target.classification.value,
        "requested_clause_no": (
            case.target.clause_no.value if case.target.clause_no else ""
        ),
        "requested_subclause_key": (
            case.target.subclause_key.value if case.target.subclause_key else ""
        ),
        "requested_admin_statuses": ",".join(
            status.value for status in case.target.administrative_statuses
        ),
        "final_classification": target.get("classification", ""),
        "final_clause_no": target.get("clause_no") or "",
        "final_subclause_key": target.get("subclause_key") or "",
        "final_admin_statuses": ",".join(target.get("administrative_statuses") or []),
        "generation_route": result.get("generation_route", ""),
        "generated_title": generated.get("title", ""),
        "generated_body_text": generated.get("body_text", ""),
        "p2_classification": pass2.get("classification", ""),
        "p2_clause_no": pass2.get("clause_no") or "",
        "p2_subclause_key": pass2.get("subclause_key") or "",
        "p2_admin_statuses": ",".join(
            finding.get("status", "")
            for finding in pass2.get("administrative_statuses") or []
        ),
        "classification_match": str(comparison.get("classification_match", "")),
        "clause_match": str(comparison.get("clause_match", "")),
        "subclause_match": str(comparison.get("subclause_match", "")),
        "administrative_status_match": str(
            comparison.get("administrative_status_match", "")
        ),
        "requires_review": str(comparison.get("requires_review", "")),
        "failure_stage": failure.get("stage", ""),
        "failure_code": failure.get("code", ""),
        "failure_message": failure.get("message", ""),
        "content_quality_pass": str(quality.get("passed", "")),
        "content_quality_issues": " | ".join(quality.get("issues") or []),
        "case_dir": str(case_dir.resolve()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help=(
            "명시할 때만 실제 스냅샷을 사용한다. 기본은 외부 전송에 안전한 "
            "실존 정보 없는 합성 공개문서다."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / f"output/source_generation_previews_{date.today():%Y%m%d}",
    )
    parser.add_argument("--generator-model", default=None)
    parser.add_argument("--grader-model", default=None)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--force",
        action="store_true",
        help="완료된 케이스와 P1 캐시를 무시하고 실제 호출을 다시 수행한다.",
    )
    parser.add_argument(
        "--max-quality-repairs",
        type=int,
        default=2,
        help="P1 본문 구체성 검사 실패 시 수행할 최대 보정 호출 수",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="확률적 계약 위반 시 같은 요청을 최대 몇 번 보낼지. 1이면 재시도 없음.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="기존 케이스 파일만 읽어 통합 CSV/JSONL을 다시 만든다.",
    )
    args = parser.parse_args(argv)
    if args.max_attempts < 1:
        raise ValueError("max-attempts must be at least 1")
    if args.max_quality_repairs < 0:
        raise ValueError("max-quality-repairs must be non-negative")

    load_dotenv(ROOT / ".env")
    generator_model = args.generator_model or os.environ.get(
        "SOURCE_GENERATOR_MODEL", "gpt-4o"
    )
    grader_model = args.grader_model or os.environ.get(
        "SOURCE_GRADER_MODEL", "gpt-4o-mini"
    )
    if generator_model == grader_model:
        raise ValueError("generator and grader models must be different")

    snapshot = (
        SourceDocumentSnapshot.model_validate_json(
            args.snapshot.read_text(encoding="utf-8")
        )
        if args.snapshot is not None
        else _synthetic_public_snapshot()
    )
    selection_config = SelectionConfig()
    prepared = prepare_document_selection(snapshot, selection_config)
    if prepared.selection is None:
        raise RuntimeError("preview snapshot unexpectedly requires relevance selection")
    selection = prepared.selection
    prompt_bundle = build_prompt_bundle(selection_config)

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    # 계약 위반은 확률적이라(20건 x 3회에서 3회 모두 실패한 케이스 0건)
    # 같은 요청을 한 번 더 보내는 것만으로 상당수가 해소된다.
    gateway = RetryingGateway(
        OpenAIResponsesGateway(client),
        max_attempts=args.max_attempts,
    )
    config = PipelineConfig(
        generator_model=generator_model,
        grader_model=grader_model,
        reference_date=date.today(),
    )
    synthetic_generator = PreviewSyntheticGenerator(gateway, generator_model)
    all_cases = preview_cases()
    requested = set(args.case)
    cases = tuple(case for case in all_cases if not requested or case.case_id in requested)
    unknown = requested - {case.case_id for case in all_cases}
    if unknown:
        raise ValueError(f"unknown preview cases: {sorted(unknown)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        args.output_dir / "run_manifest.json",
        {
            "generator_model": generator_model,
            "grader_model": grader_model,
            "source_document_id": snapshot.source_document_id,
            "source_sha256": snapshot.source_sha256,
            "selection_sha256": selection.selection_sha256,
            "prompt_bundle_sha256": prompt_bundle.sha256,
            "cases": [case.case_id for case in cases],
        },
    )

    for index, case in enumerate(cases, start=1):
        case_dir = args.output_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        result_path = case_dir / "pipeline_result.json"
        if args.summary_only:
            print(
                f"[{index}/{len(cases)}] summarize {case.case_id}",
                flush=True,
            )
            continue
        if result_path.exists() and not args.force:
            existing = json.loads(result_path.read_text(encoding="utf-8"))
            if existing.get("failure") is None:
                print(
                    f"[{index}/{len(cases)}] skip completed {case.case_id}",
                    flush=True,
                )
                continue
        print(f"[{index}/{len(cases)}] P1 {case.case_id}", flush=True)
        _write_json(case_dir / "target.json", case.target.model_dump(mode="json"))
        pass1_prompt = prompt_bundle.definition("pass1")
        (case_dir / "p1_system_prompt.txt").write_text(
            pass1_prompt.system_prompt, encoding="utf-8"
        )
        (case_dir / "p1_user_prompt.txt").write_text(
            render_pass1_user_prompt(
                render_selected_source(
                    snapshot,
                    selection,
                    selection_config,
                ),
                generation_plan=_generation_plan_json(case.target),
                assessment_scope=_assessment_scope(selection).value,
                sensitive_seed=case.sensitive_seed,
            ),
            encoding="utf-8",
        )

        cached = None if args.force else _load_p1(case_dir)
        if cached is None:
            p1 = execute_pass1(
                snapshot=snapshot,
                selection=selection,
                counterfactual_target=case.target,
                gateway=gateway,
                config=config,
                selection_config=selection_config,
                prompt_bundle=prompt_bundle,
                sensitive_seed=case.sensitive_seed,
                fully_synthetic_generator=synthetic_generator,
                fully_synthetic_context=FullySyntheticContext(
                    scenario_id=f"preview-{case.case_id}",
                    ordering_agency="가상행정기관",
                    production_date="2026-07-28",
                ),
            )
            _write_json(
                case_dir / "p1_stage.json",
                {
                    "result": (
                        p1.result.model_dump(
                            mode="json",
                            exclude_computed_fields=True,
                        )
                        if p1.result
                        else None
                    ),
                    "receipt": (
                        p1.receipt.model_dump(mode="json") if p1.receipt else None
                    ),
                    "provenance": (
                        p1.provenance.model_dump(mode="json")
                        if p1.provenance
                        else None
                    ),
                    "failure": (
                        p1.failure.model_dump(mode="json") if p1.failure else None
                    ),
                    "synthetic_generator_prompt": synthetic_generator.last_prompt,
                    "synthetic_generator_response_id": (
                        synthetic_generator.last_response_id
                    ),
                },
            )
            if p1.failure is not None:
                _write_json(
                    case_dir / "content_quality.json",
                    {
                        "passed": False,
                        "required_content_markers": list(
                            case.required_content_markers
                        ),
                        "issues": [
                            (
                                "pass1 failed before content quality check: "
                                f"{p1.failure.code.value}"
                            )
                        ],
                        "repairs": [],
                    },
                )
                _write_json(
                    result_path,
                    {
                        "source_document_id": snapshot.source_document_id,
                        "pass1_result": (
                            p1.result.model_dump(mode="json") if p1.result else None
                        ),
                        "pass2_assessment": None,
                        "comparison": None,
                        "failure": p1.failure.model_dump(mode="json"),
                    },
                )
                continue
            assert p1.result and p1.receipt and p1.provenance
            p1_result, p1_receipt, provenance = (
                p1.result,
                p1.receipt,
                p1.provenance,
            )
        else:
            p1_result, p1_receipt, provenance = cached

        generated = p1_result.generated_document
        original_generated = generated
        content_quality_issues = _content_quality_issues(case, generated)
        quality_repairs: list[dict[str, object]] = []
        for repair_attempt in range(1, args.max_quality_repairs + 1):
            if not content_quality_issues:
                break
            try:
                generated, repair_record = _repair_generated_document(
                    case=case,
                    document=generated,
                    issues=content_quality_issues,
                    gateway=gateway,
                    model=generator_model,
                )
            except StructuredCallError as exc:
                quality_repairs.append(
                    {
                        "attempt": repair_attempt,
                        "failure_code": exc.code.value,
                        "failure_message": str(exc),
                    }
                )
                break
            content_quality_issues = _content_quality_issues(case, generated)
            quality_repairs.append(
                {
                    "attempt": repair_attempt,
                    **repair_record,
                    "remaining_issues": list(content_quality_issues),
                }
            )
        if generated != original_generated:
            _write_json(
                case_dir / "generated_ir_before_quality_repair.json",
                original_generated.model_dump(mode="json"),
            )
            p1_result = p1_result.model_copy(
                update={"generated_document": generated}
            )
            p1_stage_path = case_dir / "p1_stage.json"
            p1_stage_payload = json.loads(
                p1_stage_path.read_text(encoding="utf-8")
            )
            p1_stage_payload["result"] = p1_result.model_dump(
                mode="json",
                exclude_computed_fields=True,
            )
            p1_stage_payload["quality_repairs"] = quality_repairs
            _write_json(p1_stage_path, p1_stage_payload)
        _write_json(
            case_dir / "content_quality.json",
            {
                "passed": not content_quality_issues,
                "required_content_markers": list(case.required_content_markers),
                "issues": list(content_quality_issues),
                "repairs": quality_repairs,
            },
        )
        (case_dir / "generated_body.txt").write_text(
            f"{generated.title}\n\n{generated.body_text}\n", encoding="utf-8"
        )
        _write_json(
            case_dir / "generated_ir.json",
            generated.model_dump(mode="json"),
        )
        pass2_prompt = prompt_bundle.definition("pass2")
        (case_dir / "p2_system_prompt.txt").write_text(
            pass2_prompt.system_prompt, encoding="utf-8"
        )
        (case_dir / "p2_user_prompt.txt").write_text(
            render_pass2_user_prompt(
                generated.model_dump_json(exclude_computed_fields=True)
            ),
            encoding="utf-8",
        )

        print(f"[{index}/{len(cases)}] P2 {case.case_id}", flush=True)
        p2 = execute_pass2(
            pass1_result=p1_result,
            gateway=gateway,
            config=config,
            selection_config=selection_config,
            prompt_bundle=prompt_bundle,
        )
        _write_json(
            case_dir / "p2_stage.json",
            {
                "assessment": (
                    p2.assessment.model_dump(mode="json") if p2.assessment else None
                ),
                "receipt": (
                    p2.receipt.model_dump(mode="json") if p2.receipt else None
                ),
                "comparison": (
                    p2.comparison.model_dump(mode="json") if p2.comparison else None
                ),
                "failure": (
                    p2.failure.model_dump(mode="json") if p2.failure else None
                ),
            },
        )
        _write_json(
            result_path,
            {
                "source_document_id": snapshot.source_document_id,
                "pass1_result": p1_result.model_dump(mode="json"),
                "pass1_receipt": p1_receipt.model_dump(mode="json"),
                "generation_provenance": provenance.model_dump(mode="json"),
                "pass2_assessment": (
                    p2.assessment.model_dump(mode="json") if p2.assessment else None
                ),
                "pass2_receipt": (
                    p2.receipt.model_dump(mode="json") if p2.receipt else None
                ),
                "comparison": (
                    p2.comparison.model_dump(mode="json") if p2.comparison else None
                ),
                "failure": (
                    p2.failure.model_dump(mode="json") if p2.failure else None
                ),
            },
        )

    summaries = [
        _case_summary(case, args.output_dir / case.case_id) for case in cases
    ]
    fieldnames = sorted({key for row in summaries for key in row})
    with (args.output_dir / "preview_summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summaries)
    with (args.output_dir / "preview_records.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        for row in summaries:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    ok_count = sum(row.get("status") == "ok" for row in summaries)
    quality_count = sum(
        row.get("content_quality_pass") == "True" for row in summaries
    )
    print(
        (
            f"completed: {ok_count}/{len(summaries)}, "
            f"content-quality: {quality_count}/{len(summaries)} "
            f"-> {args.output_dir.resolve()}"
        ),
        flush=True,
    )
    return (
        0
        if ok_count == len(summaries) and quality_count == len(summaries)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
