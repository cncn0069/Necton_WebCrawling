"""실제 원문 N건을 원본문서 기반 생성 파이프라인에 통과시키고 전 과정을 남긴다.

PDF 렌더링 **직전**까지 — 즉 P1 생성과 blind P2 채점까지 — 를 수행하고,
각 문서마다 다음을 모두 기록해 사람이 눈으로 확인할 수 있게 한다.

    원문(제목·본문 일부, 수집 시점 doc_type)
    -> P1이 추론한 것 (문서유형, C/S/O, 조항·세부조항, 근거, 적합성 수준)
    -> P1이 고른 것 (generation route, 최종 target)
    -> 생성된 문서 (제목, block 구성, 본문)
    -> P2가 독립 판정한 것 (문서유형, C/S/O, 조항·세부조항, 행정상태)
    -> 두 판정의 일치 여부

P1과 P2가 같은 문서를 두고 어디서 갈라지는지가 이 산출물의 핵심이다. 특히
**수집 시점 doc_type / P1 추론 유형 / P2 확인 유형** 셋을 나란히 두면, 불일치가
모델 오류인지 라벨 정의 차이인지 구분할 단서가 된다.

입력은 프로덕션 DB의 공개(O) 문서다 — 실제 C/S 문서는 비공개라 본문을 갖고
있지 않다(의도된 동작). 따라서 target은 counterfactual로 제안되며, P1이 원문
근거를 보고 route와 최종 target을 스스로 정한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymysql  # noqa: E402
from openai import OpenAI  # noqa: E402

from rd2.administrative_status import AdminStatus  # noqa: E402
from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
    SemanticDocumentType,
    expected_classification,
)
from rd2.source_generation.contracts import (  # noqa: E402
    GenerationMode,
    GenerationTarget,
    SourceDocumentSnapshot,
    TargetClassification,
)
from rd2.source_generation.document_select import (  # noqa: E402
    SelectionConfig,
    prepare_document_selection,
)
from rd2.source_generation.pipeline import (  # noqa: E402
    OpenAIResponsesGateway,
    PipelineConfig,
    RetryingGateway,
    run_two_pass,
)
from rd2.source_generation.prompts import build_prompt_bundle  # noqa: E402

BLOCKS_PER_PAGE = 12


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ["MARIADB_HOST"],
        port=int(os.environ["MARIADB_PORT"]),
        user=os.environ["MARIADB_USER"],
        password=os.environ["MARIADB_PASSWORD"],
        database=os.environ["MARIADB_DATABASE"],
        charset="utf8mb4",
    )


def _snapshot_from_body(document_id: str, source: str, body: str) -> SourceDocumentSnapshot | None:
    blocks = [chunk.strip() for chunk in body.split("\n\n") if chunk.strip()]
    if len(blocks) <= 1:
        blocks = [line.strip() for line in body.splitlines() if line.strip()]
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
    """24개 세부조항 + 행정상태 단독 12개를 고르게 도는 target 목록."""

    targets: list[GenerationTarget] = []
    for clause in ClauseNumber:
        for subclause in sorted(SUBCLAUSES_BY_CLAUSE[clause], key=lambda item: item.value):
            targets.append(
                GenerationTarget(
                    classification=TargetClassification(
                        expected_classification(clause).value
                    ),
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


def _fetch_documents(cursor, *, count: int, min_chars: int, max_chars: int) -> list[tuple]:
    cursor.execute(
        """
        SELECT id, source, doc_type, title, body_text
        FROM documents
        WHERE cso_classification = 'O'
          AND body_text IS NOT NULL
          AND CHAR_LENGTH(body_text) BETWEEN %s AND %s
        ORDER BY id
        """,
        (min_chars, max_chars),
    )
    known = {item.value for item in SemanticDocumentType}
    rows, per_type = [], {}
    for row in cursor.fetchall():
        doc_type = row[2]
        if doc_type not in known:
            continue
        # 한 유형이 표본을 독식하지 않게 고르게 뽑는다.
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
    pass1 = result.pass1_result
    pass2 = result.pass2_assessment
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
    if pass1 is not None:
        source_cls = pass1.source_classification
        suitability = pass1.source_suitability
        record.update(
            {
                "p1_source_document_type": source_cls.document_type.value,
                "p1_source_classification": source_cls.classification.value,
                "p1_source_clause": source_cls.clause_no.value if source_cls.clause_no else None,
                "p1_source_subclause": (
                    source_cls.subclause_key.value if source_cls.subclause_key else None
                ),
                "p1_source_rationale": source_cls.rationale,
                "p1_evidence_level": suitability.evidence_level.value,
                "p1_evidence_quotes": [span.quote for span in suitability.evidence_spans],
                "p1_reason_code": suitability.reason_code,
                "p1_route": pass1.generation_route.value,
                "p1_final_target": pass1.generation_target.model_dump(mode="json"),
                "generated_title": pass1.generated_document.title,
                "generated_blocks": [
                    block.kind for block in pass1.generated_document.blocks
                ],
                "generated_body": pass1.generated_document.body_text,
            }
        )
    if pass2 is not None:
        record.update(
            {
                "p2_document_type": pass2.document_type.value,
                "p2_classification": pass2.classification.value,
                "p2_clause": pass2.clause_no.value if pass2.clause_no else None,
                "p2_subclause": pass2.subclause_key.value if pass2.subclause_key else None,
                "p2_admin_statuses": [
                    finding.status.value for finding in pass2.administrative_statuses
                ],
                "p2_effective_classification": pass2.effective_classification.value,
                "p2_evidence_quotes": [span.quote for span in pass2.evidence_spans],
                "p2_rationale": pass2.rationale,
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
    parser.add_argument("--generator-model", default="gpt-4o")
    parser.add_argument("--grader-model", default="gpt-4o-mini")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    connection = _connect()
    rows = _fetch_documents(
        connection.cursor(),
        count=args.count,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
    )
    print(f"원문 {len(rows)}건 확보")

    gateway = RetryingGateway(
        OpenAIResponsesGateway(OpenAI(api_key=os.environ["OPENAI_API_KEY"])),
        max_attempts=args.max_attempts,
    )
    config = PipelineConfig(
        generator_model=args.generator_model,
        grader_model=args.grader_model,
        reference_date=date.today(),
    )
    selection_config = SelectionConfig()
    prompt_bundle = build_prompt_bundle(selection_config)
    targets = _target_cycle()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    records_path = args.out_dir / "batch_records.jsonl"
    records: list[dict] = []

    with records_path.open("w", encoding="utf-8") as handle:
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
            result = run_two_pass(
                snapshot=snapshot,
                selection=prepared.selection,
                counterfactual_target=target,
                gateway=gateway,
                config=config,
                selection_config=selection_config,
                prompt_bundle=prompt_bundle,
            )
            record = _record(row, target, result, snapshot)
            records.append(record)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    ok = sum(1 for record in records if record["succeeded"])
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "prompt_bundle": prompt_bundle.version,
        "prompt_bundle_sha256": prompt_bundle.sha256,
        "taxonomy_version": prompt_bundle.taxonomy_version,
        "generator_model": args.generator_model,
        "grader_model": args.grader_model,
        "max_attempts": args.max_attempts,
        "total": len(records),
        "succeeded": ok,
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n완료 {ok}/{len(records)} -> {records_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
