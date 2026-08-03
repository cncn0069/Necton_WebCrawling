"""배치가 남긴 ``batch_records.jsonl``을 RDS ``documents`` 행으로 적재한다.

**왜 별도 스크립트인가.** 생성은 문서당 LLM을 3~4회 부르므로 다시 돌리는 비용이
크다. 배치가 그 결과를 통째로(``generation_plan``·``generation_artifact``·
``consistency_assessment``) JSONL에 남기므로, DB를 어디로 할지 나중에 정해도
재생성 없이 그 파일에서 바로 적재할 수 있다. 배치를 ``--commit-to-rds`` 없이
돌린 경우와, 템플릿이 완성돼 PDF 경로를 채워 넣는 경우가 이 스크립트의 용도다.

메타데이터(기관·부서·생산일자)는 JSONL에 없다 — 원문 RDS 행에서 물려받는
값이라 기록하지 않았다. 대신 ``source_row_id``가 남아 있어 여기서 원문 행을
다시 읽는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from rd2.source_generation.classification_taxonomy import DocumentForm  # noqa: E402
from rd2.source_generation.contracts import (  # noqa: E402
    GeneratedDocumentIR,
    GenerationPlan,
    SensitiveConsistencyAssessment,
    SensitivePipelineStatus,
)
from rd2.source_generation.rds_writeback import (  # noqa: E402
    SourceRow,
    build_generated_document,
    should_commit,
)
from rd2.storage.db import DocumentStore  # noqa: E402
from scripts.run_seoul_official_batch import (  # noqa: E402
    _RDS_COLUMNS,
    _connect_rds,
)

load_dotenv(ROOT / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_records(paths: list[Path]) -> list[dict]:
    records: list[dict] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def fetch_source_rows(connection, row_ids: list[int]) -> dict[int, SourceRow]:
    """원문 행을 id로 다시 읽는다. 없는 id는 빠진 채로 돌아온다."""

    if not row_ids:
        return {}
    placeholders = ", ".join("%s" for _ in row_ids)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(_RDS_COLUMNS)} FROM documents "
            f"WHERE id IN ({placeholders})",
            row_ids,
        )
        rows = cursor.fetchall()
    result = {}
    for row in rows:
        source_row = SourceRow(**dict(zip(_RDS_COLUMNS, row)))
        result[source_row.id] = source_row
    return result


def _document_form(record: dict) -> DocumentForm | None:
    raw = record.get("source_document_form")
    if not raw:
        return None
    try:
        return DocumentForm(raw)
    except ValueError:
        return None


def build_rows(
    records: list[dict],
    source_rows: dict[int, SourceRow],
    *,
    include_weak_mask_restoration: bool = False,
    fallback_source: str | None = None,
) -> tuple[list, list[dict]]:
    """적재 대상 Document와, 제외된 레코드의 사유를 함께 낸다."""

    documents = []
    skipped: list[dict] = []
    for record in records:
        approval = record.get("approval_status")
        if approval != SensitivePipelineStatus.ACCEPTED_S.value:
            skipped.append({"id": record.get("source_document_id"), "reason": approval})
            continue
        raw_plan = record.get("generation_plan")
        raw_artifact = record.get("generation_artifact")
        if not raw_plan or not raw_artifact:
            skipped.append(
                {"id": record.get("source_document_id"), "reason": "artifact_missing"}
            )
            continue
        plan = GenerationPlan.model_validate(raw_plan)
        document = GeneratedDocumentIR.model_validate(
            raw_artifact["generated_document"]
        )
        raw_assessment = record.get("consistency_assessment")
        assessment = (
            SensitiveConsistencyAssessment.model_validate(raw_assessment)
            if raw_assessment
            else None
        )
        if not should_commit(
            status=SensitivePipelineStatus.ACCEPTED_S,
            plan=plan,
            assessment=assessment,
            include_weak_mask_restoration=include_weak_mask_restoration,
        ):
            skipped.append(
                {
                    "id": record.get("source_document_id"),
                    "reason": "weak_mask_restoration",
                }
            )
            continue
        row_id = record.get("source_row_id")
        source_row = source_rows.get(row_id) if row_id is not None else None
        documents.append(
            build_generated_document(
                document=document,
                plan=plan,
                source_document_id=str(record["source_document_id"]),
                source_row=source_row,
                fallback_source=fallback_source,
                document_form=_document_form(record),
                assessment=assessment,
            )
        )
    return documents, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "records",
        type=Path,
        nargs="+",
        help="batch_records.jsonl (여러 배치를 한 번에 줄 수 있다)",
    )
    parser.add_argument(
        "--rds-database",
        default=None,
        help="적재할 DB. 생략하면 .env의 MARIADB_DATABASE",
    )
    parser.add_argument(
        "--fallback-source",
        default=None,
        help="원문 행을 못 찾았을 때 쓸 출처 이름(로컬 파일 입력으로 돈 배치)",
    )
    parser.add_argument(
        "--include-weak-mask-restoration",
        action="store_true",
        help="검증기가 O를 낸 mask_restoration 결과도 넣는다",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB에 쓰지 않고 몇 건이 들어갈지만 센다",
    )
    args = parser.parse_args()

    records = load_records(args.records)
    print(f"레코드 {len(records)}건")

    row_ids = sorted(
        {
            record["source_row_id"]
            for record in records
            if record.get("source_row_id") is not None
        }
    )
    connection = _connect_rds(args.rds_database)
    try:
        source_rows = fetch_source_rows(connection, row_ids)
    finally:
        connection.close()
    print(f"원문 행 {len(source_rows)}/{len(row_ids)}건 조회")

    documents, skipped = build_rows(
        records,
        source_rows,
        include_weak_mask_restoration=args.include_weak_mask_restoration,
        fallback_source=args.fallback_source,
    )
    print(f"적재 대상 {len(documents)}건 / 제외 {len(skipped)}건")

    if args.dry_run:
        for document in documents[:5]:
            print(f"  {document.source} | {document.cso_sub_clause} | {document.title}")
        return 0

    inserted = duplicate = failed = 0
    with DocumentStore(database=args.rds_database) as store:
        for document in documents:
            try:
                if store.upsert(document):
                    inserted += 1
                else:
                    duplicate += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  [실패] {document.source_url}: {exc}")
    print(f"신규 {inserted} / 중복스킵 {duplicate} / 실패 {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
