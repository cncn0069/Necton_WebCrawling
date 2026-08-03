"""실제 원문 N건을 원본문서 기반 생성 파이프라인에 통과시키고 전 과정을 남긴다.

PDF 렌더링 **직전**까지 — 유형 판별, 생성, blind 정합성 검사 — 를 수행하고,
각 문서마다 다음을 모두 기록해 사람이 눈으로 확인할 수 있게 한다.

    원문(제목·본문 일부, 수집 시점 doc_type)
    -> 유형 판별기가 확인한 것 (문서유형, S/O, 조항·세부조항, 근거, 적합성)
    -> 결정론적 플래너가 잠근 것 (generation route, 최종 target)
    -> 생성된 문서 (제목, block 구성, 본문)
    -> validator가 독립 판정한 것 (문서유형, S/O, 조항·세부조항)
    -> 두 판정의 일치 여부

판별·계획과 blind 검사가 어디서 갈라지는지가 이 산출물의 핵심이다. 특히
**수집 시점 doc_type / 판별 유형 / validator 유형** 셋을 나란히 두면, 불일치가
모델 오류인지 라벨 정의 차이인지 구분할 단서가 된다.

입력은 프로덕션 DB의 공개(O) 문서다 — 실제 C/S 문서는 비공개라 본문을 갖고
있지 않다(의도된 동작). 따라서 target은 counterfactual로 요청되며, 코드가
판별 결과와 실행 조건을 조합해 route와 최종 target을 잠근다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymysql  # noqa: E402
from openai import OpenAI  # noqa: E402

from rd2.administrative_status import (  # noqa: E402
    ADMIN_STATUS_TEXT_POLICIES,
    AdminStatus,
)
from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
    SUBCLAUSE_LABELS,
    SemanticDocumentType,
    expected_classification,
)
from rd2.source_generation.contracts import (  # noqa: E402
    GeneratedDocumentIR,
    GenerationMode,
    GenerationTarget,
    SourceDocumentSnapshot,
    TargetClassification,
    effective_classification,
)
from rd2.source_generation.document_form import check_document_form  # noqa: E402
from rd2.source_generation.document_select import (  # noqa: E402
    SelectionConfig,
    prepare_document_selection,
)
from rd2.source_generation.legacy_synthetic import (  # noqa: E402
    FullySyntheticContext,
    FullySyntheticDocumentGenerator,
)
from rd2.source_generation.pipeline import (  # noqa: E402
    OpenAIResponsesGateway,
    PipelineConfig,
    RetryingGateway,
    run_three_stage_pipeline,
)
from rd2.source_generation.prompts import build_prompt_bundle  # noqa: E402

BLOCKS_PER_PAGE = 12


class BatchSyntheticGenerator(FullySyntheticDocumentGenerator):
    """플래너가 ``fully_synthetic``을 고를 때 원문 없이 본문을 만드는 실행기.

    이 경로가 없으면 계획을 실행할 수 없다. 기존 실측에서는 공개 원문이
    counterfactual 목표를 지지하지 못해 ``fully_synthetic``이 자주 필요했다 —
    공개 원문에 counterfactual C/S 목표를 지지하는 근거가 없을 때 억지로
    맞추지 않고 물러서는 것이 설계된 동작이기 때문이다.

    입력은 최종 target과 시나리오 컨텍스트뿐이다. 원문 block·인용·근거는
    전달하지 않는다.
    """

    def __init__(self, gateway, model: str) -> None:
        self._gateway = gateway
        self._model = model

    def generate(
        self,
        *,
        target: GenerationTarget,
        context: FullySyntheticContext,
    ) -> GeneratedDocumentIR:
        payload = {
            "instruction": (
                "원문을 사용하지 않는 완전 합성 대한민국 공공문서를 작성하라. "
                "상태명이나 정답용 고정 문구를 억지로 쓰지 말고 상황과 문맥으로 "
                "드러내라. 허용된 5개 block 종류만 사용하라."
            ),
            "classification": target.classification.value,
            "clause_no": target.clause_no.value if target.clause_no else "",
            "subclause_key": (
                target.subclause_key.value if target.subclause_key else ""
            ),
            "subclause_label": (
                SUBCLAUSE_LABELS[target.subclause_key] if target.subclause_key else ""
            ),
            "administrative_status_context": [
                ADMIN_STATUS_TEXT_POLICIES[status].detail
                for status in target.administrative_statuses
            ],
            "scenario_id": context.scenario_id,
            "ordering_agency": context.ordering_agency,
            "production_date": context.production_date,
        }
        call = self._gateway.parse(
            model=self._model,
            system_prompt=(
                "당신은 학습용 합성 공공문서 생성기다. 실존 개인정보·기밀을 쓰지 "
                "않고 입력 목표에 맞는 GeneratedDocumentIR만 반환한다."
            ),
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            response_model=GeneratedDocumentIR,
            max_output_tokens=8_000,
        )
        if not isinstance(call.parsed, GeneratedDocumentIR):
            raise ValueError("synthetic batch generator returned wrong contract")
        return call.parsed


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ["MARIADB_HOST"],
        port=int(os.environ["MARIADB_PORT"]),
        user=os.environ["MARIADB_USER"],
        password=os.environ["MARIADB_PASSWORD"],
        database=os.environ["MARIADB_DATABASE"],
        charset="utf8mb4",
    )


#: 개행 없는 본문을 자를 자리. 공문에서 실제로 문단이 갈리는 지점이다 —
#: 번호 항목("1. ", "2. ") 앞, 경어체 종결("~니다.") 뒤, 붙임·끝 표지 앞.
_RUN_ON_SPLIT = re.compile(
    r"(?<=니다\.)\s+"      # 문장 종결 뒤
    r"|(?=\s\d+\.\s)"      # 번호 항목 앞
    r"|(?=\s붙임)"          # 붙임 앞
    r"|(?=\s끝\.)"          # 끝 표지 앞
)

#: 위 경계로도 안 갈리는 덩어리를 강제로 자르는 상한.
MAX_BLOCK_CHARS = 300

#: 이보다 짧은 조각은 앞 block에 도로 붙인다.
#:
#: 경계 규칙이 문맥을 못 보기 때문에 필요하다 — 날짜 "2026. 7. 20."의 " 7. "이
#: 번호 항목으로, 문장 중간의 "붙임과 같이"가 붙임 표지로 잘못 걸린다. 규칙을
#: 더 정교하게 만드는 대신 결과를 정리한다: 홀로 서지 못하는 조각은 근거로
#: 인용할 수도 없으므로 block으로 남길 이유가 없다.
MIN_BLOCK_CHARS = 20


def _split_run_on_text(text: str) -> list[str]:
    """개행이 하나도 없는 본문을 문단 단위로 나눈다.

    실측(2026-08-01, seoul_opengov-18752): 본문 797자에 개행이 0개라 문서 전체가
    ``p1:b0`` 한 덩어리가 됐다. evidence 인용은 **그 block 안에서 유일해야**
    하는데(``EvidenceSpan.locate_in``), 한 덩어리 안에 "광운대역 물류부지
    개발사업"이 3번, "품질지도과"가 3번 나와 어떤 인용을 골라도 ambiguous가
    되기 쉬웠다. 실제로 5회 실행 중 2회가 이 문서의 판별 단계에서
    ``evidence_invalid``로 죽었다.

    개행이 있는 본문은 기존 두 분기가 그대로 처리하므로 이 경로를 타지 않는다.
    """

    chunks = [chunk.strip() for chunk in _RUN_ON_SPLIT.split(text) if chunk.strip()]
    blocks: list[str] = []
    for chunk in chunks:
        # 경계가 없는 긴 덩어리는 상한으로 자른다. 자르는 자리를 공백으로 맞춰
        # 낱말이 두 block에 걸치지 않게 한다.
        while len(chunk) > MAX_BLOCK_CHARS:
            cut = chunk.rfind(" ", 0, MAX_BLOCK_CHARS)
            if cut <= 0:
                cut = MAX_BLOCK_CHARS
            blocks.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            blocks.append(chunk)

    merged: list[str] = []
    for block in blocks:
        if merged and len(block) < MIN_BLOCK_CHARS:
            merged[-1] = f"{merged[-1]} {block}"
        else:
            merged.append(block)
    # 첫 조각이 짧으면 앞이 없으므로 뒤와 합친다.
    if len(merged) > 1 and len(merged[0]) < MIN_BLOCK_CHARS:
        merged[1] = f"{merged[0]} {merged[1]}"
        merged.pop(0)
    return merged


def _snapshot_from_body(document_id: str, source: str, body: str) -> SourceDocumentSnapshot | None:
    blocks = [chunk.strip() for chunk in body.split("\n\n") if chunk.strip()]
    if len(blocks) <= 1:
        blocks = [line.strip() for line in body.splitlines() if line.strip()]
    if len(blocks) <= 1:
        blocks = _split_run_on_text(body)
    if not blocks:
        return None
    pages = []
    for offset in range(0, len(blocks), BLOCKS_PER_PAGE):
        page_number = offset // BLOCKS_PER_PAGE + 1
        pages.append(
            {
                "page_number": page_number,
                "blocks": [
                    {"block_id": f"p{page_number}:b{index}", "text": text}
                    for index, text in enumerate(blocks[offset : offset + BLOCKS_PER_PAGE])
                ],
            }
        )
    return SourceDocumentSnapshot.model_validate(
        {
            "source_document_id": document_id,
            "source": source,
            "manifest_key": "source-generation-batch",
            "source_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "pages": pages,
        }
    )


def _target_cycle() -> list[GenerationTarget]:
    """민감(S) 세부조항 + 행정상태 단독 12개를 고르게 도는 target 목록.

    **왜 S만인가.** 이 파이프라인은 제5~8호만 다룬다 — ``SourceAssessment``가 C를
    계약 단계에서 거부하고, 생성기가 받는 taxonomy도 제5~8호뿐이다. 그래서 C 목표를
    요청하면 실패하지 않고 **계획기가 판별기의 1순위 호환 세부조항으로 조용히
    대체한다.** 실측에서 이게 측정을 망쳤다: 목록이 조항 번호 순이고 배정이 위치
    기반(``targets[(index - 1) % len(targets)]``)이라, 10건을 돌리면 앞 8칸이 전부
    C트랙이어서 8건이 대체 경로만 검증했고 요청 목표는 한 번도 구현되지 않았다.

    조항 번호를 박아 넣지 않고 ``expected_classification``으로 걸러낸다 — taxonomy가
    바뀌면 목록이 따라온다.

    기밀(C) 생성을 붙일 때는 이 필터를 푸는 것만으로는 안 된다. 계약과 생성기
    taxonomy가 제1~4호를 받아들이게 먼저 고쳐야 한다.
    """

    targets: list[GenerationTarget] = []
    for clause in ClauseNumber:
        classification = TargetClassification(expected_classification(clause).value)
        if classification is not TargetClassification.S:
            continue
        for subclause in sorted(SUBCLAUSES_BY_CLAUSE[clause], key=lambda item: item.value):
            targets.append(
                GenerationTarget(
                    classification=classification,
                    clause_no=clause,
                    subclause_key=subclause,
                    generation_mode=GenerationMode.COUNTERFACTUAL,
                )
            )
    for status in AdminStatus:
        targets.append(
            GenerationTarget(
                classification=TargetClassification.S,
                administrative_statuses=(status,),
                generation_mode=GenerationMode.COUNTERFACTUAL,
            )
        )
    return targets


def _fetch_documents(
    cursor,
    *,
    count: int,
    min_chars: int,
    max_chars: int,
    source: str | None = None,
) -> list[tuple]:
    where = [
        "cso_classification = 'O'",
        "body_text IS NOT NULL",
        "CHAR_LENGTH(body_text) BETWEEN %s AND %s",
    ]
    params: list = [min_chars, max_chars]
    if source is not None:
        where.append("source = %s")
        params.append(source)
    cursor.execute(
        f"""
        SELECT id, source, doc_type, title, body_text
        FROM documents
        WHERE {' AND '.join(where)}
        ORDER BY id
        """,
        params,
    )
    known = {item.value for item in SemanticDocumentType}
    rows, per_type = [], {}
    for row in cursor.fetchall():
        doc_type = row[2]
        if doc_type not in known:
            continue
        # 한 유형이 표본을 독식하지 않게 고르게 뽑는다 — 특정 source로 좁힌
        # 경우는 그 source가 doc_type 한둘뿐일 수 있어(예: seoul_opengov는
        # official_document 하나뿐) 이 cap을 그대로 적용하면 count를 채우지
        # 못하고 조용히 적게 반환한다. source를 지정했다면 다양성보다
        # 요청한 건수를 우선한다.
        if source is None:
            cap = max(1, count // 6)
            if per_type.get(doc_type, 0) >= cap:
                continue
            per_type[doc_type] = per_type.get(doc_type, 0) + 1
        rows.append(row)
        if len(rows) >= count:
            break
    return rows


def _record(row, target, result, snapshot) -> dict:
    row_id, source, doc_type, title, body = row
    assessment = result.source_assessment
    plan = result.generation_plan
    generation = result.generation_artifact
    validation = result.consistency_assessment
    record: dict = {
        "source_document_id": snapshot.source_document_id,
        "source": source,
        "collected_doc_type": doc_type,
        "source_title": title,
        "source_excerpt": body[:400],
        "source_block_count": sum(len(page.blocks) for page in snapshot.pages),
        "requested_target": target.model_dump(mode="json"),
        "succeeded": result.succeeded,
    }
    if result.failure is not None:
        record["failure_stage"] = result.failure.stage.value
        record["failure_code"] = result.failure.code.value
        record["failure_message"] = result.failure.message
    if assessment is not None:
        source_cls = assessment.source_classification
        suitability = assessment.source_suitability
        record.update(
            {
                "source_document_form": source_cls.document_form.value,
                "source_classification": source_cls.classification.value,
                "source_clause": (
                    source_cls.clause_no.value if source_cls.clause_no else None
                ),
                "source_subclause": (
                    source_cls.subclause_key.value if source_cls.subclause_key else None
                ),
                "source_rationale": source_cls.rationale,
                "source_evidence_level": suitability.evidence_level.value,
                "source_evidence_quotes": [
                    span.quote for span in suitability.evidence_spans
                ],
                "source_reason_code": suitability.reason_code,
                "source_business_context": assessment.business_context,
                "source_subject_roles": [
                    role.value for role in assessment.subject_roles
                ],
                "primary_subclause": assessment.primary_subclause.value,
                "primary_rationale": assessment.primary_rationale,
                "compatible_subclauses": [
                    item.value for item in assessment.compatible_subclauses
                ],
            }
        )
    if plan is not None:
        record.update(
            {
                "generation_route": plan.generation_route.value,
                "generation_final_target": plan.final_target.model_dump(
                    mode="json"
                ),
                "source_assessment_sha256": plan.source_assessment_sha256,
            }
        )
    if generation is not None:
        document = generation.generated_document
        record.update(
            {
                "generated_title": document.title,
                "generated_blocks": [
                    block.kind for block in document.blocks
                ],
                "generated_body": document.body_text,
                "generation_attempt_index": generation.attempt_index,
                "generation_repair_codes": [
                    code.value for code in generation.repair_codes
                ],
            }
        )
        form = check_document_form(
            document,
            plan.final_target,
            document_form=(
                assessment.source_classification.document_form
                if assessment is not None
                else None
            ),
        )
        record["form_has_header"] = form.has_header
        record["form_has_approval"] = form.has_approval_block
        record["form_has_attachment"] = form.has_attachment_block
        record["form_missing"] = list(form.missing)
    if validation is not None:
        effective = effective_classification(
            validation.classification,
            plan.final_target.administrative_statuses if plan is not None else (),
        )
        record.update(
            {
                "validation_document_form": validation.document_form.value,
                "validation_classification": validation.classification.value,
                "validation_clause": (
                    validation.clause_no.value if validation.clause_no else None
                ),
                "validation_subclause": (
                    validation.subclause_key.value
                    if validation.subclause_key
                    else None
                ),
                "validation_effective_classification": effective.value,
                "validation_evidence_quotes": [
                    span.quote for span in validation.evidence_spans
                ],
                "validation_rationale": validation.rationale,
                # O로 끝난 건이 무엇 때문에 미끄러졌는지. 세부유형별로 모아
                # 생성 규칙을 어디부터 고칠지 정하는 데 쓴다(진단 전용 —
                # 재생성 입력으로 되먹이지 않는다).
                "validation_near_miss": [
                    {
                        "subclause_key": note.subclause_key.value,
                        "missing": note.missing,
                        "block_id": note.block_id,
                    }
                    for note in getattr(validation, "near_miss", ())
                ],
            }
        )
    if result.comparison is not None:
        record["comparison"] = result.comparison.model_dump(mode="json")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--min-chars", type=int, default=800)
    parser.add_argument("--max-chars", type=int, default=6_000)
    parser.add_argument(
        "--source",
        default=None,
        help="documents.source로 필터링한다(예: seoul_opengov). 지정하지 않으면 전체 출처.",
    )
    parser.add_argument("--classifier-model", default="gpt-4o")
    parser.add_argument("--generator-model", default="gpt-4o")
    parser.add_argument("--validator-model", default="gpt-4o-mini")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    connection = _connect()
    rows = _fetch_documents(
        connection.cursor(),
        count=args.count,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        source=args.source,
    )
    print(f"원문 {len(rows)}건 확보")

    gateway = RetryingGateway(
        OpenAIResponsesGateway(OpenAI(api_key=os.environ["OPENAI_API_KEY"])),
        max_attempts=args.max_attempts,
    )
    config = PipelineConfig(
        classifier_model=args.classifier_model,
        generator_model=args.generator_model,
        validator_model=args.validator_model,
        reference_date=date.today(),
    )
    synthetic_generator = BatchSyntheticGenerator(gateway, args.generator_model)
    selection_config = SelectionConfig()
    prompt_bundle = build_prompt_bundle(selection_config)
    targets = _target_cycle()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    records_path = args.out_dir / "batch_records.jsonl"
    # ``batch_records.jsonl``은 사람이 읽는 요약본이라 block 구성 이름만 담고
    # (``generated_blocks``: kind 목록) 실제 내용은 버린다. PDF 렌더러
    # (``scripts/render_generated_documents.py``)는 GeneratedDocumentIR 전체가
    # 필요하므로, 생성 단계까지 도달한 건은 DocumentPipelineResult 전체를 그대로
    # 여기 남긴다. 필드 이름이 이미 렌더러의 ``_renderer_payload`` 투영과
    # 일치하므로(``generation_plan``, ``generation_artifact``,
    # ``generation_receipt``) 별도 변환 없이 그대로 입력으로 쓸 수 있다.
    render_payloads_path = args.out_dir / "render_payloads.jsonl"
    records: list[dict] = []

    with records_path.open("w", encoding="utf-8") as handle, render_payloads_path.open(
        "w", encoding="utf-8"
    ) as render_handle:
        for index, row in enumerate(rows, 1):
            row_id, source, doc_type, title, body = row
            document_id = f"{source}-{row_id}"
            snapshot = _snapshot_from_body(document_id, source, body)
            if snapshot is None:
                continue
            prepared = prepare_document_selection(snapshot, selection_config)
            if prepared.selection is None:
                print(f"[{index}/{len(rows)}] {document_id} 건너뜀 (relevance 필요)")
                continue
            target = targets[(index - 1) % len(targets)]
            print(f"[{index}/{len(rows)}] {document_id} <- {target.classification.value}"
                  f"/{target.clause_no.value if target.clause_no else '-'}"
                  f"/{target.subclause_key.value if target.subclause_key else '-'}")
            result = run_three_stage_pipeline(
                snapshot=snapshot,
                selection=prepared.selection,
                counterfactual_target=target,
                gateway=gateway,
                config=config,
                selection_config=selection_config,
                prompt_bundle=prompt_bundle,
                fully_synthetic_generator=synthetic_generator,
                fully_synthetic_context=FullySyntheticContext(
                    scenario_id=f"batch-{document_id}",
                    ordering_agency="가상행정기관",
                    production_date=date.today().isoformat(),
                ),
            )
            record = _record(row, target, result, snapshot)
            records.append(record)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            if result.generation_artifact is not None:
                render_handle.write(
                    json.dumps(
                        result.model_dump(mode="json"), ensure_ascii=False
                    )
                    + "\n"
                )
                render_handle.flush()

    ok = sum(1 for record in records if record["succeeded"])
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "prompt_bundle": prompt_bundle.version,
        "classifier_prompt_sha256": prompt_bundle.definition(
            "classifier"
        ).sha256,
        "generator_prompt_sha256": prompt_bundle.definition(
            "generator"
        ).sha256,
        "validator_prompt_sha256": prompt_bundle.definition(
            "validator"
        ).sha256,
        "taxonomy_version": prompt_bundle.taxonomy_version,
        "classifier_model": args.classifier_model,
        "generator_model": args.generator_model,
        "validator_model": args.validator_model,
        "max_attempts": args.max_attempts,
        "total": len(records),
        "succeeded": ok,
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    generated = sum(1 for record in records if record.get("generated_body"))
    print(f"\n완료 {ok}/{len(records)} -> {records_path}")
    print(
        f"생성 도달 {generated}건의 렌더링용 원본 -> {render_payloads_path}\n"
        f"PDF로 뽑으려면: python scripts/render_generated_documents.py "
        f"{render_payloads_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
