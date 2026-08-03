"""실제 공문 원문을 넣고 판별→생성→blind 검사와 재생성 게이트를 거친다.

앞선 `generate_official_letters.py`는 원문 없이 LLM 한 번만 호출해 공문 모양
문서를 만들었다 — 유형 판별과 독립 검사도 거치지 않아 라벨과 provenance가 없다.
이 스크립트는 승인된 문서만 PDF로 렌더링한다.

원문의 표준은 서울시 결재문서다. 수신·결재선·시행번호·접수란·기관 연락처가
실제로 들어 있고, 부분공개 문서는 본문에 마스킹(`****`)과 `부분공개(6)` 같은
근거 표기까지 남아 있다. 공문 형식을 배울 재료로는 이보다 나은 것이 없다.

**입력은 두 가지다.**

``--source-dir``(기본)는 이미 내려받은 로컬 hwpx/pdf를 읽는다. 서울 소스는
RDS에 없다 — 한국 정부 사이트가 EC2 IP를 차단해 서버에서는 수집이 안 되고
로컬에서만 된다(TODOS의 open_go_kr 건과 같은 패턴).

``--from-rds``는 RDS의 공개(O) 행을 골라 그 행의 ``body_file_path``가 가리키는
파일을 연다. **파일을 여는 이유**는 마스킹 자리 때문이다 — 부분공개 원문의
`****`와 `부분공개(6)` 표기가 ``mask_restoration`` route의 입력인데, 그게
살아 있는 형태는 원본 파일이다. 파일이 없는 행은 기본적으로 건너뛰고,
``--allow-body-text``를 주면 ``body_text``로 스냅샷을 만들어 처리한다.

``--commit-to-rds``를 주면 승인된(``accepted_s``) 생성물이 같은 ``documents``
테이블에 S 행으로 들어간다. 템플릿 작업이 끝나기 전이라 PDF가 없으므로
``body_file_path``는 비워두고 생성 원문과 메타데이터만 넣는다 — 템플릿이
나오면 ``DocumentStore.update_files()``로 같은 행을 백필한다
(``source_generation/rds_writeback.py`` 참고).
"""

from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

from rd2.extraction.hwp_text import extract_hwp_document  # noqa: E402
from rd2.extractors.pdf import extract_pdf  # noqa: E402
from rd2.generators.output_naming import generation_output_filename  # noqa: E402
from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
    SubclauseKey,
    clause_of_subclause,
    expected_classification,
)
from rd2.source_generation.contracts import (  # noqa: E402
    GenerationMode,
    GenerationTarget,
    SensitiveConsistencyAssessment,
    SensitivePipelineStatus,
    SourceDocumentSnapshot,
    TargetClassification,
)
from rd2.source_generation.document_form import check_document_form  # noqa: E402
from rd2.source_generation.evidence import (  # noqa: E402
    evidence_from_inserted_text,
)
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
from rd2.source_generation.minimal_prompt import (  # noqa: E402
    MINIMAL_PROMPT_VERSION,
)
from rd2.source_generation.prompts import build_prompt_bundle  # noqa: E402
from rd2.source_generation.rds_writeback import (  # noqa: E402
    SourceRow,
    build_generated_document,
    should_commit,
)
from rd2.schema.models import Document  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402

BLOCKS_PER_PAGE = 12
load_dotenv(ROOT / ".env")

# cp949 콘솔(윈도우 기본)에서 em-dash가 섞인 --help/진행 로그가 UnicodeEncodeError로
# 죽는다. collect_prism.py와 같은 처리.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _snapshot_from_pdf(
    path: Path,
    *,
    source: str,
    doc_id: str | None = None,
) -> tuple[SourceDocumentSnapshot, str] | None:
    """PDF 원문을 hwpx와 **같은 block 구조**로 옮긴다.

    seoul_opengov 외의 출처는 대부분 PDF다(alio 4,303 / PRISM 1,964 /
    orginl_info 105 …). 파이프라인은 ``SourceDocumentSnapshot``만 보므로 원문
    형식과 무관한데, 이 스크립트가 hwpx만 읽어 그 출처들이 통째로 입력에서
    빠져 있었다.

    추출기는 이미 있다(``rd2.extractors.pdf.extract_pdf``) — 여기서는 그
    페이지 텍스트를 hwpx 경로와 같은 규칙으로 줄 단위 block에 담기만 한다.
    두 경로가 같은 모양의 block을 내야 마스킹 검출도, 판별기 프롬프트도
    출처에 따라 다르게 동작하지 않는다.
    """

    try:
        extracted = extract_pdf(path)
    except Exception:  # noqa: BLE001 - 한 문서 실패가 배치를 끊지 않는다
        return None
    if extracted.needs_ocr:
        # 텍스트 레이어가 없는 스캔본은 본문이 비어 있다. 넘기면 판별기가
        # 빈 문서를 보고 지어내기 시작한다.
        return None

    texts = [
        line.strip()
        for page in extracted.pages
        for line in page.text.splitlines()
        if line.strip()
    ]
    if not texts:
        return None

    return _snapshot_from_texts(
        texts,
        source=source,
        doc_id=doc_id or path.stem,
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _snapshot_from_hwpx(
    path: Path,
    data_root: Path,
    *,
    source: str = "seoul_opengov",
    doc_id: str | None = None,
) -> tuple[SourceDocumentSnapshot, str] | None:
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

    return _snapshot_from_texts(
        texts,
        source=source,
        doc_id=doc_id or str(extracted.get("doc_id") or path.stem),
        source_sha256=extracted["source_sha256"],
    )


def _snapshot_from_texts(
    texts: list[str],
    *,
    source: str,
    doc_id: str,
    source_sha256: str,
) -> tuple[SourceDocumentSnapshot, str]:
    """hwpx·PDF가 공유하는 block 배치 규칙."""

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
            "manifest_key": "seoul-official-batch",
            "source_sha256": source_sha256,
            "pages": pages,
        }
    )
    # 첫 몇 줄에 제목이 들어 있는 경우가 많다. 없으면 문서 ID로 대체한다.
    title = next((t for t in texts if len(t) > 6), doc_id)
    return snapshot, title


@dataclass(frozen=True)
class SourceItem:
    """생성 파이프라인에 넣을 원문 하나. 입력이 파일이든 RDS 행이든 이 모양이다."""

    snapshot: SourceDocumentSnapshot
    title: str
    #: 리포트·파일명에 쓰는 이름. 파일 입력은 파일명, RDS 입력은 "{source}-{id}".
    display_name: str
    row: SourceRow | None = None


def _connect_rds(database: str | None):
    """읽기 전용 연결.

    ``DocumentStore``를 쓰지 않는다 — 그 생성자는 접속할 때마다 ALTER TABLE
    마이그레이션을 돌리는데(``storage/db.py``), 원문을 고르기만 하는 경로에서
    운영 테이블 스키마를 건드릴 이유가 없다. 쓰기가 필요할 때만
    ``DocumentStore``를 연다.
    """

    import pymysql

    return pymysql.connect(
        host=os.environ["MARIADB_HOST"],
        port=int(os.environ.get("MARIADB_PORT", 3306)),
        user=os.environ["MARIADB_USER"],
        password=os.environ["MARIADB_PASSWORD"],
        database=database or os.environ["MARIADB_DATABASE"],
        charset="utf8mb4",
    )


_RDS_COLUMNS = (
    "id",
    "source",
    "doc_type",
    "title",
    "ordering_agency",
    "department",
    "unit_task",
    "production_date",
    "subject_category",
    "body_text",
    "body_file_path",
)


def _fetch_rds_rows(
    connection,
    *,
    source: str | None,
    doc_type: str | None,
    require_file: bool,
    limit: int,
) -> list[SourceRow]:
    """생성 입력 후보인 공개(O) 행을 고른다.

    C/S 라벨 문서는 본문이 없으므로 애초에 후보가 아니다 — 입력은 항상 O다.
    """

    where = ["cso_classification = 'O'"]
    params: list = []
    if source is not None:
        where.append("source = %s")
        params.append(source)
    if doc_type is not None:
        where.append("doc_type = %s")
        params.append(doc_type)
    if require_file:
        where.append("body_file_path IS NOT NULL AND body_file_path <> ''")
    else:
        where.append(
            "(body_file_path IS NOT NULL AND body_file_path <> '' "
            "OR body_text IS NOT NULL)"
        )
    params.append(limit)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(_RDS_COLUMNS)} FROM documents "
            f"WHERE {' AND '.join(where)} ORDER BY id LIMIT %s",
            params,
        )
        rows = cursor.fetchall()
    return [SourceRow(**dict(zip(_RDS_COLUMNS, row))) for row in rows]


def _snapshot_from_body_text(
    body: str,
    *,
    source: str,
    doc_id: str,
) -> tuple[SourceDocumentSnapshot, str] | None:
    """파일이 없는 행의 ``body_text``로 스냅샷을 만든다(폴백 경로).

    파일 경로보다 열등하다 — 추출 과정에서 마스킹 표기가 사라진 본문이면
    ``mask_restoration`` route가 서지 않는다. 그래서 기본값이 아니다.
    """

    # 개행이 아예 없는 본문을 문단으로 자르는 규칙은 RDS 입력을 먼저 다룬
    # 배치에 이미 있다. 두 벌로 갈라두면 block 경계가 하네스마다 달라진다.
    from scripts.run_source_generation_batch import _split_run_on_text

    texts = [chunk.strip() for chunk in body.split("\n\n") if chunk.strip()]
    if len(texts) <= 1:
        texts = [line.strip() for line in body.splitlines() if line.strip()]
    if len(texts) <= 1:
        texts = _split_run_on_text(body)
    if not texts:
        return None
    return _snapshot_from_texts(
        texts,
        source=source,
        doc_id=doc_id,
        source_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def _iter_rds_items(
    rows: list[SourceRow],
    *,
    files_root: Path,
    allow_body_text: bool,
) -> "Iterator[SourceItem]":
    """RDS 행을 원문 스냅샷으로 바꾼다. 열 수 없는 행은 조용히 건너뛴다."""

    for row in rows:
        path: Path | None = None
        if row.body_file_path:
            candidate = files_root / row.body_file_path
            if candidate.exists() and candidate.suffix.lower() in {".hwpx", ".pdf"}:
                path = candidate
        built: tuple[SourceDocumentSnapshot, str] | None = None
        if path is not None:
            if path.suffix.lower() == ".pdf":
                built = _snapshot_from_pdf(
                    path, source=row.source, doc_id=str(row.id)
                )
            else:
                built = _snapshot_from_hwpx(
                    path, ROOT / "data", source=row.source, doc_id=str(row.id)
                )
        if built is None and allow_body_text and row.body_text:
            built = _snapshot_from_body_text(
                row.body_text, source=row.source, doc_id=str(row.id)
            )
        if built is None:
            continue
        snapshot, extracted_title = built
        yield SourceItem(
            snapshot=snapshot,
            title=row.title or extracted_title,
            display_name=row.document_id,
            row=row,
        )


def _iter_file_items(
    files: list[Path],
    *,
    source_name: str,
) -> "Iterator[SourceItem]":
    for path in files:
        if path.suffix.lower() == ".pdf":
            built = _snapshot_from_pdf(path, source=source_name)
        else:
            built = _snapshot_from_hwpx(path, ROOT / "data", source=source_name)
        if built is None:
            continue
        snapshot, title = built
        yield SourceItem(
            snapshot=snapshot,
            title=title,
            display_name=path.name,
        )


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
    parser.add_argument(
        "--source-name",
        default="seoul_opengov",
        help="snapshot.source에 기록할 출처 이름 (예: alio, PRISM, orginl_info)",
    )
    parser.add_argument(
        "--subclause",
        default=None,
        help=(
            "세부유형을 고정한다(예: technology_development). 생략하면 판별기가 "
            "고른다. **기본값(생략)을 권한다** — PRISM 실측에서 고정이 라벨을 "
            "모으는 대신 수율을 깎았다: technology_development로 묶으니 귀속이 "
            "75%%->89%%로 올랐지만 S승인이 8->6으로 떨어졌고, 떨어진 3건은 모두 "
            "`정책연구 활용결과 보고서`였다. 연구를 **어떻게 썼는지** 적은 "
            "문서는 `연구개발의 심사·평가 절차`와 겹치지 않아 판별기가 "
            "decision_review/audit_inspection으로 보낸 편이 맞았다. 출처 이름만 "
            "보고 업무를 단정하지 말 것."
        ),
    )
    parser.add_argument(
        "--synthetic-mask",
        action="store_true",
        help="문서를 다시 쓰지 않고 자리를 만들어 값만 채운다(2단계)",
    )
    parser.add_argument(
        "--minimal-prompt",
        action="store_true",
        help="생성 단계에 현행 대신 최소판(레드팀) 프롬프트를 쓴다",
    )
    parser.add_argument(
        "--from-rds",
        action="store_true",
        help="로컬 디렉터리 대신 RDS의 공개(O) 행에서 원문을 고른다",
    )
    parser.add_argument(
        "--rds-source",
        default=None,
        help="--from-rds일 때 documents.source 필터 (예: alio)",
    )
    parser.add_argument(
        "--rds-doc-type",
        default=None,
        help="--from-rds일 때 documents.doc_type 필터",
    )
    parser.add_argument(
        "--rds-scan-limit",
        type=int,
        default=0,
        help="RDS에서 훑어볼 행 수. 0이면 --count의 20배 "
             "(파일 없음·추출 실패·relevance 미달로 상당수가 빠진다)",
    )
    parser.add_argument(
        "--files-root",
        type=Path,
        default=ROOT / "data",
        help="body_file_path의 기준 경로(상대경로로 저장돼 있다)",
    )
    parser.add_argument(
        "--allow-body-text",
        action="store_true",
        help="파일을 열 수 없는 행은 body_text로 대신 스냅샷을 만든다"
             "(마스킹 자리가 사라진 본문일 수 있어 기본값 아님)",
    )
    parser.add_argument(
        "--commit-to-rds",
        action="store_true",
        help="승인된(accepted_s) 생성물을 documents 테이블에 S 행으로 넣는다",
    )
    parser.add_argument(
        "--rds-database",
        default=None,
        help="접속할 DB 이름. 생략하면 .env의 MARIADB_DATABASE "
             "(검증 실행은 rd2_test를 쓸 것)",
    )
    parser.add_argument(
        "--skip-render",
        action="store_true",
        help="PDF 렌더링을 하지 않고 생성·검증까지만 하고 끝낸다. 템플릿 교체 "
             "작업이 끝나기 전까지 본문과 메타데이터만 모을 때 쓴다 — "
             "render_payloads.jsonl은 그대로 남으므로 나중에 그대로 렌더링할 수 "
             "있다",
    )
    parser.add_argument(
        "--require-render-ok",
        action="store_true",
        help="PDF 렌더링에 성공한 문서만 RDS에 넣는다. 템플릿 교체 작업이 "
             "끝나면 기본으로 올릴 것 — 지금 켜면 현재 템플릿이 담지 못하는 "
             "긴 본문·표 문서가 통째로 빠진다",
    )
    parser.add_argument(
        "--include-weak-mask-restoration",
        action="store_true",
        help="검증기가 O를 낸 mask_restoration 결과도 RDS에 넣는다"
             "(기본은 제외 — rds_writeback.should_commit 참고)",
    )
    parser.add_argument("--classifier-model", default="gpt-4o")
    parser.add_argument("--generator-model", default="gpt-4o")
    parser.add_argument("--validator-model", default="gpt-4o-mini")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    if args.from_rds:
        read_connection = _connect_rds(args.rds_database)
        try:
            rows = _fetch_rds_rows(
                read_connection,
                source=args.rds_source,
                doc_type=args.rds_doc_type,
                require_file=not args.allow_body_text,
                limit=args.rds_scan_limit or args.count * 20,
            )
        finally:
            read_connection.close()
        if not rows:
            print("조건에 맞는 O 원문이 RDS에 없다")
            return 1
        print(f"RDS 후보 {len(rows)}행 -> 최대 {args.count}건 처리")
        items = _iter_rds_items(
            rows,
            files_root=args.files_root,
            allow_body_text=args.allow_body_text,
        )
    else:
        files = sorted(
            [*args.source_dir.rglob("*.hwpx"), *args.source_dir.rglob("*.pdf")]
        )
        if not files:
            print(f"hwpx/pdf 파일이 없다: {args.source_dir}")
            return 1
        items = _iter_file_items(files, source_name=args.source_name)

    store = (
        DocumentStore(database=args.rds_database) if args.commit_to_rds else None
    )
    rds_inserted = 0
    rds_skipped = 0
    rds_failed = 0
    #: (배치 레코드, 조립된 Document). 렌더링 결과를 보고 나서 넣는다.
    pending_commits: list[tuple[dict, Document]] = []

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
        minimal_generator_prompt=args.minimal_prompt,
        synthetic_mask_generation=args.synthetic_mask,
    )
    selection_config = SelectionConfig()
    prompt_bundle = build_prompt_bundle(selection_config)
    forced_target: GenerationTarget | None = None
    if args.subclause:
        subclause = SubclauseKey(args.subclause)
        clause = clause_of_subclause(subclause)
        forced_target = GenerationTarget(
            classification=TargetClassification(
                expected_classification(clause).value
            ),
            clause_no=clause,
            subclause_key=subclause,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        )
    targets = _targets()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    records_path = args.out_dir / "batch_records.jsonl"
    payload_path = args.out_dir / "render_payloads.jsonl"

    done = 0
    report_records: list[dict] = []
    with records_path.open("w", encoding="utf-8") as records, payload_path.open(
        "w", encoding="utf-8"
    ) as payloads:
        for item in items:
            if done >= args.count:
                break
            snapshot, title = item.snapshot, item.title
            prepared = prepare_document_selection(snapshot, selection_config)
            if prepared.selection is None:
                continue

            done += 1
            source_text = "\n\n".join(
                block.text for page in snapshot.pages for block in page.blocks
            )
            # 목표를 밖에서 강제하지 않는다. 판별기가 원문을 읽고 고른
            # primary_subclause가 그대로 생성 목표가 되고, 부분공개 원문이면
            # 플래너가 푸터의 호로 다시 잠근다.
            #
            # 강제했을 때 값을 치른 자리가 둘이다 — 고시·통계처럼 개인이 없는
            # 문서에 제6호를 요구해 계획 단계에서 끝났고(실측 24건), 푸터가
            # 제6호인 문서에 제5호를 배정해 마스킹 route가 풀렸다.
            target = forced_target
            target_source = (
                f"forced:{args.subclause}" if forced_target else "classifier-primary"
            )
            print(
                f"[{done}/{args.count}] {snapshot.source_document_id} "
                f"({target_source})"
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
                "source_file": item.display_name,
                "source_row_id": item.row.id if item.row is not None else None,
                "source_title": title,
                "source_block_count": sum(len(p.blocks) for p in snapshot.pages),
                "source_text": source_text,
                "target_source": target_source,
                "requested_target": (
                    target.model_dump(mode="json") if target else None
                ),
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
                    source_filename=item.display_name,
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
                                    # 렌더러가 route별로 다르게 처리한다 —
                                    # mask_restoration 산출물은 이미 완성된
                                    # 원문이라 템플릿 조립을 건너뛴다. 이 값이
                                    # 빠지면 그 분기가 서지 않는다.
                                    "generation_route": (
                                        plan.generation_route.value
                                    ),
                                    # 합성 마스킹도 원문을 그대로 옮긴다.
                                    # route는 anchored 등으로 남으므로 표시를
                                    # 따로 남겨야 렌더러가 알아본다.
                                    "verbatim_render": bool(
                                        args.synthetic_mask
                                    ),
                                    "generation_target": (
                                        plan.final_target.model_dump(mode="json")
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
            # 검사기가 든 근거가 **우리가 넣은 자리**에서 왔는지.
            #
            # 원문 보존율이 높아질수록 필요한 기록이다. 생성물의 99%가 원문이면
            # 검사기가 원문 쪽 문장을 근거로 S를 줄 수 있고, 그러면 라벨은
            # 맞아도 학습데이터로는 해롭다(실측: alio 연간감사 결과보고서에서
            # 이미 공표된 징계 처분 내역이 근거로 잡혔다).
            consistency = result.consistency_assessment
            if consistency is not None and record.get("generated_body"):
                quotes = [span.quote for span in consistency.evidence_spans]
                record["evidence_quotes"] = quotes
                record["evidence_from_insertion"] = list(
                    evidence_from_inserted_text(
                        source_text,
                        record["generated_body"],
                        quotes,
                    )
                )
            # 조립만 하고 넣지는 않는다. 실제 upsert는 렌더링 이후다 —
            # PDF 근거 검사(verify_rendered_sensitive_evidence)가 마지막
            # 품질 게이트이고, 실측에서 검증기를 통과한 35건 중 23건이 그
            # 게이트에서 떨어졌다(2026-08-03 allsources_synthmask). 여기서
            # 넣으면 PDF로 만들지 못한 문서가 학습 코퍼스에 남는다.
            if store is not None and result.generation_plan is not None:
                plan = result.generation_plan
                assessment = result.source_assessment
                consistency = result.consistency_assessment
                sensitive_assessment = (
                    consistency
                    if isinstance(consistency, SensitiveConsistencyAssessment)
                    else None
                )
                commit = (
                    result.generation_artifact is not None
                    and should_commit(
                        status=sensitive_run.status,
                        plan=plan,
                        assessment=sensitive_assessment,
                        include_weak_mask_restoration=(
                            args.include_weak_mask_restoration
                        ),
                    )
                )
                record["rds_committed"] = False
                if commit:
                    try:
                        generated_row = build_generated_document(
                            document=(
                                result.generation_artifact.generated_document
                            ),
                            plan=plan,
                            source_document_id=snapshot.source_document_id,
                            source_row=item.row,
                            fallback_source=args.source_name,
                            document_form=(
                                assessment.source_classification.document_form
                                if assessment is not None
                                else None
                            ),
                            assessment=sensitive_assessment,
                        )
                        pending_commits.append((record, generated_row))
                    except Exception as exc:  # noqa: BLE001
                        # 한 건의 조립 실패가 배치를 끊지 않는다 — 나머지
                        # 문서는 이미 API 비용을 치렀다.
                        rds_failed += 1
                        record["rds_error"] = str(exc)

            records.write(json.dumps(record, ensure_ascii=False) + "\n")
            report_records.append(record)
            records.flush()
            payloads.flush()

    if args.skip_render:
        print("PDF 렌더링 생략 — render_payloads.jsonl로 나중에 렌더링할 수 있다")
    elif payload_path.stat().st_size:
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
    # 넣는 시점은 렌더링 **이후**다. PDF가 최종 산출물이 되면 렌더 결과가
    # 코퍼스 포함 여부를 정해야 하기 때문이다(--require-render-ok).
    #
    # 다만 지금은 기본값이 꺼져 있다. 실측(2026-08-03 allsources_synthmask)에서
    # 검증기를 통과한 35건 중 23건이 렌더 검증의 ``missing source text``로
    # 떨어졌는데, 그건 생성 실패가 아니라 **현재 템플릿이 긴 본문·표를 담지
    # 못한 결과**다. 템플릿 교체 작업이 끝나기 전까지 그걸로 코퍼스를 막으면
    # 멀쩡한 생성물 3분의 2를 곧 사라질 이유로 버린다. 템플릿이 완성되면 이
    # 플래그를 기본으로 올리고, 백필 패스가 렌더 성공분에만 body_file_path를
    # 채운다 — 실패한 행은 경로가 NULL로 남아 그대로 식별된다.
    for record, document in pending_commits:
        if args.require_render_ok and record.get("render_status") not in (None, "ok"):
            record["rds_skipped_reason"] = "render_not_ok"
            continue
        try:
            if store.upsert(document):
                rds_inserted += 1
                record["rds_committed"] = True
            else:
                rds_skipped += 1
            record["rds_source_url"] = document.source_url
        except Exception as exc:  # noqa: BLE001
            rds_failed += 1
            record["rds_error"] = str(exc)

    if report_records:
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
                "generator_prompt": (
                    MINIMAL_PROMPT_VERSION
                    if args.minimal_prompt
                    else prompt_bundle.version
                ),
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
                "input_mode": "rds" if args.from_rds else "files",
                "source_dir": (
                    None if args.from_rds else str(args.source_dir)
                ),
                "rds_filter": (
                    {
                        "source": args.rds_source,
                        "doc_type": args.rds_doc_type,
                        "allow_body_text": args.allow_body_text,
                    }
                    if args.from_rds
                    else None
                ),
                "rds_writeback": (
                    {
                        "database": args.rds_database,
                        "inserted": rds_inserted,
                        "skipped_duplicate": rds_skipped,
                        "failed": rds_failed,
                        "include_weak_mask_restoration": (
                            args.include_weak_mask_restoration
                        ),
                    }
                    if args.commit_to_rds
                    else None
                ),
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
    if store is not None:
        store.close()
        print(
            f"RDS 기록: 신규 {rds_inserted} / 중복스킵 {rds_skipped} / 실패 {rds_failed}"
        )
    print(f"\n{done}건 -> {records_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
