from __future__ import annotations

from pathlib import Path

import fitz
import pytest

import rd2.generators.document_security_marking as security_marking
from rd2.generators.document_security_marking import (
    SecurityMarkingError,
    apply_security_marking_to_manifest,
    prepend_military_secret_cover,
    resolve_security_marking,
)
from rd2.generators.confidential_security_templates import (
    CONFIDENTIAL_SECURITY_TEMPLATES,
    CONFIDENTIAL_SECURITY_TEMPLATE_SLUGS,
    SECURITY_MARK_HEIGHT_PT,
    draw_confidential_security_template,
)
from rd2.source_generation.classification_taxonomy import ClauseNumber, SubclauseKey
from rd2.source_generation.contracts import (
    GenerationMode,
    GenerationTarget,
    TargetClassification,
)

def _target(
    classification: TargetClassification,
    *,
    grade: str | None = None,
) -> GenerationTarget:
    if classification is TargetClassification.C:
        return GenerationTarget(
            classification=classification,
            clause_no=ClauseNumber.CLAUSE_2,
            subclause_key=SubclauseKey.SECURITY_DEFENSE,
            generation_mode=GenerationMode.COUNTERFACTUAL,
            military_secret_grade=grade,
        )
    return GenerationTarget(
        classification=classification,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def _write_pdf(path: Path, *, pages: int = 2) -> None:
    document = fitz.open()
    for page_number in range(pages):
        page = document.new_page(width=595, height=842)
        page.insert_textbox(
            fitz.Rect(72, 180, 523, 680),
            f"Body page {page_number + 1}\n"
            + ("Central body text remains intact. " * 8),
            fontsize=11,
        )
    document.save(path)
    document.close()


def _manifest(path: Path) -> list[dict[str, object]]:
    return [{"status": "ok", "pdf": str(path), "actual_pages": 2}]


def test_resolve_security_marking_uses_c_as_confidential_and_grade_as_military() -> None:
    assert resolve_security_marking(_target(TargetClassification.S)) is None
    confidential = resolve_security_marking(_target(TargetClassification.C))
    assert confidential is not None
    assert confidential.kind == "confidential"
    assert confidential.mark_asset_path is None
    assert confidential.cover_asset_path is None

    military = resolve_security_marking(
        _target(TargetClassification.C, grade="2급")
    )
    assert military is not None
    assert military.kind == "military_secret"
    assert military.mark_asset_path.name == "2급_비밀.png"
    assert military.cover_asset_path.name == "2급_비밀_표지.png"

    compatible_mapping = resolve_security_marking(
        {
            "classification": " c ",
            "military_secret_grade": "1급",
            "legacy_metadata": "kept",
        }
    )
    assert compatible_mapping is not None
    assert compatible_mapping.military_secret_grade == "1급"

    with pytest.raises(SecurityMarkingError, match="1급, 2급, 3급"):
        resolve_security_marking(
            {"classification": "C", "military_secret_grade": "4급"}
        )


@pytest.mark.parametrize("grade", ["1급", "2급", "3급"])
def test_military_secret_adds_unlimited_front_cover_and_body_marks(
    tmp_path: Path,
    grade: str,
) -> None:
    pdf_path = tmp_path / f"military-{grade}.pdf"
    _write_pdf(pdf_path)
    manifest = _manifest(pdf_path)

    apply_security_marking_to_manifest(
        manifest,
        target=_target(TargetClassification.C, grade=grade),
    )

    marking = manifest[0]["security_marking"]
    assert marking["kind"] == "military_secret"
    assert marking["military_secret_grade"] == grade
    assert marking["placement"]["strategy"] == (
        "front_cover_top_bottom_and_neutral_frame"
    )
    assert marking["security_template"]["slug"] == "military_neutral_frame"
    assert marking["placement"]["cover"]["counted_in_page_limit"] is False
    assert marking["content_page_count"] == 2
    assert marking["final_pdf_page_count"] == 3
    # 본문 상한을 나타내는 기존 manifest 값은 표지 추가로 바꾸지 않는다.
    assert manifest[0]["actual_pages"] == 2
    assert "agency_marking" not in manifest[0]

    with fitz.open(pdf_path) as document:
        assert document.page_count == 3
        assert document[0].get_text().strip() == ""
        assert len(document[0].get_images(full=True)) == 1
        for page_number in range(1, document.page_count):
            page = document[page_number]
            assert "Central body text" in page.get_text()
            page_text = page.get_text().upper()
            assert "RESTRICTED" not in page_text
            assert "OFFICIAL-SENSITIVE" not in page_text
            assert "CONFIDENTIAL REPORT" not in page_text
            assert "NEED TO KNOW" not in page_text
            image_rect_count = sum(
                len(page.get_image_rects(xref))
                for xref in {image[0] for image in page.get_images(full=True)}
            )
            assert image_rect_count == 2
            image_rects = [
                rect
                for image in page.get_images(full=True)
                for rect in page.get_image_rects(image[0])
            ]
            assert all(rect.height == pytest.approx(25.0) for rect in image_rects)


def test_grade_without_agency_name_still_gets_cover(tmp_path: Path) -> None:
    pdf_path = tmp_path / "no-agency.pdf"
    _write_pdf(pdf_path, pages=1)
    manifest = [{"status": "ok", "pdf": str(pdf_path)}]

    apply_security_marking_to_manifest(
        manifest,
        target={"classification": "C", "military_secret_grade": "3급"},
    )

    assert manifest[0]["security_marking"]["military_secret_grade"] == "3급"
    with fitz.open(pdf_path) as document:
        assert document.page_count == 2


def test_c_document_without_grade_gets_monochrome_confidential_skin(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "ungraded.pdf"
    _write_pdf(pdf_path, pages=1)
    manifest = [
        {
            "status": "ok",
            "pdf": str(pdf_path),
            "agency_marking": {"legacy": True},
        }
    ]

    apply_security_marking_to_manifest(
        manifest,
        target=_target(TargetClassification.C),
        selection_seed=2,
    )

    marking = manifest[0]["security_marking"]
    assert marking["kind"] == "confidential"
    assert marking["asset"] == "logo/보안등급_3급_비밀.png"
    assert marking["asset_kind"] == "security_classification_stamp"
    assert marking["security_template"]["slug"] == "03_minimal_mark"
    assert marking["palette"] == {
        "mode": "monochrome_dark",
        "ink_hex": "#22272C",
    }
    assert marking["content_page_count"] == 1
    assert marking["final_pdf_page_count"] == 1
    mark_rect = marking["placement"]["body"]["page_placements"][0]["mark_rect"]
    assert mark_rect[3] - mark_rect[1] == pytest.approx(SECURITY_MARK_HEIGHT_PT)
    assert "agency_marking" not in manifest[0]
    with fitz.open(pdf_path) as document:
        assert document.page_count == 1
        assert "Central body text" in document[0].get_text()
        assert len(document[0].get_images(full=True)) == 1


def test_all_ten_confidential_templates_render_once_and_cycle_by_seed(
    tmp_path: Path,
) -> None:
    selected: list[str] = []
    assets: set[str] = set()
    for seed in range(10):
        pdf_path = tmp_path / f"confidential-{seed}.pdf"
        _write_pdf(pdf_path, pages=1)
        manifest = [{"status": "ok", "pdf": str(pdf_path)}]

        apply_security_marking_to_manifest(
            manifest,
            target=_target(TargetClassification.C),
            selection_seed=seed,
        )

        selected.append(
            manifest[0]["security_marking"]["security_template"]["slug"]
        )
        assets.add(manifest[0]["security_marking"]["asset"])
        with fitz.open(pdf_path) as document:
            assert document.page_count == 1
            assert "Central body text" in document[0].get_text()

    assert tuple(selected) == CONFIDENTIAL_SECURITY_TEMPLATE_SLUGS
    assert assets == {
        "logo/대외비.png",
        "logo/보안등급_3급_비밀.png",
    }


def test_confidential_templates_do_not_add_textual_header_or_footer_labels() -> None:
    """분류 영문 문구는 보안 스킨이 아닌 실제 마크 자산으로만 표시한다."""

    for template in CONFIDENTIAL_SECURITY_TEMPLATES:
        document = fitz.open()
        try:
            page = document.new_page(width=595, height=842)
            placement = draw_confidential_security_template(
                page,
                template=template,
                mark_bytes=None,
                mark_ratio=None,
            )

            assert page.get_text() == ""
            assert placement["layout"] == template.layout
            assert "english_label" not in template.to_dict()
        finally:
            document.close()


def test_prepend_cover_keeps_existing_body_page_bytes_semantically(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "existing.pdf"
    _write_pdf(pdf_path, pages=2)
    with fitz.open(pdf_path) as before:
        body_text = [page.get_text() for page in before]

    prepend_military_secret_cover(pdf_path, "1급")

    with fitz.open(pdf_path) as after:
        assert after.page_count == 3
        assert after[0].get_text().strip() == ""
        assert [after[index].get_text() for index in range(1, 3)] == body_text


def test_batch_is_not_modified_when_any_pdf_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_pdf = tmp_path / "clear.pdf"
    failing_pdf = tmp_path / "failing.pdf"
    _write_pdf(clear_pdf, pages=1)
    _write_pdf(failing_pdf, pages=1)

    clear_before = clear_pdf.read_bytes()
    failing_before = failing_pdf.read_bytes()
    manifest = [
        {
            "status": "ok",
            "pdf": str(clear_pdf),
            "agency_marking": {"legacy": "clear"},
            "security_marking": {"legacy": "clear"},
        },
        {
            "status": "ok",
            "pdf": str(failing_pdf),
            "agency_marking": {"legacy": "failing"},
            "security_marking": {"legacy": "failing"},
        },
    ]

    original_save = security_marking._save_marked_pdf

    def fail_second(source_path, output_path, *, spec, security_template):
        if source_path == failing_pdf:
            raise SecurityMarkingError("두 번째 PDF staging 실패")
        return original_save(
            source_path,
            output_path,
            spec=spec,
            security_template=security_template,
        )

    monkeypatch.setattr(security_marking, "_save_marked_pdf", fail_second)

    with pytest.raises(SecurityMarkingError, match="staging 실패"):
        apply_security_marking_to_manifest(
            manifest,
            target=_target(TargetClassification.C, grade="1급"),
        )

    assert clear_pdf.read_bytes() == clear_before
    assert failing_pdf.read_bytes() == failing_before
    assert manifest[0]["agency_marking"] == {"legacy": "clear"}
    assert manifest[0]["security_marking"] == {"legacy": "clear"}
    assert manifest[1]["agency_marking"] == {"legacy": "failing"}
    assert manifest[1]["security_marking"] == {"legacy": "failing"}
    assert not list(tmp_path.glob(".*.security-mark.pdf"))
