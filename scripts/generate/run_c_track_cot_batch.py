"""등록된 C트랙 템플릿 전체를 **문서형식마다 N건씩** 3단계 경로로 생성한다.

``run_c_cot_generation.py``가 프레임 하나에 하는 일(고정 접두사 + 이 건의 조건 ->
``CTrackCoTResponse`` 1회 호출 -> 선택적 ``documents`` 행)을 그대로 배치로 돌린다.
다른 점은 **무엇을 생성할지 고르는 방법**뿐이다.

    등록 템플릿 12종
        -> 템플릿마다 select_frames_per_document_form(per_form)
        -> 형식별 N건 (템플릿이 쓰는 형식만)
        -> 3단계 프롬프트 1회 호출 -> JSON 기록 (-> 선택적 documents 행)

**형식으로 세는 이유.** ``expand_cases``의 앞 N건을 그냥 쓰면 형식 분포가
``subject_cases`` 구성을 물려받는다 — 법무부 템플릿은 18개 쌍 중 6개가
``plan_draft``이고 ``response_plan``은 1개뿐이라 계획안이 6배로 나온다. 형식별
할당량은 ``c_track_templates.select_frames_per_document_form``이 셔플 순서를
걸어가며 채운다.

**형식마다 조합 수가 다르다.** 같은 N건이어도 ``subject_case``가 하나뿐인 형식은
사안이 전부 같고 부서·장소·촉발계기만 다르다. 다양성이 형식마다 비대칭이라는
사실은 산출물에서 보여야 하므로 ``summary.json``에 형식별 조합 수와 실제 뽑힌
건수를 함께 남긴다.

**기관 단위로 병렬이다.** 레인 하나가 기관 하나를 맡고(``--agency-concurrency``),
레인 안에서 문서가 동시에 나간다(``--concurrency``). 평평한 풀에 전부 던지지 않는
이유는 고정 접두사가 기관·세부조항이 잠긴 통짜 문자열이기 때문이다 — 동시에 나가는
건이 서로 다른 기관이면 프롬프트 캐시가 걸리지 않는다.

**한 건이 배치를 끊지 않는다.** 호출 실패·계약 위반은 사유를 세어 ``summary.json``
의 ``drops``에 남기고 다음 건으로 넘어간다.

기본값은 실호출을 하지 않는다. ``--execute`` 없이 돌리면 어떤 템플릿의 어떤
형식이 몇 건 뽑혔는지까지만 보여 주고 과금되지 않는다. 호출은 문서당 1회다.

사용:
    # 대상 확인만 (무료)
    python scripts/generate/run_c_track_cot_batch.py --out-dir output/c_cot

    # 실제 호출 (과금 — 문서당 1회)
    python scripts/generate/run_c_track_cot_batch.py --out-dir output/c_cot \\
        --per-document-form 3 --execute

    # 기관 3곳을 동시에, 기관마다 문서 4건씩 (동시 호출 12)
    python scripts/generate/run_c_track_cot_batch.py --out-dir output/c_cot \\
        --per-document-form 3 --agency-concurrency 3 --execute

    # 검증용 DB에 바로 넣기
    python scripts/generate/run_c_track_cot_batch.py --out-dir output/c_cot \\
        --per-document-form 1 --execute --save-to-db --database rd2_test
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 콘솔 기본 코드페이지가 cp949라 프롬프트·제목의 em dash 하나에 죽는다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# conda 기본값이 없는 파일을 가리켜 OpenAI 호출이 요청 전에 FileNotFoundError로
# 죽는다. certifi 번들로 덮어쓴다 — import 시점이어야 openai가 집어 간다.
if not Path(os.environ.get("SSL_CERT_FILE", "")).exists():
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from rd2.source_generation.c_track_templates import (  # noqa: E402
    C_TRACK_COT_PROMPT_VERSION,
    C_TRACK_TEMPLATE_VERSION,
    C_TRACK_TEMPLATES,
    CaseFrame,
    CTrackTemplate,
    render_cot_case_section,
    render_cot_fixed_prefix,
    select_frames_for_agency,
    select_frames_per_document_form,
)
from rd2.source_generation.c_track_writeback import (  # noqa: E402
    build_c_track_row,
    c_track_source_document_id,
)
from rd2.source_generation.contracts import CTrackCoTResponse  # noqa: E402
from rd2.source_generation.gateway import default_openai_gateway  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402


def _template_key(template: CTrackTemplate) -> str:
    return f"{template.subclause_key.value}/{template.agency}"


def _count(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _resolve_templates(selectors: list[str]) -> list[CTrackTemplate]:
    """``--template`` 인자를 템플릿 목록으로 옮긴다. 없으면 등록된 전체다."""

    if not selectors:
        return list(C_TRACK_TEMPLATES.values())

    chosen: list[CTrackTemplate] = []
    for selector in selectors:
        subclause_value, _, agency = selector.partition("/")
        matches = [
            template
            for key, template in C_TRACK_TEMPLATES.items()
            if key[0].value == subclause_value and (not agency or key[1] == agency)
        ]
        if not matches:
            raise SystemExit(f"등록되지 않은 템플릿이다: {selector}")
        for template in matches:
            if template not in chosen:
                chosen.append(template)
    return chosen


def _form_case_counts(template: CTrackTemplate) -> dict[str, int]:
    """형식별 조합 수. 같은 N건이어도 다양성이 어디서 얇은지가 여기서 보인다.

    세는 것은 템플릿이 한다. 여기서 다시 세던 때 축이 하나 늘자 이 값만 6배
    어긋났고, ``summary.json``에만 실리는 값이라 아무 데서도 예외가 나지 않았다.
    """

    return {
        form.value: count for form, count in template.form_case_counts.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    # 세는 단위가 둘이다. 형식이 기준이면 총량이 형식 수(5~8)에 끌려가 기관마다
    # 건수가 다르고, 기관이 기준이면 총량이 고정되고 형식이 그 안에서 고르게
    # 돈다. 둘을 함께 주면 어느 쪽이 이겼는지가 산출물에 안 남으므로 막는다.
    counting = parser.add_mutually_exclusive_group()
    counting.add_argument(
        "--per-document-form",
        type=int,
        default=None,
        help=(
            "템플릿마다 문서형식 하나당 몇 건을 생성할지 (기본값 1)."
            " 호출 수는 (형식 수 × N)이라 기관마다 다르다"
        ),
    )
    counting.add_argument(
        "--per-agency",
        type=int,
        default=None,
        help=(
            "템플릿마다 모두 합쳐 몇 건을 생성할지. 형식은 그 안에서 고르게 돈다"
            " — N이 형식 수보다 작으면 서로 다른 형식이 N개 나온다"
        ),
    )
    parser.add_argument(
        "--template",
        action="append",
        default=[],
        help=(
            "`{세부조항}/{기관}` 또는 `{세부조항}`. 여러 번 줄 수 있다. "
            "생략하면 등록된 전체를 돈다. "
            f"등록된 것: {', '.join(f'{k.value}/{a}' for k, a in C_TRACK_TEMPLATES)}"
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="expand_cases 전개 시드")
    parser.add_argument("--model", default="gpt-5")
    # 단일 출력 경로보다 높다. 분석·발췌·저장 사유가 같은 응답에 들어가므로
    # 16,000에서는 본문이 밀려 LengthFinishReasonError로 죽을 수 있다.
    parser.add_argument("--max-output-tokens", type=int, default=24000)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help=(
            "**한 기관 안에서** 동시에 처리할 문서 수."
            " 시간의 대부분이 추론 대기라 그만큼 줄어든다"
        ),
    )
    parser.add_argument(
        "--agency-concurrency",
        type=int,
        default=1,
        help=(
            "동시에 도는 기관 수. 기본 1이면 기관을 하나씩 끝내고 넘어간다."
            " 실제 동시 호출 수는 이 값 × --concurrency다"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "고른 건 중 앞에서 N건만 처리한다(0이면 전량)."
            " 과금되는 실호출 전에 한두 건으로 흐름을 확인할 때 쓴다"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="out-dir에 이미 결과 파일이 있는 건은 건너뛴다",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 LLM을 호출한다. 문서당 1회 호출이며 그대로 과금된다.",
    )
    # 생성 직후 저장. 사후 되쓰기(writeback_c_track_to_rds)와 **같은 조립 함수**를
    # 부르므로 어느 쪽으로 넣어도 같은 행이 된다.
    parser.add_argument(
        "--save-to-db",
        action="store_true",
        help="생성 결과를 곧바로 documents 행으로 넣는다 (--execute 필요)",
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
    args = parser.parse_args()

    if args.save_to_db and not args.execute:
        raise SystemExit("--save-to-db는 --execute와 함께 써라. 넣을 문서가 없다")
    per_agency = args.per_agency
    per_form = args.per_document_form
    if per_agency is None and per_form is None:
        per_form = 1
    if per_form is not None and per_form < 1:
        raise SystemExit("--per-document-form은 1 이상이어야 한다")
    if per_agency is not None and per_agency < 1:
        raise SystemExit("--per-agency는 1 이상이어야 한다")

    templates = _resolve_templates(args.template)

    # 계획을 먼저 세운다. 형식별 부족분은 여기서만 알 수 있다 — 뽑힌 건수만 보면
    # "그 형식은 원래 없었다"와 "조합이 모자랐다"가 구분되지 않는다.
    plan: list[tuple[CTrackTemplate, CaseFrame]] = []
    shortfalls: dict[str, dict[str, int]] = {}
    unit = (
        f"기관당 {per_agency}건"
        if per_agency is not None
        else f"형식당 {per_form}건"
    )
    print(
        f"템플릿 {len(templates)}종 × {unit} "
        f"(seed={args.seed}, 프롬프트 {C_TRACK_TEMPLATE_VERSION} + "
        f"{C_TRACK_COT_PROMPT_VERSION})\n"
    )
    for template in templates:
        if per_agency is not None:
            frames = select_frames_for_agency(template, per_agency, seed=args.seed)
        else:
            frames = select_frames_per_document_form(template, per_form, seed=args.seed)
        picked: dict[str, int] = {}
        for frame in frames:
            _count(picked, frame.document_form.value)
            plan.append((template, frame))
        available = _form_case_counts(template)
        if per_agency is not None:
            # 기관 기준일 때 모자람은 형식이 아니라 총량에서 난다. 형식별 0건은
            # 부족이 아니라 **그만큼만 요구했다**는 뜻이다.
            if len(frames) < per_agency:
                shortfalls[_template_key(template)] = {
                    "총량": per_agency - len(frames)
                }
        else:
            missing = {
                form: per_form - picked.get(form, 0)
                for form in available
                if picked.get(form, 0) < per_form
            }
            if missing:
                shortfalls[_template_key(template)] = missing
        print(
            f"{_template_key(template)} — {len(frames)}건 "
            f"(전체 조합 {template.case_count:,})"
        )
        for form in sorted(available):
            demanded = per_form if per_agency is None else picked.get(form, 0)
            note = "" if available[form] >= demanded else "  ← 조합 부족"
            print(
                f"    {form:22s} {picked.get(form, 0)}건 / 조합 {available[form]:,}{note}"
            )
    print(f"\n합계 {len(plan)}건 = 호출 {len(plan)}회")

    if args.limit and args.limit < len(plan):
        # 잘라낸 사실을 반드시 찍는다. 조용히 줄이면 산출물만 봐서는 "고른 것이
        # 이것뿐"이었는지 상한에 걸린 것인지 구분되지 않는다.
        print(f"--limit {args.limit}: 고른 {len(plan)}건 중 앞 {args.limit}건만 쓴다")
        plan = plan[: args.limit]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = args.out_dir / "documents"
    docs_dir.mkdir(exist_ok=True)
    records_path = args.out_dir / "c_cot_records.jsonl"

    def out_name(template: CTrackTemplate, frame: CaseFrame) -> str:
        return (
            f"{template.subclause_key.value}-{template.agency}"
            f"-{frame.document_form.value}-seed{args.seed}-case{frame.case_index}.json"
        )

    # 고정 접두사는 템플릿마다 한 벌이다. 같은 문자열이어야 프롬프트 캐시에 걸린다.
    system_prompts = {
        _template_key(template): render_cot_fixed_prefix(template)
        for template in templates
    }

    if not args.execute:
        print("\n--execute 없이 돌렸다. 실호출은 하지 않는다.")
        return 0

    pending: list[tuple[CTrackTemplate, CaseFrame]] = []
    drops: dict[str, int] = {}
    for template, frame in plan:
        if args.resume and (docs_dir / out_name(template, frame)).exists():
            _count(drops, "already_done")
            continue
        pending.append((template, frame))

    gateway = default_openai_gateway()
    store = (
        DocumentStore(
            host=args.db_host,
            port=args.db_port,
            user=args.db_user,
            password=args.db_password,
            database=args.database,
        )
        if args.save_to_db
        else None
    )
    if store is not None:
        print(f"\nDB: {store.user}@{store.host}:{store.port}/{store.database}")

    def process(
        item: tuple[CTrackTemplate, CaseFrame]
    ) -> tuple[tuple[CTrackTemplate, CaseFrame], dict | None, str, list[str]]:
        """문서 하나를 생성한다. 예외를 밖으로 내지 않으므로 배치가 끊기지 않는다.

        스레드에서 돌기 때문에 공유 상태를 건드리지 않고 로그도 모아서 돌려준다 —
        여러 문서의 진행 줄이 섞이면 읽을 수 없다. DB 쓰기도 여기서 하지 않는다
        (``DocumentStore``는 커넥션 하나를 autocommit 없이 들고 있다).

        받은 ``item``을 그대로 돌려준다. 레인이 둘 이상이면 결과가 큐로 흘러
        오므로 소비하는 쪽에 future -> item 지도가 없다.
        """

        template, frame = item
        tag = f"{_template_key(template)}/case{frame.case_index}"
        system_prompt = system_prompts[_template_key(template)]
        user_prompt = render_cot_case_section(template, frame)
        log = [
            f"[{tag}] {frame.document_form.value} / "
            f"{frame.subject_case.document_name} / {frame.department} / "
            f"{frame.stage} / {frame.security_grade}"
        ]
        try:
            call = gateway.parse(
                model=args.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=CTrackCoTResponse,
                max_output_tokens=args.max_output_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - 한 건이 배치를 끊지 않는다
            log.append(f"  [skip] 생성 실패: {type(exc).__name__}: {exc}")
            return item, None, f"generation_failed:{type(exc).__name__}", log

        response: CTrackCoTResponse = call.parsed
        document = response.document
        log.append(
            f"  생성 완료 — {document.title} / block {len(document.blocks)} / "
            f"본문 {len(document.body_text):,}자 / "
            f"발췌 {len(response.confidential_snippets)}"
        )
        record = {
            "template": _template_key(template),
            "agency": template.agency,
            "template_version": C_TRACK_TEMPLATE_VERSION,
            "prompt_version": C_TRACK_COT_PROMPT_VERSION,
            # 조항은 템플릿이 잠근 값이라 모델에게 묻지 않는다.
            "legal_basis": f"9-1-{template.clause_no.value}",
            "subclause_key": template.subclause_key.value,
            "document_form": frame.document_form.value,
            "document_name": frame.subject_case.document_name,
            "department": frame.department,
            "subject": frame.subject,
            "stage": frame.stage,
            "security_grade": frame.security_grade,
            # 좌표에서 복원되는 값이지만 기록에 남긴다 — 산출물끼리 "같은 적대자
            # 조합을 받은 문서들이 실제로 다른 취약점을 말했는가"를 세려면
            # case_index를 다시 전개하지 않고도 묶을 수 있어야 한다.
            "adversaries": [adversary.who for adversary in frame.adversaries],
            "slot_values": dict(frame.slot_values),
            "case_index": frame.case_index,
            "seed": args.seed,
            "model": args.model,
            "response_id": call.response_id,
            "response": response.model_dump(mode="json"),
        }
        return item, record, "ok", log

    # 레인 하나가 기관 하나를 맡는다.
    #
    # **왜 기관으로 묶는가.** 평평한 풀에 전부 던지면 동시에 나가는 네 건이 서로
    # 다른 기관일 수 있고, 그러면 고정 접두사가 매번 달라져 프롬프트 캐시가
    # 걸리지 않는다(C트랙 접두사는 기관·세부조항이 잠긴 통짜 문자열이다). 레인
    # 안에서는 system 프롬프트가 하나뿐이라 그 안의 동시 호출이 전부 같은 접두사를
    # 공유한다.
    #
    # 진행 로그도 기관 단위로 읽힌다 — 어느 기관이 몇 건에서 죽었는지가 줄
    # 사이에서 흩어지지 않는다.
    lanes: dict[str, list[tuple[CTrackTemplate, CaseFrame]]] = {}
    for item in pending:
        lanes.setdefault(_template_key(item[0]), []).append(item)

    lane_workers = max(1, min(args.agency_concurrency, len(lanes) or 1))
    doc_workers = max(1, args.concurrency)
    print(
        f"\n동시 실행: 기관 {lane_workers} × 기관당 문서 {doc_workers} = "
        f"최대 {lane_workers * doc_workers}건이 한꺼번에 나간다"
    )

    done = 0
    saved = 0
    skipped_existing_rows = 0
    by_template: dict[str, int] = {}
    form_counts: dict[str, int] = {}
    subclause_counts: dict[str, int] = {}
    started = time.monotonic()

    #: 레인이 낸 것을 메인 스레드 한 곳에서만 소비한다. 파일 쓰기와
    #: ``DocumentStore.upsert``가 여기 있어야 하기 때문이다 — 커넥션 하나를
    #: autocommit 없이 들고 있어 여러 레인이 동시에 부르면 커밋 경계가 섞인다.
    results: "queue.Queue[tuple[str, tuple]]" = queue.Queue()

    def run_lane(key: str, items: list[tuple[CTrackTemplate, CaseFrame]]) -> None:
        """기관 하나를 끝까지 돈다. 결과는 큐로만 내보낸다."""

        lane_started = time.monotonic()
        with ThreadPoolExecutor(max_workers=doc_workers) as pool:
            futures = [pool.submit(process, item) for item in items]
            for future in as_completed(futures):
                results.put(("doc", future.result()))
        results.put(("lane", (key, len(items), time.monotonic() - lane_started)))

    try:
        with records_path.open("a" if args.resume else "w", encoding="utf-8") as out:
            with ThreadPoolExecutor(max_workers=lane_workers) as lane_pool:
                lane_futures = [
                    lane_pool.submit(run_lane, key, items)
                    for key, items in lanes.items()
                ]
                # 문서 하나마다 한 번, 레인 하나마다 한 번.
                expected = len(pending) + len(lanes)
                received = 0
                while received < expected:
                    try:
                        kind, payload = results.get(timeout=1.0)
                    except queue.Empty:
                        # 레인이 결과를 다 넣기 전에 죽으면 큐가 영원히 비어 있다.
                        # 그때 매달려 있지 않고 빠져나가 아래 result()로 이유를 낸다.
                        if all(future.done() for future in lane_futures):
                            break
                        continue
                    received += 1
                    if kind == "lane":
                        lane_key, lane_count, lane_elapsed = payload
                        print(
                            f"[{lane_key}] 레인 종료 — {lane_count}건 / "
                            f"{lane_elapsed / 60:.1f}분",
                            flush=True,
                        )
                        continue

                    (template, frame), record, reason, log = payload
                    for line in log:
                        print(line, flush=True)
                    if record is None:
                        _count(drops, reason)
                        continue
                    done += 1
                    _count(by_template, _template_key(template))
                    _count(form_counts, frame.document_form.value)
                    _count(subclause_counts, template.subclause_key.value)
                    (docs_dir / out_name(template, frame)).write_text(
                        json.dumps(record, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()

                    if store is not None:
                        response = CTrackCoTResponse.model_validate(record["response"])
                        row = build_c_track_row(
                            template=template,
                            frame=frame,
                            document=response.document,
                            seed=args.seed,
                            system_prompt=system_prompts[_template_key(template)],
                            user_prompt=render_cot_case_section(template, frame),
                            snippets=response.confidential_snippets,
                            storage_reasoning=response.reasoning_for_storage,
                            prompt_version=C_TRACK_COT_PROMPT_VERSION,
                        )
                        if store.upsert(row):
                            saved += 1
                        else:
                            # 같은 (세부조항, 기관, seed, case_index)로 이미 들어간
                            # 행이다. 새 행을 만들지 않고 스킵한 사실만 센다.
                            skipped_existing_rows += 1
                            print(
                                "  이미 있는 행이다 — 스킵: "
                                f"{c_track_source_document_id(frame, seed=args.seed)}",
                                flush=True,
                            )

                    elapsed = time.monotonic() - started
                    print(
                        f"  [{done}/{len(pending)}] {elapsed / 60:.1f}분 경과",
                        flush=True,
                    )

                # 레인 자체가 죽었으면(풀 생성 실패 등) 여기서 드러난다. 조용히
                # 지나가면 "탈락 없이 몇 건 적게 나왔다"로만 보인다.
                for future in lane_futures:
                    future.result()
    finally:
        if store is not None:
            store.close()

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "template_version": C_TRACK_TEMPLATE_VERSION,
        "prompt_version": C_TRACK_COT_PROMPT_VERSION,
        "model": args.model,
        "seed": args.seed,
        # 세는 단위가 둘이라 쓰지 않은 쪽은 null로 남는다. 건수만 봐서는 어느
        # 단위로 뽑은 표본인지 복원되지 않는다.
        "per_document_form": per_form,
        "per_agency": per_agency,
        # 동시 호출 수는 산출물 품질과 무관해 보이지만, 속도제한에 걸려 무더기로
        # 탈락한 배치와 그렇지 않은 배치를 나중에 구분하려면 남아 있어야 한다.
        "agency_concurrency": lane_workers,
        "concurrency": doc_workers,
        "templates": [_template_key(template) for template in templates],
        "planned": len(plan),
        "generated": done,
        "by_template": by_template,
        "document_form_counts": form_counts,
        "subclause_counts": subclause_counts,
        # 같은 N건이어도 형식마다 조합 수가 다르다. 다양성이 어디서 얇은지는
        # 건수가 아니라 이 값이 말한다.
        "form_case_counts": {
            _template_key(template): _form_case_counts(template)
            for template in templates
        },
        "form_shortfalls": shortfalls,
        "drops": drops,
    }
    if store is not None or args.save_to_db:
        summary["saved_rows"] = saved
        summary["skipped_existing_rows"] = skipped_existing_rows
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if form_counts:
        print("\n형식 분포: " + " / ".join(
            f"{name} {count}" for name, count in sorted(form_counts.items())
        ))
    if subclause_counts:
        print("세부유형 분포: " + " / ".join(
            f"{name} {count}" for name, count in sorted(subclause_counts.items())
        ))
    if drops:
        print(f"탈락 {sum(drops.values())}건: " + " / ".join(
            f"{reason} {count}" for reason, count in sorted(drops.items())
        ))
    if args.save_to_db:
        print(f"저장 {saved}행 (이미 있던 행 {skipped_existing_rows}건은 스킵)")
    print(f"\n{done}건 -> {records_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
