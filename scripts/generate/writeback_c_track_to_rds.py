"""C트랙(제1~4호) 생성물을 ``documents`` 테이블 행으로 되쓴다.

``run_c_track_generation``/``run_c_cot_generation``은 파일로 끝난다. 그 산출물을
수집 코퍼스와 같은 테이블에 넣는 것이 이 스크립트다. 5~8호의
``writeback_minimal_to_rds``와 같은 자리이고, 다른 점은 **원문이 없다**는 것뿐이다
(``c_track_writeback`` 참고).

    tmp/c-cot-001.json
        -> build_generated_document(metadata=CaseFrame에서, route=fully_synthetic)
        -> DocumentStore.upsert   (generated_yn='1', body_text/content/ref_id는 NULL)

**프롬프트는 다시 렌더해서 넣는다.** 생성 스크립트가 프롬프트를 기록에 남기지
않는데, ``(템플릿, seed, case_index)``만 있으면 같은 문자열이 결정론적으로 다시
나온다(``expand_cases``). 그래서 ``input_prompt``는 지어내는 값이 아니라 다시
계산한 값이다 — 다만 그 결정론은 **템플릿이 그대로일 때만** 성립하므로,
기록의 ``template_version``이 지금과 다르면 멈춘다. 조용히 다시 렌더하면 지금
템플릿의 프롬프트가 옛 문서의 입력으로 남고, 그 착각은 행만 봐서는 드러나지
않는다. 받아줄 버전은 ``--accept-template-version``으로 사람이 지목한다.

기본값은 쓰지 않는다. ``--execute`` 없이 돌리면 어떤 행이 만들어지는지만 보여
주고 DB를 건드리지 않는다.

사용:
    # 1) 만들어질 행 확인만
    python scripts/generate/writeback_c_track_to_rds.py --records tmp/c-cot-001.json

    # 2) 실제 삽입 (검증용은 rd2_test)
    python scripts/generate/writeback_c_track_to_rds.py --records tmp/c-cot \\
        --database rd2_test --execute
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from rd2.source_generation.c_track_templates import (  # noqa: E402
    C_TRACK_COT_PROMPT_VERSION,
    C_TRACK_TEMPLATE_VERSION,
    C_TRACK_TEMPLATES,
    case_frame,
    render_case_section,
    render_cot_case_section,
    render_cot_fixed_prefix,
    render_fixed_prefix,
)
from rd2.source_generation.c_track_writeback import (  # noqa: E402
    build_c_track_row,
    c_track_source_document_id,
)
from rd2.source_generation.contracts import (  # noqa: E402
    CONTRACT_SCHEMA_VERSION,
    ConfidentialSnippet,
    GeneratedDocumentIR,
)
from rd2.storage.db import DocumentStore  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _template_of(record: dict):
    """``"security_defense/국방부"`` 형식의 기록 값을 등록된 템플릿으로 되돌린다."""

    key = str(record["template"])
    subclause_value, _, agency = key.partition("/")
    for (subclause, registered_agency), template in C_TRACK_TEMPLATES.items():
        if subclause.value == subclause_value and registered_agency == agency:
            return template
    raise ValueError(f"등록되지 않은 템플릿이다: {key}")


def _frame_of(template, record: dict):
    """기록의 좌표로 사건 프레임을 다시 세운다.

    기록의 ``case_index``는 ``CaseFrame.case_index``, 즉 전개 순번이 아니라
    **좌표**다. ``case_frame``이 그 값 하나로 프레임을 되돌린다 — seed는 전개
    순서만 정하므로 여기서는 기록용으로만 읽는다.
    """

    return case_frame(template, int(record["case_index"])), int(record["seed"])


@dataclass(frozen=True)
class _Parsed:
    """기록 하나에서 꺼낸 것들. 두 생성 경로의 모양 차이를 여기서 흡수한다."""

    document: dict
    system_prompt: str
    user_prompt: str
    prompt_version: str | None
    #: 3단계 경로만 낸다. 단일 출력 경로에는 발췌도 저장 사유도 없다.
    snippets: tuple[ConfidentialSnippet, ...]
    storage_reasoning: str | None


def _parse(record: dict, template, frame) -> _Parsed:
    """문서 IR과, 그 문서를 만든 프롬프트와, 비공개 근거를 돌려준다.

    두 생성 경로가 기록 모양이 다르다 — 3단계(CoT) 경로는 문서를
    ``response.document``에 두고 프롬프트 골격도 단계형이다. 어느 쪽으로 만든
    기록인지는 ``response`` 키 유무로 갈린다.
    """

    if "response" not in record:
        return _Parsed(
            document=dict(record["document"]),
            system_prompt=render_fixed_prefix(template),
            user_prompt=render_case_section(template, frame),
            prompt_version=None,
            snippets=(),
            storage_reasoning=None,
        )

    response = record["response"]
    return _Parsed(
        document=dict(response["document"]),
        system_prompt=render_cot_fixed_prefix(template),
        user_prompt=render_cot_case_section(template, frame),
        prompt_version=str(record.get("prompt_version") or C_TRACK_COT_PROMPT_VERSION),
        snippets=tuple(
            ConfidentialSnippet.model_validate(snippet)
            for snippet in response.get("confidential_snippets", ())
        ),
        storage_reasoning=response.get("reasoning_for_storage"),
    )


def _row_from_record(record: dict, *, accepted_versions: set[str]) -> tuple:
    template = _template_of(record)

    found_template_version = str(record.get("template_version") or "")
    if found_template_version and found_template_version != C_TRACK_TEMPLATE_VERSION:
        if found_template_version not in accepted_versions:
            raise ValueError(
                f"템플릿 버전이 {found_template_version}인데 지금은 "
                f"{C_TRACK_TEMPLATE_VERSION}다. 프롬프트를 다시 렌더하면 옛 "
                f"문서에 지금 프롬프트가 붙는다 — 다시 생성하거나 "
                f"--accept-template-version {found_template_version}을 줘라"
            )

    frame, seed = _frame_of(template, record)
    parsed = _parse(record, template, frame)
    document_dump = parsed.document

    # 현재 계약과 같은 버전은 "맞춰 읽은 것"이 아니다. 같아도 값을 들고 있으면
    # 요약의 accepted_older_... 가 매번 차서 정상과 이상을 구분하지 못한다.
    found_contract = str(document_dump.get("contract_version") or "")
    if found_contract == CONTRACT_SCHEMA_VERSION:
        found_contract = ""
    elif found_contract:
        document_dump["contract_version"] = CONTRACT_SCHEMA_VERSION
    # 생성 스크립트는 계산 필드까지 포함해 dump한다(``body_text``는 blocks에서
    # 파생된 값이다). 계약이 extra를 금지하므로 되읽기 전에 뺀다 — 지우는 게
    # 아니라 blocks에서 다시 계산된다.
    for computed in GeneratedDocumentIR.model_computed_fields:
        document_dump.pop(computed, None)
    document = GeneratedDocumentIR.model_validate(document_dump)

    row = build_c_track_row(
        template=template,
        frame=frame,
        document=document,
        seed=seed,
        system_prompt=parsed.system_prompt,
        user_prompt=parsed.user_prompt,
        snippets=parsed.snippets,
        storage_reasoning=parsed.storage_reasoning,
        prompt_version=parsed.prompt_version,
        document_contract_version=found_contract or None,
    )
    return row, c_track_source_document_id(frame, seed=seed), found_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--records",
        type=Path,
        required=True,
        help="생성 결과 JSON 파일, 또는 그런 파일들이 든 디렉터리",
    )
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
        "--accept-template-version",
        action="append",
        default=[],
        metavar="VERSION",
        help="이 템플릿 버전으로 만든 옛 기록도 읽는다",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제로 DB에 쓴다. 없으면 무엇을 쓸지만 보여준다",
    )
    args = parser.parse_args()

    if args.records.is_dir():
        files = sorted(args.records.glob("*.json"))
    elif args.records.is_file():
        files = [args.records]
    else:
        print(f"생성물이 없다: {args.records}")
        return 1
    if not files:
        print(f"생성물이 없다: {args.records}")
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

    accepted = set(args.accept_template_version)
    inserted = skipped = failed = 0
    older_contracts: dict[str, int] = {}
    try:
        for path in files:
            record = json.loads(path.read_text(encoding="utf-8"))
            try:
                row, document_id, older = _row_from_record(
                    record, accepted_versions=accepted
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  [실패] {path.name} — {type(exc).__name__}: {exc}")
                failed += 1
                continue
            if older:
                older_contracts[older] = older_contracts.get(older, 0) + 1

            print(
                f"  [{'ok' if store is not None else 'dry'}] {document_id}\n"
                f"        {row.ordering_agency} / {row.department}"
                f" / 제{row.cso_sub_clause}호 / {row.doc_type}\n"
                f"        {row.title}"
            )
            if store is None:
                continue
            try:
                if store.upsert(row):
                    inserted += 1
                else:
                    skipped += 1
                    print("        (이미 있는 행이다 — 스킵)")
            except Exception as exc:  # noqa: BLE001
                print(f"  [실패] {document_id} — upsert: {type(exc).__name__}: {exc}")
                failed += 1
    finally:
        if store is not None:
            store.close()

    if store is None:
        print(f"\n--execute 없이 돌렸다. DB는 건드리지 않았다 (실패 {failed}건).")
        return 0 if failed == 0 else 1

    print(f"\n삽입 {inserted}건 / 중복 스킵 {skipped}건 / 실패 {failed}건")
    if older_contracts:
        print(f"옛 계약 버전으로 읽은 기록: {older_contracts}")
    summary = {
        "written_at": datetime.now().astimezone().isoformat(),
        "records": args.records.as_posix(),
        "database": args.database,
        "inserted": inserted,
        "skipped_duplicate": skipped,
        "failed": failed,
        "accepted_older_contract_versions": older_contracts,
    }
    out_dir = args.records if args.records.is_dir() else args.records.parent
    (out_dir / "c_track_rds_writeback.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
