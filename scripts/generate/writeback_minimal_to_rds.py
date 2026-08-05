"""최소 프롬프트 루트의 생성물을 ``documents`` 테이블 행으로 되쓴다.

``run_minimal_doc_type_batch``는 판별 -> 생성까지만 하고 파일로 끝난다. 그
산출물을 학습 코퍼스와 같은 테이블에 넣는 것이 이 스크립트다.

    documents/*.json
        -> build_generated_document(target=판별기 세부조항, route=source_aligned)
        -> DocumentStore.upsert   (generated_yn='1', body_file_path만 비어 있음)
    render_minimal_pdf
        -> DocumentStore.set_body_file_path  (--fill-pdf-path)

**행은 PDF보다 먼저 넣는다.** 행에 들어갈 값 — 본문(generated_text)·입력 원문
(content)·프롬프트(input_prompt)·참조 원문(ref_id)·기관/부서/생산일자 — 은 전부
생성 시점에 이미 손에 있다. 렌더는 그중 어느 값도 만들지 않고 ``body_file_path``
하나만 더한다. 렌더 뒤로 미루면 템플릿 조립이 깨진 문서는 무엇을 만들었는지조차
DB에 남지 않는데, 그 실패는 본문의 문제가 아니라 서식의 문제다.

그래서 자르는 자리도 옮긴다. 예전에는 렌더 성공이 게이트였다(검증기가 없는
경로라 쥘 수 있는 사실이 그것뿐이었다). 지금은 게이트를 두지 않고, 렌더가 안 된
문서는 ``body_file_path``가 NULL로 남아 그 자체로 식별된다:

    SELECT id, title FROM documents
     WHERE generated_yn = '1' AND (body_file_path IS NULL OR body_file_path = '')

**목표는 판별기가 준 세부조항으로 고정한다.** 요청 목표도 갈아타기도 없다
(``minimal_envelope.minimal_generation_target``).

기본값은 쓰지 않는다. ``--execute`` 없이 돌리면 어떤 행이 만들어지는지(또는 어느
행에 어떤 경로가 들어갈지)만 보여 주고 DB를 건드리지 않는다.

사용:
    # 1) 만들어질 행 확인만
    python scripts/writeback_minimal_to_rds.py --records output/minimal_doctype_20260804

    # 2) 행 삽입 — 렌더 전 (RDS는 터널 경유)
    python scripts/writeback_minimal_to_rds.py --records output/minimal_doctype_20260804 \\
        --database rd2_test --execute

    # 3) 렌더
    python scripts/render_minimal_pdf.py --records output/minimal_doctype_20260804

    # 4) PDF 경로만 UPDATE
    python scripts/writeback_minimal_to_rds.py --records output/minimal_doctype_20260804 \\
        --database rd2_test --fill-pdf-path --execute
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

# .env와 기본 출력 루트는 저장소 루트 기준이다. `import rd2`는 설치가
# 책임지므로(pyproject.toml 참고) sys.path는 건드리지 않는다.
ROOT = Path(__file__).resolve().parents[2]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    GROUND_IDS,
    SubclauseKey,
)
from rd2.generators.output_naming import generation_output_filename  # noqa: E402
from rd2.source_generation.contracts import (  # noqa: E402
    CONTRACT_SCHEMA_VERSION,
    GeneratedDocumentIR,
)
from rd2.source_generation.minimal_envelope import (  # noqa: E402
    MINIMAL_GENERATION_ROUTE,
    minimal_generation_target,
)
from rd2.source_generation.minimal_prompt import (  # noqa: E402
    render_minimal_generator_system_prompt,
    render_minimal_generator_user_prompt,
)
from rd2.source_generation.rds_writeback import (  # noqa: E402
    SourceRow,
    build_generated_document,
    generated_source_name,
    generated_source_url,
)
from rd2.storage.db import DocumentStore  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_RENDER_OK = {"ok", "ok_truncated"}


def _generated_document(
    record: dict,
    accepted_versions: set[str],
) -> tuple[GeneratedDocumentIR, str]:
    """기록의 문서를 현재 계약으로 읽는다.

    ``GeneratedDocumentIR``의 ``contract_version``은 ``Literal``이라 옛 배치의
    산출물은 그대로 통과하지 못한다. 파싱을 위해 현재 값으로 맞춰 읽되, 원래
    버전은 돌려줘서 ``generated_text`` JSON에 되살린다 — 그 컬럼이 IR을 통째로
    담게 된 뒤로는 맞춰 읽은 값이 그대로 행에 남기 때문이다
    (``rds_writeback.generated_document_json``). 어느 버전에서 왔는지는 요약에도
    남긴다.

    그래도 자동으로 하지는 않는다. 필드가 실제로 바뀐 버전 상승이면 조용히
    맞춰 읽는 순간 빠진 값이 기본값으로 채워지고, 그건 산출물만 봐서는
    드러나지 않는다. 어느 버전을 받아줄지는 ``--accept-contract-version``으로
    사람이 지목해야 한다.
    """

    document = dict(record["generation"]["document"])
    found = str(document.get("contract_version") or "")
    if found and found != CONTRACT_SCHEMA_VERSION:
        if found not in accepted_versions:
            raise ValueError(
                f"계약 버전이 {found}인데 현재는 {CONTRACT_SCHEMA_VERSION}다. "
                f"다시 생성하거나 --accept-contract-version {found}를 줘라"
            )
        document["contract_version"] = CONTRACT_SCHEMA_VERSION
    return GeneratedDocumentIR.model_validate(document), found


def _source_row(record: dict) -> SourceRow:
    raw = record["source_row"]
    production_date = raw.get("production_date")
    if isinstance(production_date, str) and production_date:
        # 기록에는 문자열로 남아 있다(JSON). ``Document``는 date를 요구한다.
        production_date = date.fromisoformat(production_date[:10])
    else:
        production_date = None
    # unit_task/subject_category는 2026-08-04에 배치 SELECT에 추가됐다. 그 전
    # 기록에는 없으므로 get으로 읽고 없으면 None으로 둔다 — 옛 배치를 다시
    # 돌리게 만들 값은 아니지만, 있는 기록에서는 컬럼이 채워진다.
    return SourceRow(
        id=int(raw["id"]),
        source=str(raw["source"]),
        doc_type=raw.get("doc_type"),
        title=raw.get("title"),
        ordering_agency=raw.get("ordering_agency"),
        department=raw.get("department"),
        unit_task=raw.get("unit_task"),
        subject_category=raw.get("subject_category"),
        production_date=production_date,
    )


def _input_prompt(record: dict) -> str:
    """생성에 실제로 들어간 프롬프트를 되살린다.

    배치 기록은 프롬프트 문자열을 남기지 않지만, 재료(세부조항·ground·원문·
    layout_analysis·슬롯)를 전부 남긴다. 렌더 함수가 결정론적이라 같은 재료로
    같은 문자열이 나온다 — 기록에 프롬프트를 통째로 또 적어 두 벌을 어긋나게
    두는 것보다 낫다.

    슬롯 표는 ``run_minimal_doc_type_batch._render_slot_table``과 같은 모양이어야
    한다. 다르면 DB에 남는 프롬프트가 실제로 보낸 것과 달라진다.
    """

    assessment = record["assessment"]
    subclause = SubclauseKey(record["subclause_key"])
    ground_index = GROUND_IDS.index(record["ground_id"])
    slot_table = "\n".join(
        f"- [{slot['kind']}] {slot['name']} — 원문 인용: "
        f"{slot['evidence_span']['quote']} ({slot['evidence_span']['block_id']})"
        for slot in assessment["available_slots"]
    )
    system = render_minimal_generator_system_prompt(
        subclause, ground_index=ground_index
    )
    user = render_minimal_generator_user_prompt(
        record["source_text"],
        layout_analysis=assessment["layout_analysis"],
        available_slots=slot_table,
    )
    return f"[SYSTEM]\n{system}\n\n[USER]\n{user}"


def _rendered_pdfs(rendered_dir: Path) -> dict[str, tuple[str, Path | None]]:
    """batch_manifest.json을 (document_id -> (status, pdf 경로))로 편다.

    ``render_input_directory``가 남기는 균등 배치 manifest는 dict이고 문서마다
    ``output_dir``만 준다. 실제 PDF 경로는 그 안의 manifest.json에 있다.
    """

    manifest_path = rendered_dir / "batch_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"렌더 결과가 없다: {manifest_path}\n"
            "  scripts/render_minimal_pdf.py를 먼저 돌려라."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["documents"] if isinstance(manifest, dict) else manifest

    result: dict[str, tuple[str, Path | None]] = {}
    for entry in entries:
        document_id = str(entry.get("document_id") or "")
        status = str(entry.get("status") or "")
        if not document_id:
            continue
        pdf: Path | None = None
        if status in _RENDER_OK and entry.get("output_dir"):
            document_manifest = Path(str(entry["output_dir"])) / "manifest.json"
            if document_manifest.exists():
                rendered = json.loads(
                    document_manifest.read_text(encoding="utf-8")
                )
                if rendered:
                    pdf = Path(str(rendered[0]["pdf"]))
        result[document_id] = (status, pdf)
    return result


def _document_id(record: dict) -> str:
    """렌더 manifest의 ``document_id``와 맞춘다.

    ``render_generated_documents._output_id``는 payload의 ``output_filename``
    stem을 쓴다 — ``source_document_id``가 아니다. 그 파일명을 만드는 규칙이
    ``generation_output_filename`` 하나뿐이므로 ``render_minimal_pdf``와 **같은
    호출**로 다시 만든다. 규칙을 여기 손으로 옮겨 적으면 한쪽만 바뀌었을 때
    조용히 매칭이 깨지고, 그러면 전 건이 "렌더 missing"으로 떨어진다.
    """

    filename = generation_output_filename(
        generation_route=MINIMAL_GENERATION_ROUTE.value,
        source_filename=Path(record["source_row"]["body_file_path"]).name,
        generated_title=record["generation"]["document"]["title"],
    )
    return Path(filename).stem


def _insert_rows(args, files: list[Path], store: DocumentStore | None) -> int:
    """렌더 **전**에 도는 패스. ``body_file_path``만 빼고 전 컬럼을 채워 넣는다."""

    inserted = skipped = failed = 0
    contract_versions: dict[str, int] = {}
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        document_id = _document_id(record)

        try:
            generated, contract_version = _generated_document(
                record, set(args.accept_contract_version)
            )
            if contract_version and contract_version != CONTRACT_SCHEMA_VERSION:
                contract_versions[contract_version] = (
                    contract_versions.get(contract_version, 0) + 1
                )
            document = build_generated_document(
                document=generated,
                source_document_id=document_id,
                target=minimal_generation_target(
                    SubclauseKey(record["subclause_key"])
                ),
                generation_route=MINIMAL_GENERATION_ROUTE,
                source_row=_source_row(record),
                input_prompt=_input_prompt(record),
                content=record["source_text"],
                # 렌더가 아직 안 돌았다. 이 칸은 --fill-pdf-path가 메운다.
                body_file_path=None,
                # 맞춰 읽은 옛 계약 버전은 행에 남지 않는다 — 그 값이 되살아나던
                # ``pdf_renderd_json``이 스키마에서 빠졌다(2026-08-05). 어느
                # 버전에서 왔는지는 아래 요약에만 남는다.
            )
        except Exception as exc:  # noqa: BLE001 - 한 건이 배치를 끊지 않는다
            print(f"  [실패] {document_id} — 조립: {type(exc).__name__}: {exc}")
            failed += 1
            continue

        print(
            f"  [{'ok' if store is not None else 'dry'}] {document_id}"
            f" — 제{document.cso_sub_clause}호"
            f"/{record['subclause_key']}"
            f" ref_id={document.ref_id}"
            f" prompt={len(document.input_prompt or ''):,}자"
            f" content={len(document.content or ''):,}자"
            f" generated={len(document.generated_text or ''):,}자"
        )
        if store is None:
            continue
        try:
            if store.upsert(document):
                inserted += 1
            else:
                # 같은 원문·같은 세부조항으로 이미 들어간 행이 있다. 덮어쓰지
                # 않는다 — 재실행이 기존 본문을 갈아치우면 그 행으로 이미 학습한
                # 셋과 DB가 어긋난다.
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [실패] {document_id} — upsert: {type(exc).__name__}: {exc}")
            failed += 1

    if store is None:
        print("\n--execute 없이 돌렸다. DB는 건드리지 않았다.")
        return 0

    summary = {
        "written_at": datetime.now().astimezone().isoformat(),
        "records": args.records.as_posix(),
        "database": args.database,
        "inserted": inserted,
        "skipped_duplicate": skipped,
        "failed": failed,
        # 현재 계약이 아닌 버전으로 만든 기록을 받아준 건수. 비어 있어야 정상이다.
        "accepted_older_contract_versions": contract_versions,
    }
    (args.records / "rds_writeback.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n삽입 {inserted}건 / 중복 스킵 {skipped}건 / 실패 {failed}건")
    print(
        "PDF 경로는 아직 NULL이다. 렌더 후"
        " --fill-pdf-path로 한 번 더 돌려라."
    )
    return 0 if failed == 0 else 1


def _fill_pdf_paths(args, files: list[Path], store: DocumentStore | None) -> int:
    """렌더 **후**에 도는 패스. 이미 들어간 행의 ``body_file_path``만 UPDATE한다.

    행을 다시 찾는 열쇠는 ``dedup_key``이고, 그 재료인 source/source_url은
    ``generated_source_name``/``generated_source_url``로 기록에서 다시 만든다 —
    둘 다 결정론적이라 삽입 패스가 만든 값과 같다. 값이 흔들리면 여기서 행을
    못 찾고, 그때는 조용히 넘어가지 않고 실패로 센다.
    """

    renders = _rendered_pdfs(args.records / "rendered")
    pdf_root = args.pdf_root.resolve()

    filled = failed = 0
    gated: dict[str, int] = {}
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        document_id = _document_id(record)
        status, pdf = renders.get(document_id, ("missing", None))
        if status not in _RENDER_OK or pdf is None:
            # 렌더가 안 된 문서. 행은 이미 있고 body_file_path가 NULL로 남는다.
            gated[status] = gated.get(status, 0) + 1
            print(f"  [건너뜀] {document_id} — 렌더 {status}")
            continue

        resolved = pdf.resolve()
        try:
            body_file_path = resolved.relative_to(pdf_root).as_posix()
        except ValueError:
            # --pdf-root 밖이면 상대화할 수 없다. 절대경로를 넣으면 다른
            # 기계에서 못 찾으므로 멈춘다.
            print(f"  [실패] {document_id} — PDF가 --pdf-root 밖이다: {resolved}")
            failed += 1
            continue

        target = minimal_generation_target(SubclauseKey(record["subclause_key"]))
        source = generated_source_name(str(record["source_row"]["source"]))
        source_url = generated_source_url(document_id, target)

        print(
            f"  [{'ok' if store is not None else 'dry'}] {document_id}"
            f" -> {body_file_path}"
        )
        if store is None:
            continue
        try:
            updated = store.set_body_file_path(
                source=source,
                source_url=source_url,
                body_file_path=body_file_path,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [실패] {document_id} — update: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        if updated:
            filled += 1
        else:
            print(
                f"  [실패] {document_id} — 행이 없다."
                " --fill-pdf-path 없이 먼저 돌려 행을 넣어라"
            )
            failed += 1

    if store is None:
        print("\n--execute 없이 돌렸다. DB는 건드리지 않았다.")
        return 0

    summary = {
        "written_at": datetime.now().astimezone().isoformat(),
        "records": args.records.as_posix(),
        "database": args.database,
        "body_file_path_filled": filled,
        "failed": failed,
        # 행은 있지만 PDF가 없는 건수. body_file_path가 NULL로 남는다.
        "gated_by_render": gated,
    }
    (args.records / "rds_pdf_path.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\n경로 채움 {filled}건 / 실패 {failed}건"
        + (f" / 렌더 실패로 NULL 유지 {sum(gated.values())}건" if gated else "")
    )
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--records",
        type=Path,
        required=True,
        help="배치 out-dir. documents/*.json(과 --fill-pdf-path면 rendered/)을 읽는다",
    )
    parser.add_argument(
        "--pdf-root",
        type=Path,
        default=ROOT,
        help=(
            "body_file_path를 이 경로 기준 상대경로로 저장한다"
            " (기본: 저장소 루트)"
        ),
    )
    # DocumentStore는 MARIADB_*만 읽는다. RDS는 퍼블릭 접근이 막혀 있어 EC2
    # 터널의 로컬 끝을 가리켜야 하므로(deploy/README.md) host/port를 직접
    # 넘길 수 있어야 한다.
    parser.add_argument(
        "--database",
        default=None,
        help="기본값은 .env의 MARIADB_DATABASE. 검증용은 rd2_test를 쓴다",
    )
    parser.add_argument("--db-host", default=None)
    parser.add_argument("--db-port", type=int, default=None)
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-password", default=None)
    parser.add_argument(
        "--accept-contract-version",
        action="append",
        default=[],
        metavar="VERSION",
        help=(
            "이 계약 버전으로 만든 옛 기록도 읽는다(예: 2.2.0)."
            " 필드가 바뀐 상승이면 쓰지 말고 다시 생성하라"
        ),
    )
    parser.add_argument(
        "--fill-pdf-path",
        action="store_true",
        help=(
            "렌더 후 패스. 새 행을 넣지 않고 이미 들어간 행의"
            " body_file_path만 UPDATE한다"
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제로 DB에 쓴다. 없으면 무엇을 쓸지만 보여준다",
    )
    args = parser.parse_args()

    docs_dir = args.records / "documents"
    files = sorted(docs_dir.glob("*.json"))
    if not files:
        print(f"생성물이 없다: {docs_dir}")
        return 1

    store = (
        DocumentStore(
            host=args.db_host,
            port=args.db_port,
            user=args.db_user,
            password=args.db_password,
            database=args.database,
        )
        if args.execute
        else None
    )
    if store is not None:
        print(f"DB: {store.user}@{store.host}:{store.port}/{store.database}")
    try:
        if args.fill_pdf_path:
            return _fill_pdf_paths(args, files, store)
        return _insert_rows(args, files, store)
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
