"""승인된 S 관계 근거가 최종 PDF에도 남았는지 확인한다."""

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
    """렌더링 manifest의 모든 PDF를 확인하고 하나라도 손실되면 실패한다."""

    if payload.get("approval_status") != "accepted_s":
        return []
    raw_assessment = payload.get("consistency_assessment")
    if not isinstance(raw_assessment, Mapping):
        raise ValueError("accepted_s payload requires consistency_assessment")
    raw_result = payload.get("result")
    if not isinstance(raw_result, Mapping):
        raise ValueError("accepted_s payload requires result")
    raw_document = raw_result.get("generated_document")
    if not isinstance(raw_document, Mapping):
        raise ValueError("accepted_s payload requires generated_document")

    assessment = SensitiveConsistencyAssessment.model_validate(raw_assessment)
    document = GeneratedDocumentIR.model_validate(raw_document)
    results: list[dict[str, Any]] = []
    for entry in rendered:
        pdf_path = Path(str(entry["pdf"]))
        checks = verify_sensitive_evidence_pages(
            assessment=assessment,
            document=document,
            page_texts=_page_texts(pdf_path),
        )
        if not all(check["passed"] for check in checks):
            raise ValueError(
                f"sensitive evidence missing from rendered PDF: {pdf_path}"
            )
        results.append(
            {
                "pdf": str(pdf_path),
                "status": "preserved",
                "assertions": checks,
            }
        )
    return results
