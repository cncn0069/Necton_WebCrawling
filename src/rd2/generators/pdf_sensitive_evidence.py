"""S 판정 근거의 PDF 보존 상태를 비차단 진단으로 기록한다."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import fitz

from rd2.source_generation.contracts import (
    GeneratedDocumentIR,
    SensitiveConsistencyAssessment,
    SensitiveVerdict,
)

_WHITESPACE = re.compile(r"\s+")


def _normalized(text: str) -> str:
    return _WHITESPACE.sub("", text)


def _page_texts(pdf_path: Path) -> list[str]:
    with fitz.open(pdf_path) as document:
        return [page.get_text("text") for page in document]


def verify_sensitive_evidence_pages(
    *,
    assessment: SensitiveConsistencyAssessment,
    document: GeneratedDocumentIR,
    page_texts: Sequence[str],
) -> list[dict[str, Any]]:
    """각 assertion의 주체·값·연결이 같은 PDF 페이지에 보존됐는지 확인한다."""

    if assessment.sensitivity_verdict != SensitiveVerdict.ACCEPTED_S:
        raise ValueError("PDF evidence verification requires accepted_s")
    if not page_texts:
        raise ValueError("rendered PDF contains no pages")

    block_kind = {block.block_id: block.kind for block in document.blocks}
    normalized_pages = [_normalized(text) for text in page_texts]
    checks: list[dict[str, Any]] = []
    for assertion in assessment.assertions:
        subject = _normalized(assertion.subject_span.quote)
        value = _normalized(assertion.value_span.quote)
        link = _normalized(assertion.link_span.quote)
        matching_pages = [
            index + 1
            for index, page in enumerate(normalized_pages)
            if subject in page and value in page
        ]
        kind = block_kind.get(assertion.link_span.block_id)
        link_required = kind in {"paragraph", "bullet_list", "attachment_reference"}
        link_pages = [
            index + 1
            for index, page in enumerate(normalized_pages)
            if link in page
        ]
        passed = bool(matching_pages) and (not link_required or bool(link_pages))
        checks.append(
            {
                "block_id": assertion.link_span.block_id,
                "block_kind": kind,
                "subject": assertion.subject_span.quote,
                "value": assertion.value_span.quote,
                "matching_pages": matching_pages,
                "link_pages": link_pages,
                "passed": passed,
            }
        )
    return checks


def verify_rendered_sensitive_evidence(
    payload: Mapping[str, Any],
    rendered: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """렌더링을 차단하지 않고 가능한 범위에서 근거 보존 상태만 기록한다."""

    if payload.get("approval_status") != "accepted_s":
        return []

    def not_checked(reason: str) -> list[dict[str, Any]]:
        return [
            {
                "pdf": str(entry["pdf"]),
                "status": "not_checked",
                "reason": reason,
            }
            for entry in rendered
        ]

    raw_assessment = payload.get("consistency_assessment")
    if not isinstance(raw_assessment, Mapping):
        return not_checked("consistency_assessment_missing")
    raw_result = payload.get("result")
    if not isinstance(raw_result, Mapping):
        return not_checked("result_missing")
    raw_document = raw_result.get("generated_document")
    if not isinstance(raw_document, Mapping):
        return not_checked("generated_document_missing")

    try:
        assessment = SensitiveConsistencyAssessment.model_validate(raw_assessment)
        document = GeneratedDocumentIR.model_validate(raw_document)
    except ValueError as exc:
        return not_checked(f"diagnostic_payload_invalid: {exc}")

    if not assessment.assertions:
        return [
            {
                "pdf": str(entry["pdf"]),
                "status": "not_checked",
                "reason": "no_structured_evidence",
                "rationale": assessment.rationale,
            }
            for entry in rendered
        ]

    results: list[dict[str, Any]] = []
    for entry in rendered:
        pdf_path = Path(str(entry["pdf"]))
        try:
            checks = verify_sensitive_evidence_pages(
                assessment=assessment,
                document=document,
                page_texts=_page_texts(pdf_path),
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics must never block render
            results.append(
                {
                    "pdf": str(pdf_path),
                    "status": "not_checked",
                    "reason": f"diagnostic_failed: {exc}",
                }
            )
            continue
        preserved = all(check["passed"] for check in checks)
        results.append(
            {
                "pdf": str(pdf_path),
                "status": "preserved" if preserved else "not_preserved",
                "assertions": checks,
            }
        )
    return results
