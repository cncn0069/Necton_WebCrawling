"""문서종류(doc_type)마다 원문 N건을 뽑아 **최소 프롬프트 루트**로 생성한다.

``run_minimal_generation``이 원문 하나로 하는 일(최소 판별기 -> 자료 하나
결정론적 선택 -> 최소 생성기)을 그대로 배치로 돌린다. 다른 점은 입력을 고르는
방법뿐이다 — 로컬 추출 JSON 대신 **DB의 공개 PDF 행**을 doc_type마다 최신
N건씩 가져온다.

    documents(공개, body_file_path가 .pdf)
        -> doc_type별 id DESC 상위 N건
        -> PDF 추출 -> block snapshot
        -> minimal_classifier -> 자료 하나 결정론적 선택 -> minimal 생성기

큰 프롬프트 경로(판별기 -> 플래너 -> 생성기 -> 검증기)는 제거됐다. 남은 것은
이 경로뿐이고, 여기는 프롬프트 두 개가 전부다 — 플래너도 검증기도 없다.

**한 건이 배치를 끊지 않는다.** 추출 실패·계약 위반·호출 실패는 사유를 세어
``summary.json``의 ``drops``에 남기고 다음 원문으로 넘어간다.

기본값은 실호출을 하지 않는다. ``--execute`` 없이 돌리면 어떤 행이 뽑혔고 원문이
얼마나 큰지까지만 보여 주고 과금되지 않는다. 호출 수는 문서당 2회다.

사용:
    # 대상 확인만 (무료)
    python scripts/run_minimal_doc_type_batch.py --out-dir output/minimal_doctype

    # 실제 호출 (과금 — 문서당 2회)
    python scripts/run_minimal_doc_type_batch.py --out-dir output/minimal_doctype \\
        --per-doc-type 5 --execute
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

# .env와 기본 데이터 디렉터리는 저장소 루트 기준이다. `import rd2`는 설치가
# 책임지므로(pyproject.toml 참고) sys.path는 건드리지 않는다.
ROOT = Path(__file__).resolve().parents[2]

# conda 기본값이 없는 파일을 가리켜 OpenAI 호출이 요청 전에 FileNotFoundError로
# 죽는다. certifi 번들로 덮어쓴다 — import 시점이어야 openai가 집어 간다.
if not Path(os.environ.get("SSL_CERT_FILE", "")).exists():
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    DOCUMENT_FORM_BY_TYPE,
    GROUND_IDS,
    SUBCLAUSE_GENERATION_RULES,
    DocumentForm,
    SemanticDocumentType,
    clause_of_subclause,
)
from rd2.extractors.pdf import _page_needs_ocr  # noqa: E402
from rd2.source_generation.contracts import (  # noqa: E402
    GeneratorResponse,
    SourceDocumentSnapshot,
)
from rd2.source_generation.document_select import render_full_source  # noqa: E402
from rd2.source_generation.minimal_classifier import (  # noqa: E402
    MINIMAL_CLASSIFIER_VERSION,
    MinimalSourceAssessment,
    render_minimal_classifier_system_prompt,
    render_minimal_classifier_user_prompt,
)
from rd2.source_generation.minimal_prompt import (  # noqa: E402
    MINIMAL_PROMPT_VERSION,
    render_minimal_generator_system_prompt,
    render_minimal_generator_user_prompt,
    select_minimal_ground,
)
from rd2.source_generation.gateway import default_openai_gateway  # noqa: E402
from rd2.storage.connection import (  # noqa: E402
    ConnectionSettingsError,
    add_arguments as add_db_arguments,
    describe,
    settings_from_args,
)
from rd2.storage.reader import DocumentReader  # noqa: E402

# cp949 콘솔(윈도우 기본)에서 한글 제목·em-dash가 섞인 진행 로그가
# UnicodeEncodeError로 죽는다. collect_prism.py와 같은 처리.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

#: 한 페이지에 담는 block 수. ``run_seoul_official_batch``가 쓰던 값을 그대로
#: 옮겼다 — 그 스크립트가 사라지면서 여기가 유일한 사용처가 됐다.
BLOCKS_PER_PAGE = 12


def _snapshot_from_texts(
    texts: list[str],
    *,
    source: str,
    doc_id: str,
    source_sha256: str,
) -> tuple[SourceDocumentSnapshot, str]:
    """추출한 줄 목록을 block snapshot으로 옮긴다."""

    pages = []
    for offset in range(0, len(texts), BLOCKS_PER_PAGE):
        page_number = offset // BLOCKS_PER_PAGE + 1
        pages.append(
            {
                "page_number": page_number,
                "blocks": [
                    {"block_id": f"p{page_number}:b{index}", "text": text}
                    for index, text in enumerate(texts[offset : offset + BLOCKS_PER_PAGE])
                ],
            }
        )

    snapshot = SourceDocumentSnapshot.model_validate(
        {
            "source_document_id": f"{source}-{doc_id}",
            "source": source,
            "manifest_key": "minimal-doc-type-batch",
            "source_sha256": source_sha256,
            "pages": pages,
        }
    )
    # 첫 몇 줄에 제목이 들어 있는 경우가 많다. 없으면 문서 ID로 대체한다.
    title = next((t for t in texts if len(t) > 6), doc_id)
    return snapshot, title


#: 생성 입력으로 삼는 문서종류. 공고·보고서·정책자료처럼 **본문이 있는** 형식만
#: 있고, 목록·링크성 doc_type은 빠져 있다.
DOC_TYPES: tuple[str, ...] = (
    "pre_spec_notice",
    "bid_notice",
    "bid_renotice",
    "notice",
    "policy_material",
    "budget_material",
    "director_activity",
    "audit_result",
    "research_report",
    "press_release",
    "notification",
    "interpretation_compilation",
    "guide",
    "status_report",
    "directive",
    "regulation",
    "official_document",
)

def _snapshot_from_pdf(
    path: Path,
    *,
    source: str,
    doc_id: str,
    max_pages: int,
    max_chars: int,
    min_chars: int,
) -> tuple[SourceDocumentSnapshot, str, bool, int, int] | None:
    """
    원문이 한도를 넘으면 앞에서부터 자른다. 한도는 **PDF 쪽 수**가 기본이고
    (``max_pages``), ``max_chars``는 0이 아닐 때만 함께 거는 보조 상한이다. 큰
    배치에는 relevance selection(원문을 훑어 관련 block만 고르는 LLM 호출)이
    있지만 여기서는 쓰지 않는다 — 프롬프트 두 개가 전부인 것이 이 경로의 정의라
    호출을 하나 더 붙이면 비교 대상이 달라진다. 공문·보고서는 표제부와 앞머리에
    업무·주체가 다 나오므로 판별기 입력으로 앞부분이 뒤보다 낫다.

    돌려주는 것은 (snapshot, 제목, 잘렸는지, 버린 페이지 수, 읽은 쪽 수)다.
    """

    import pymupdf

    texts: list[str] = []
    used = 0
    truncated = False
    dropped_pages = 0
    used_pages = 0
    try:
        with pymupdf.open(path) as document:
            page_count = len(document)
            for page in document:
                if max_pages and used_pages >= max_pages:
                    # 40쪽짜리 매뉴얼의 41쪽 이후는 판별에도 생성에도 쓰이지
                    # 않는다. 넣으면 값만 비싸진다.
                    truncated = True
                    break
                text = page.get_text("text")
                if _page_needs_ocr(text):
                    # 텍스트 레이어가 없거나 글리프가 깨진 페이지. 넘기면 판별기가
                    # U+FFFD 덩어리를 보고 지어내기 시작한다. 한도에도 세지 않는다.
                    dropped_pages += 1
                    continue
                used_pages += 1
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if max_chars and used + len(line) > max_chars:
                        truncated = True
                        break
                    texts.append(line)
                    used += len(line)
                if truncated:
                    break
            if used_pages + dropped_pages < page_count:
                truncated = True
    except Exception:  # noqa: BLE001 - 한 문서 실패가 배치를 끊지 않는다
        return None
    if used < min_chars:
        return None

    snapshot, title = _snapshot_from_texts(
        texts,
        source=source,
        doc_id=doc_id,
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return snapshot, title, truncated, dropped_pages, used_pages


def _render_slot_table(assessment: MinimalSourceAssessment) -> str:
    return "\n".join(
        f"- [{slot.kind.value}] {slot.name} — 원문 인용: "
        f"{slot.evidence_span.quote} ({slot.evidence_span.block_id})"
        for slot in assessment.available_slots
    )


def _count(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--per-doc-type", type=int, default=5)
    parser.add_argument(
        "--files-root",
        type=Path,
        default=ROOT / "data",
        help="body_file_path의 기준 경로(상대경로로 저장돼 있다)",
    )
    # 접속정보는 기본이 .env의 MARIADB_*(로컬 rd2_dump)다. --from-rds를 주면
    # RDS_MARIADB_*를 읽는다 — .env의 MARIADB_HOST를 고쳐서 전환하면 다른
    # 스크립트가 모르는 사이에 운영 DB를 보게 된다.
    add_db_arguments(parser)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="동시에 처리할 문서 수. 시간의 대부분이 추론 대기라 그만큼 줄어든다",
    )
    parser.add_argument("--classifier-model", default="gpt-5")
    parser.add_argument("--generator-model", default="gpt-5")
    parser.add_argument("--max-output-tokens", type=int, default=16000)
    parser.add_argument(
        "--max-source-pages",
        type=int,
        default=40,
        help="판별기·생성기에 넣을 원문의 최대 쪽 수. 넘으면 앞 40쪽만 쓴다",
    )
    parser.add_argument(
        "--max-source-chars",
        type=int,
        default=0,
        help="글자 수 보조 상한. 0이면 쪽 수 한도만 건다",
    )
    parser.add_argument(
        "--min-source-chars",
        type=int,
        default=200,
        help="이보다 짧으면 스캔본으로 보고 건너뛴다",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "고른 행 중 앞에서 N건만 처리한다(0이면 전량)."
            " 과금되는 실호출 전에 한두 건으로 흐름을 확인할 때 쓴다"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="out-dir에 이미 결과 파일이 있는 문서는 건너뛴다",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 LLM을 호출한다. 문서당 2회 호출이며 그대로 과금된다.",
    )
    args = parser.parse_args()

    try:
        settings = settings_from_args(args)
    except ConnectionSettingsError as exc:
        raise SystemExit(str(exc)) from exc
    # 어느 DB를 봤는지 산출물 밖에서 알 수 있어야 한다. rd2_dump(로컬 덤프)와
    # 운영 RDS는 행 구성이 다른데 기록만 봐서는 구분되지 않는다.
    print(f"DB: {describe(settings)}{' (RDS)' if args.from_rds else ''}")
    with DocumentReader(settings=settings) as reader:
        rows = reader.top_pdfs_by_doc_type(DOC_TYPES, args.per_doc_type)
    if not rows:
        print("조건에 맞는 행이 없다.")
        return 1
    if args.limit and args.limit < len(rows):
        # 잘라낸 사실을 반드시 찍는다. 조용히 줄이면 산출물만 봐서는 "조건에
        # 맞는 행이 이것뿐"이었는지 상한에 걸린 것인지 구분되지 않는다.
        print(f"--limit {args.limit}: 고른 {len(rows)}건 중 앞 {args.limit}건만 쓴다")
        rows = rows[: args.limit]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = args.out_dir / "documents"
    docs_dir.mkdir(exist_ok=True)

    # 문서 형식은 수집 라벨(doc_type)에서 온다 — 판별기에게 묻지 않는다.
    # 형식마다 프롬프트가 달라지므로 한 번씩만 렌더링해 둔다.
    forms: dict[str, DocumentForm] = {
        doc_type: DOCUMENT_FORM_BY_TYPE[SemanticDocumentType(doc_type)]
        for doc_type in {row["doc_type"] for row in rows}
    }
    classifier_prompts = {
        doc_type: render_minimal_classifier_system_prompt(form)
        for doc_type, form in forms.items()
    }

    by_doc_type: dict[str, int] = {}
    for row in rows:
        _count(by_doc_type, row["doc_type"])
    print(f"대상 {len(rows)}건 — " + " / ".join(
        f"{name} {count}" for name, count in sorted(by_doc_type.items())
    ))
    for doc_type in sorted(by_doc_type):
        print(
            f"  {doc_type} -> 형식 {forms[doc_type].value}"
            f" (판별기 프롬프트 {len(classifier_prompts[doc_type]):,}자)"
        )
    print()

    gateway = default_openai_gateway() if args.execute else None

    done = 0
    drops: dict[str, int] = {}
    clause_counts: dict[str, int] = {}
    subclause_counts: dict[str, int] = {}
    form_counts: dict[str, int] = {}
    doc_type_done: dict[str, int] = {}
    records_path = args.out_dir / "minimal_records.jsonl"

    def process(row: dict) -> tuple[dict | None, str, list[str]]:
        """원문 하나를 판별 -> 생성한다. 배치가 아니라 **이 함수**가 문서 하나다.

        돌려주는 것은 (기록, 사유, 로그)다. 예외를 밖으로 내지 않으므로 한 건이
        배치를 끊지 않는다. 스레드에서 돌기 때문에 공유 상태를 건드리지 않고
        로그도 모아서 돌려준다 — 여러 문서의 진행 줄이 섞이면 읽을 수 없다.
        """

        tag = f"{row['doc_type']}/{row['id']}"
        log: list[str] = []
        # 경로는 윈도우에서 수집돼 역슬래시로 저장돼 있다
        # (``seoul_opengov\\official_document\\...``). 리눅스에서는 그게 구분자가
        # 아니라 파일명의 한 글자가 되어 전 건이 "파일 없음"으로 떨어지므로
        # 조인 전에 통일한다. 실제 파일명에 역슬래시가 들어갈 일은 없다
        # (files.py의 sanitize 규칙이 금지문자로 뺀다).
        path = args.files_root / row["body_file_path"].replace("\\", "/")
        if not path.exists():
            return None, "file_missing", [f"[skip] {tag} 파일 없음"]

        built = _snapshot_from_pdf(
            path,
            source=row["source"],
            doc_id=str(row["id"]),
            max_pages=args.max_source_pages,
            max_chars=args.max_source_chars,
            min_chars=args.min_source_chars,
        )
        if built is None:
            # 스캔본(텍스트 레이어 없음)이거나 PDF가 열리지 않는 경우다.
            return None, "snapshot_failed", [
                f"[skip] {tag} 본문 추출 실패(스캔본일 수 있다)"
            ]
        snapshot, title, truncated, dropped_pages, used_pages = built
        source_text = render_full_source(snapshot)
        block_count = sum(len(page.blocks) for page in snapshot.pages)
        log.append(
            f"[{tag}] {str(row['title'])[:40]} — {used_pages}쪽, "
            f"block {block_count}, {len(source_text):,}자"
            f"{' (앞부분만)' if truncated else ''}"
            f"{f' / OCR 페이지 {dropped_pages}장 제외' if dropped_pages else ''}"
        )
        if not args.execute:
            return None, "not_executed", log

        form = forms[row["doc_type"]]
        try:
            assessment: MinimalSourceAssessment = gateway.parse(
                model=args.classifier_model,
                system_prompt=classifier_prompts[row["doc_type"]],
                user_prompt=render_minimal_classifier_user_prompt(source_text),
                response_model=MinimalSourceAssessment,
                max_output_tokens=args.max_output_tokens,
            ).parsed
        except Exception as exc:  # noqa: BLE001 - 한 건이 배치를 끊지 않는다
            log.append(f"  [skip] 판별 실패: {type(exc).__name__}: {exc}")
            return None, f"classifier_failed:{type(exc).__name__}", log

        subclause = assessment.primary_subclause
        ground_index = select_minimal_ground(subclause, assessment.business_context)
        patterns = SUBCLAUSE_GENERATION_RULES[subclause].document_patterns
        ground_id = GROUND_IDS[ground_index]
        log.append(
            f"  판별: {subclause.value} / 형식 {form.value} "
            f"-> 조건{ground_id} {patterns[ground_index].partition(':')[0]}"
        )

        try:
            generation: GeneratorResponse = gateway.parse(
                model=args.generator_model,
                system_prompt=render_minimal_generator_system_prompt(
                    subclause, ground_index=ground_index
                ),
                user_prompt=render_minimal_generator_user_prompt(
                    source_text,
                    layout_analysis=assessment.layout_analysis,
                    available_slots=_render_slot_table(assessment),
                ),
                response_model=GeneratorResponse,
                max_output_tokens=args.max_output_tokens,
            ).parsed
        except Exception as exc:  # noqa: BLE001
            log.append(f"  [skip] 생성 실패: {type(exc).__name__}: {exc}")
            return None, f"generator_failed:{type(exc).__name__}", log

        log.append(
            f"  생성 완료 — 심은 자료 {', '.join(generation.planted_grounds)} / "
            f"변형 {len(generation.transformations)}곳"
        )
        record = {
            "source_row": {
                key: (str(value) if key == "production_date" else value)
                for key, value in row.items()
            },
            "source_file": str(path),
            "source_document_id": snapshot.source_document_id,
            "source_title": title,
            "source_truncated": truncated,
            "source_pages_used": used_pages,
            "source_ocr_pages_dropped": dropped_pages,
            "source_block_count": block_count,
            "source_text": source_text,
            "assessment": assessment.model_dump(mode="json"),
            # 판별기가 고른 것이 아니라 DB의 doc_type에서 유도한 값이다.
            "document_form": form.value,
            "clause_no": clause_of_subclause(subclause).value,
            "subclause_key": subclause.value,
            "ground_id": ground_id,
            "generation": generation.model_dump(
                mode="json", exclude_computed_fields=True
            ),
            "classifier_model": args.classifier_model,
            "generator_model": args.generator_model,
            "classifier_version": MINIMAL_CLASSIFIER_VERSION,
            "prompt_version": MINIMAL_PROMPT_VERSION,
        }
        return record, "ok", log

    pending = []
    for row in rows:
        if args.resume and (docs_dir / f"{row['doc_type']}-{row['id']}.json").exists():
            _count(drops, "already_done")
            continue
        pending.append(row)

    started = time.monotonic()
    with records_path.open("a" if args.resume else "w", encoding="utf-8") as out:
        # 문서끼리는 독립이고 시간의 대부분이 추론 대기다. 순차로 돌리면
        # 85건짜리 배치가 4시간을 넘는다.
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {pool.submit(process, row): row for row in pending}
            for future in as_completed(futures):
                record, reason, log = future.result()
                for line in log:
                    print(line, flush=True)
                if record is None:
                    if reason != "not_executed":
                        _count(drops, reason)
                    continue
                done += 1
                row = futures[future]
                _count(clause_counts, f"제{record['clause_no']}호")
                _count(subclause_counts, record["subclause_key"])
                _count(form_counts, record["document_form"])
                _count(doc_type_done, row["doc_type"])
                elapsed = time.monotonic() - started
                print(
                    f"  [{done}/{len(pending)}] {elapsed / 60:.1f}분 경과",
                    flush=True,
                )
                (docs_dir / f"{row['doc_type']}-{row['id']}.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()

    if not args.execute:
        print("\n--execute 없이 돌렸다. 실호출은 하지 않았다.")
        return 0

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        # 비밀번호는 담지 않는다. 어느 DB였는지만 남긴다.
        "database": f"{settings['host']}:{settings['port']}/{settings['database']}",
        "from_rds": args.from_rds,
        "classifier_version": MINIMAL_CLASSIFIER_VERSION,
        "prompt_version": MINIMAL_PROMPT_VERSION,
        "classifier_model": args.classifier_model,
        "generator_model": args.generator_model,
        "per_doc_type": args.per_doc_type,
        "document_form_by_doc_type": {
            doc_type: form.value for doc_type, form in sorted(forms.items())
        },
        "max_source_pages": args.max_source_pages,
        "max_source_chars": args.max_source_chars,
        "candidates": len(rows),
        "generated": done,
        "by_doc_type": doc_type_done,
        "clause_counts": clause_counts,
        "subclause_counts": subclause_counts,
        "document_form_counts": form_counts,
        "drops": drops,
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if clause_counts:
        print("\n호 분포: " + " / ".join(
            f"{name} {count}" for name, count in sorted(clause_counts.items())
        ))
    if subclause_counts:
        print("세부유형 분포: " + " / ".join(
            f"{name} {count}" for name, count in sorted(subclause_counts.items())
        ))
    if drops:
        print(f"탈락 {sum(drops.values())}건: " + " / ".join(
            f"{reason} {count}" for reason, count in sorted(drops.items())
        ))
    print(f"\n{done}건 -> {records_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
