"""서울 정보소통광장 결재문서(hwpx)를 원문으로 삼아 생성 파이프라인을 돌린다.

앞선 `generate_official_letters.py`는 원문 없이 LLM 한 번만 호출해 공문 모양
문서를 만들었다 — 유형 판별과 독립 검사도 거치지 않아 라벨과 provenance가 없다.
이 스크립트는 **실제 공문 원문**을 넣고 판별→생성→blind 검사와 재생성 게이트를
거친 뒤, 승인된 문서만 PDF로 렌더링한다.

원문은 서울시 결재문서다. 수신·결재선·시행번호·접수란·기관 연락처가 실제로
들어 있고, 부분공개 문서는 본문에 마스킹(`****`)과 `부분공개(6)` 같은 근거
표기까지 남아 있다. 공문 형식을 배울 재료로는 이보다 나은 것이 없다.

수집 코퍼스(RDS)에는 이 소스가 아직 없다 — 한국 정부 사이트가 EC2 IP를
차단해 서버에서는 수집이 안 되고 로컬에서만 된다(TODOS의 open_go_kr 건과 같은
패턴). 그래서 DB가 아니라 이미 내려받은 파일에서 직접 읽는다.
"""

from __future__ import annotations

import argparse
from html import escape
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

from rd2.extraction.hwp_text import extract_hwp_document  # noqa: E402
from rd2.generators.output_naming import generation_output_filename  # noqa: E402
from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
    expected_classification,
)
from rd2.source_generation.contracts import (  # noqa: E402
    GenerationMode,
    GenerationTarget,
    SensitivePipelineStatus,
    SourceDocumentSnapshot,
    TargetClassification,
)
from rd2.source_generation.document_form import check_document_form  # noqa: E402
from rd2.source_generation.document_select import (  # noqa: E402
    SelectionConfig,
    prepare_document_selection,
)
from rd2.source_generation.pipeline import (  # noqa: E402
    OpenAIResponsesGateway,
    PipelineConfig,
    RetryingGateway,
    run_source_sensitive_pipeline,
)
from rd2.source_generation.prompts import build_prompt_bundle  # noqa: E402

BLOCKS_PER_PAGE = 12
load_dotenv(ROOT / ".env")


def _snapshot_from_hwpx(path: Path, data_root: Path) -> tuple[SourceDocumentSnapshot, str] | None:
    """추출기가 낸 page/line 구조를 그대로 snapshot block으로 옮긴다."""

    extracted = extract_hwp_document(path, data_root=data_root)
    if extracted.get("status") != "ok":
        return None

    texts: list[str] = []
    for page in extracted.get("pages") or []:
        for line in page.get("lines") or []:
            text = (line.get("text") or "").strip()
            if text:
                texts.append(text)
    if not texts:
        return None

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

    doc_id = str(extracted.get("doc_id") or path.stem)
    snapshot = SourceDocumentSnapshot.model_validate(
        {
            "source_document_id": f"seoul_opengov-{doc_id}",
            "source": "seoul_opengov",
            "manifest_key": "seoul-official-batch",
            "source_sha256": extracted["source_sha256"],
            "pages": pages,
        }
    )
    # 첫 몇 줄에 제목이 들어 있는 경우가 많다. 없으면 문서 ID로 대체한다.
    title = next((t for t in texts if len(t) > 6), doc_id)
    return snapshot, title


def _targets() -> list[GenerationTarget]:
    """이번 batch는 원문 참고 제6호 S 생성만 순환한다."""

    targets: list[GenerationTarget] = []
    clause = ClauseNumber.CLAUSE_6
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
    return targets


def _receipt_json(receipt) -> dict | None:
    if receipt is None:
        return None
    return {
        "stage": receipt.stage.value,
        "model_id": receipt.model_id,
        "response_id": receipt.response_id,
        "request_id": receipt.request_id,
        "token_usage": (
            receipt.token_usage.model_dump(mode="json")
            if receipt.token_usage is not None
            else None
        ),
    }


def _json_pre(value) -> str:
    return escape(json.dumps(value, ensure_ascii=False, indent=2))


def _generated_document_html(record: dict) -> str:
    title = escape(str(record.get("generated_title") or "생성 문서"))
    body = escape(str(record.get("generated_body") or ""))
    route = escape(str(record.get("generation_route") or "unknown"))
    target = _json_pre(record.get("generation_final_target"))
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>{title}</title>
<style>
body{{font-family:"Malgun Gothic",sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:#172033}}
h1{{border-bottom:3px solid #23395d;padding-bottom:14px}} .meta{{background:#f3f6fa;padding:16px;border-radius:10px}}
pre{{white-space:pre-wrap;line-height:1.7;font-family:inherit}} code{{white-space:pre-wrap}}
</style></head><body>
<h1>{title}</h1>
<div class="meta"><strong>generation route</strong>: {route}<br>
<strong>target</strong><pre>{target}</pre></div>
<h2>생성 본문</h2><pre>{body}</pre>
</body></html>"""


def _attach_render_outputs(
    records: list[dict],
    render_manifest: list[dict],
    *,
    out_dir: Path,
) -> None:
    records_by_stem = {
        Path(str(record["output_filename"])).stem: record
        for record in records
        if record.get("output_filename")
    }
    for entry in render_manifest:
        record = records_by_stem.get(str(entry.get("document_id") or ""))
        if record is None:
            continue
        record["render_status"] = entry.get("status")
        if entry.get("status") != "ok":
            record["approval_status"] = SensitivePipelineStatus.PIPELINE_FAILED.value
            record["succeeded"] = False
            record["failure_stage"] = "render"
            record["failure_code"] = "render_or_pdf_evidence_failed"
            record["failure_message"] = entry.get("error")
            continue
        manifest_path = Path(str(entry["output_dir"])) / "manifest.json"
        rendered = json.loads(manifest_path.read_text(encoding="utf-8"))
        record["rendered_pdfs"] = [
            str(Path(str(item["pdf"])).resolve().relative_to(out_dir.resolve()))
            for item in rendered
        ]
        record["pdf_evidence"] = [
            item.get("sensitive_evidence")
            for item in rendered
            if item.get("sensitive_evidence") is not None
        ]


def _report_section(record: dict, index: int) -> str:
    approval = str(record.get("approval_status") or "pipeline_failed")
    status, status_class = {
        "accepted_s": ("S 학습 승인", "ok"),
        "hard_case_review": ("사람 검수 필요", "review"),
        "excluded_after_retry": ("재생성 후 제외", "excluded"),
        "pipeline_failed": ("파이프라인 실패", "fail"),
    }.get(approval, ("처리 실패", "fail"))
    source = escape(str(record.get("source_file") or ""))
    output = escape(str(record.get("output_filename") or "생성 파일 없음"))
    generated_html = record.get("generated_html")
    output_link = (
        f'<a href="{escape(str(generated_html))}">{output}</a>'
        if generated_html
        else output
    )
    pdf_links = " ".join(
        f'<a href="{escape(str(path))}">PDF {pdf_index}</a>'
        for pdf_index, path in enumerate(record.get("rendered_pdfs") or (), start=1)
    )
    steps = (
        ("1. 원문", record.get("source_text")),
        ("2. 유형 판별 결과", record.get("source_assessment")),
        ("3. 잠긴 생성 계획", record.get("generation_plan")),
        ("4. 시도별 생성 → blind 검사", record.get("attempts")),
        ("5. 최종 생성 결과", record.get("generation_artifact")),
        ("6. 최종 S/O 관계 검사", record.get("consistency_assessment")),
        ("7. 목표 대비 비교", record.get("comparison")),
        ("8. PDF 렌더링·근거 보존", {
            "render_status": record.get("render_status"),
            "pdf_evidence": record.get("pdf_evidence"),
        }),
        ("9. 승인 상태", {
            "approval_status": approval,
            "technical_succeeded": record.get("technical_succeeded"),
            "attempt_count": record.get("attempt_count"),
        }),
        ("10. 실패 정보", {
            "stage": record.get("failure_stage"),
            "code": record.get("failure_code"),
            "message": record.get("failure_message"),
        } if not record.get("technical_succeeded") else None),
    )
    details = []
    for label, value in steps:
        if value is None:
            rendered = '<p class="empty">결과 없음</p>'
        elif isinstance(value, str):
            rendered = f"<pre>{escape(value)}</pre>"
        else:
            rendered = f"<pre>{_json_pre(value)}</pre>"
        details.append(f"<details {'open' if label.startswith(('2.', '3.', '5.')) else ''}>"
                       f"<summary>{escape(label)}</summary>{rendered}</details>")
    return (
        f'<section id="doc-{index}"><h2>{index}. {source} '
        f'<span class="{status_class}">{status}</span></h2>'
        f'<p><strong>생성 파일:</strong> {output_link}</p>'
        f'<p><strong>렌더링:</strong> {pdf_links or "없음"}</p>'
        f'{"".join(details)}</section>'
    )


def _write_html_outputs(records: list[dict], out_dir: Path) -> None:
    html_dir = out_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    used: dict[str, int] = {}
    for record in records:
        if not record.get("generated_title"):
            continue
        base = Path(str(record["output_filename"])).stem
        count = used.get(base, 0)
        used[base] = count + 1
        suffix = f"__{count + 1}" if count else ""
        html_name = f"{base}{suffix}.html"
        (html_dir / html_name).write_text(
            _generated_document_html(record),
            encoding="utf-8",
        )
        record["generated_html"] = str(Path("html") / html_name)

    nav = "".join(
        f'<a href="#doc-{index}">{index}. {escape(str(record.get("source_file") or ""))}</a>'
        for index, record in enumerate(records, start=1)
    )
    sections = "".join(
        _report_section(record, index)
        for index, record in enumerate(records, start=1)
    )
    report = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>서울 원문 생성 파이프라인 10건</title>
<style>
body{{font-family:"Malgun Gothic",sans-serif;background:#eef2f7;color:#172033;margin:0}}
header{{background:#172a46;color:white;padding:32px max(24px,calc((100% - 1100px)/2))}}
main{{max-width:1100px;margin:24px auto;padding:0 20px}} nav{{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}}
nav a{{background:white;border:1px solid #ccd5e2;border-radius:8px;padding:8px 10px;color:#244a78;text-decoration:none}}
section{{background:white;border-radius:14px;padding:22px;margin:18px 0;box-shadow:0 3px 16px #1b355014}}
details{{border-top:1px solid #dce3ec;padding:12px 0}} summary{{font-weight:700;cursor:pointer}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fb;padding:14px;border-radius:8px;line-height:1.55}}
  .ok{{color:#087443;font-size:.72em}} .review{{color:#9a6700;font-size:.72em}}
  .excluded{{color:#7c3aed;font-size:.72em}} .fail{{color:#b42318;font-size:.72em}}
  .empty{{color:#6b7280}}
</style></head><body><header><h1>서울 원문 생성 파이프라인</h1>
<p>원문 → 유형 판별 → 잠긴 계획 → 생성 → blind S/O 관계 검사 → 재생성/검수/승인</p></header>
<main><nav>{nav}</nav>{sections}</main></body></html>"""
    (out_dir / "pipeline_report.html").write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=ROOT / "data" / "seoul_opengov" / "official_document" / "1-500",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--classifier-model", default="gpt-4o")
    parser.add_argument("--generator-model", default="gpt-4o")
    parser.add_argument("--validator-model", default="gpt-4o-mini")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    files = sorted(args.source_dir.glob("*.hwpx"))
    if not files:
        print(f"hwpx 파일이 없다: {args.source_dir}")
        return 1

    gateway = RetryingGateway(
        OpenAIResponsesGateway(OpenAI(api_key=os.environ["OPENAI_API_KEY"])),
        max_attempts=args.max_attempts,
    )
    config = PipelineConfig(
        classifier_model=args.classifier_model,
        generator_model=args.generator_model,
        validator_model=args.validator_model,
        reference_date=date.today(),
        source_sensitive_mode=True,
    )
    selection_config = SelectionConfig()
    prompt_bundle = build_prompt_bundle(selection_config)
    targets = _targets()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    records_path = args.out_dir / "batch_records.jsonl"
    payload_path = args.out_dir / "render_payloads.jsonl"

    done = 0
    report_records: list[dict] = []
    with records_path.open("w", encoding="utf-8") as records, payload_path.open(
        "w", encoding="utf-8"
    ) as payloads:
        for path in files:
            if done >= args.count:
                break
            built = _snapshot_from_hwpx(path, ROOT / "data")
            if built is None:
                continue
            snapshot, title = built
            prepared = prepare_document_selection(snapshot, selection_config)
            if prepared.selection is None:
                continue

            done += 1
            source_text = "\n\n".join(
                block.text for page in snapshot.pages for block in page.blocks
            )
            # 판별기는 파이프라인 안에서 한 번만 호출된다. 이 값은 O 원문일 때
            # 사용할 제6호 fallback 요청이며, S 원문이면 플래너가 원문 라벨을
            # 그대로 잠근다.
            target = targets[(done - 1) % len(targets)]
            target_source = "clause-6-fallback-cycle"
            print(
                f"[{done}/{args.count}] {snapshot.source_document_id} <- "
                f"{target.classification.value}/{target.clause_no.value}"
                f"/{target.subclause_key.value} ({target_source})"
            )

            sensitive_run = run_source_sensitive_pipeline(
                snapshot=snapshot,
                selection=prepared.selection,
                counterfactual_target=target,
                gateway=gateway,
                config=config,
                selection_config=selection_config,
                prompt_bundle=prompt_bundle,
            )
            result = sensitive_run.final_result

            record: dict = {
                "source_document_id": snapshot.source_document_id,
                "source_file": path.name,
                "source_title": title,
                "source_block_count": sum(len(p.blocks) for p in snapshot.pages),
                "source_text": source_text,
                "target_source": target_source,
                "requested_target": target.model_dump(mode="json"),
                "approval_status": sensitive_run.status.value,
                "technical_succeeded": result.succeeded,
                "succeeded": (
                    sensitive_run.status == SensitivePipelineStatus.ACCEPTED_S
                ),
                "attempt_count": len(sensitive_run.attempts),
                "attempts": [
                    {
                        "attempt": attempt_no,
                        "technical_succeeded": attempt.succeeded,
                        "failure": (
                            attempt.failure.model_dump(mode="json")
                            if attempt.failure is not None
                            else None
                        ),
                        "generated_document": (
                            attempt.generation_artifact.generated_document.model_dump(
                                mode="json"
                            )
                            if attempt.generation_artifact is not None
                            else None
                        ),
                        "consistency_assessment": (
                            attempt.consistency_assessment.model_dump(mode="json")
                            if attempt.consistency_assessment is not None
                            else None
                        ),
                    }
                    for attempt_no, attempt in enumerate(
                        sensitive_run.attempts,
                        start=1,
                    )
                ],
            }
            if result.failure is not None:
                record["failure_stage"] = result.failure.stage.value
                record["failure_code"] = result.failure.code.value
                record["failure_message"] = result.failure.message
            if (
                result.source_assessment is not None
                and result.generation_plan is not None
                and result.generation_artifact is not None
            ):
                assessment = result.source_assessment
                plan = result.generation_plan
                generation = result.generation_artifact
                document = generation.generated_document
                form = check_document_form(
                    document,
                    plan.final_target,
                    document_form=assessment.source_classification.document_form,
                )
                output_filename = generation_output_filename(
                    generation_route=plan.generation_route.value,
                    source_filename=path.name,
                    generated_title=document.title,
                )
                record.update(
                    {
                        "source_document_form": (
                            assessment.source_classification.document_form.value
                        ),
                        "source_classification": (
                            assessment.source_classification.classification.value
                        ),
                        "source_evidence_level": (
                            assessment.source_suitability.evidence_level.value
                        ),
                        "generation_route": plan.generation_route.value,
                        "generation_final_target": plan.final_target.model_dump(
                            mode="json"
                        ),
                        "generated_title": document.title,
                        "output_filename": output_filename,
                        "generated_blocks": [b.kind for b in document.blocks],
                        "generated_body": document.body_text,
                        "source_assessment": assessment.model_dump(mode="json"),
                        "generation_plan": plan.model_dump(mode="json"),
                        "generation_artifact": generation.model_dump(mode="json"),
                        "classification_receipt": _receipt_json(
                            result.classification_receipt
                        ),
                        "generation_receipt": _receipt_json(
                            result.generation_receipt
                        ),
                        "form_has_header": form.has_header,
                        "form_missing": list(form.missing),
                    }
                )
                if sensitive_run.status == SensitivePipelineStatus.ACCEPTED_S:
                    payloads.write(
                        json.dumps(
                            {
                                "output_filename": output_filename,
                                "approval_status": sensitive_run.status.value,
                                "consistency_assessment": (
                                    result.consistency_assessment.model_dump(
                                        mode="json"
                                    )
                                    if result.consistency_assessment is not None
                                    else None
                                ),
                                "result": {
                                    "contract_version": (
                                        document.contract_version
                                    ),
                                    "generated_document": (
                                        document.model_dump(mode="json")
                                    ),
                                },
                                "receipt": {
                                    "request_id": (
                                        f"seoul-{snapshot.source_document_id}"
                                    ),
                                    "model_id": args.generator_model,
                                    "response_id": (
                                        result.generation_receipt.response_id
                                        if result.generation_receipt
                                        else "unknown"
                                    ),
                                },
                                "provenance": {
                                    "source_document_id": snapshot.source_document_id,
                                    "generation_route": plan.generation_route.value,
                                    "document_form": (
                                        assessment.source_classification.document_form.value
                                    ),
                                },
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            if result.consistency_assessment is not None:
                consistency = result.consistency_assessment
                record.update(
                    {
                        "validation_form": consistency.document_form.value,
                        "validation_classification": (
                            consistency.classification.value
                        ),
                        "validation_clause": (
                            consistency.clause_no.value
                            if consistency.clause_no
                            else None
                        ),
                        "validation_subclause": (
                            consistency.subclause_key.value
                            if consistency.subclause_key
                            else None
                        ),
                        "consistency_assessment": consistency.model_dump(
                            mode="json"
                        ),
                        "validation_receipt": _receipt_json(
                            result.validation_receipt
                        ),
                    }
                )
            if result.comparison is not None:
                record["comparison"] = result.comparison.model_dump(mode="json")
            records.write(json.dumps(record, ensure_ascii=False) + "\n")
            report_records.append(record)
            records.flush()
            payloads.flush()

    if payload_path.stat().st_size:
        from scripts.render_generated_documents import render_input_file

        render_manifest = render_input_file(
            payload_path,
            args.out_dir / "rendered",
            per_template=1,
            base_seed=20260729,
        )
        _attach_render_outputs(
            report_records,
            render_manifest,
            out_dir=args.out_dir,
        )
        records_path.write_text(
            "".join(
                f"{json.dumps(record, ensure_ascii=False)}\n"
                for record in report_records
            ),
            encoding="utf-8",
        )

    (args.out_dir / "summary.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "prompt_bundle": prompt_bundle.version,
                "taxonomy_version": prompt_bundle.taxonomy_version,
                "classifier_prompt_sha256": prompt_bundle.definition(
                    "classifier"
                ).sha256,
                "generator_prompt_sha256": prompt_bundle.definition(
                    "sensitive_generator"
                ).sha256,
                "validator_prompt_sha256": prompt_bundle.definition(
                    "sensitive_validator"
                ).sha256,
                "classifier_model": args.classifier_model,
                "generator_model": args.generator_model,
                "validator_model": args.validator_model,
                "source_dir": str(args.source_dir),
                "processed": done,
                "approval_counts": {
                    status.value: sum(
                        record.get("approval_status") == status.value
                        for record in report_records
                    )
                    for status in SensitivePipelineStatus
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_html_outputs(report_records, args.out_dir)
    print(f"\n{done}건 -> {records_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
