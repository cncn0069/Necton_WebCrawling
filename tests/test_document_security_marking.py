from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
import pytest
from PIL import Image

import rd2.generators.document_security_marking as security_marking
from rd2.generators.agency_resolver import AGENCY_LOGO_FILENAMES
from rd2.generators.document_security_marking import (
    _agency_watermark_image,
    SecurityMarkingError,
    apply_security_marking_to_manifest,
    prepend_military_secret_cover,
    resolve_agency_marking,
    resolve_security_marking,
)
from rd2.generators.confidential_security_templates import (
    CONFIDENTIAL_SECURITY_TEMPLATE_SLUGS,
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
    assert confidential.mark_asset_path.name == "대외비.png"
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
        "front_cover_top_bottom_and_monochrome_skin"
    )
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
            image_rect_count = sum(
                len(page.get_image_rects(xref))
                for xref in {image[0] for image in page.get_images(full=True)}
            )
            assert image_rect_count == 2


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


def test_c_document_keeps_existing_agency_watermark_policy(tmp_path: Path) -> None:
    pdf_path = tmp_path / "agency.pdf"
    _write_pdf(pdf_path, pages=1)
    manifest = [{"status": "ok", "pdf": str(pdf_path)}]

    apply_security_marking_to_manifest(
        manifest,
        target=_target(TargetClassification.C),
        agency_name="행정안전부",
        selection_seed=2,
    )

    agency_marking = manifest[0]["agency_marking"]
    assert agency_marking["agency_name"] == "행정안전부"
    assert agency_marking["asset"] == "logo/정부부처.png"
    assert agency_marking["placement"]["strategy"] == "center_watermark"
    assert agency_marking["tone"] == {"grayscale": 82, "max_alpha": 74}
    with fitz.open(pdf_path) as document:
        assert len(document[0].get_images(full=True)) == 2


def test_resolve_agency_marking_still_gates_on_c_classification() -> None:
    assert (
        resolve_agency_marking(
            _target(TargetClassification.S),
            agency_name="행정안전부",
        )
        is None
    )
    resolved = resolve_agency_marking(
        _target(TargetClassification.C),
        agency_name="행정안전부",
    )
    assert resolved is not None
    assert resolved.asset_path.name == "정부부처.png"


def test_agency_watermark_preserves_approved_internal_canvas_spacing() -> None:
    expected_visible_widths = {
        "국방부.png": (0.75, 0.90),
        "정부부처.png": (0.50, 0.60),
    }
    for filename, expected_range in expected_visible_widths.items():
        image_bytes, _ = _agency_watermark_image(REPO_ROOT / "logo" / filename)
        with Image.open(BytesIO(image_bytes)) as watermark:
            alpha = watermark.getchannel("A")
            visible_box = alpha.getbbox()
            assert visible_box is not None
            visible_ratio = (visible_box[2] - visible_box[0]) / watermark.width
            assert expected_range[0] <= visible_ratio <= expected_range[1]
            assert alpha.getextrema() == (0, 74)


@pytest.mark.parametrize(
    "filename",
    sorted(set(AGENCY_LOGO_FILENAMES.values())),
)
def test_every_mapped_agency_asset_builds_a_transparent_watermark(
    filename: str,
) -> None:
    image_bytes, image_ratio = _agency_watermark_image(
        REPO_ROOT / "logo" / filename
    )
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert image_ratio > 0


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
    assert marking["asset"] == "logo/synthetic_confidential.png"
    assert marking["asset_kind"] == "synthetic_security_stamp"
    assert marking["security_template"]["slug"] == "03_minimal_mark"
    assert marking["palette"] == {
        "mode": "monochrome_dark",
        "ink_hex": "#22272C",
    }
    assert marking["content_page_count"] == 1
    assert marking["final_pdf_page_count"] == 1
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
        "logo/synthetic_confidential.png",
        "logo/synthetic_top_secret.png",
        "logo/synthetic_restricted.png",
        "logo/synthetic_need_to_know.png",
    }


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
        {"status": "ok", "pdf": str(clear_pdf)},
        {"status": "ok", "pdf": str(failing_pdf)},
    ]

    original_save = security_marking._save_marked_pdf

    def fail_second(
        source_path,
        output_path,
        *,
        spec,
        agency_spec,
        security_template,
    ):
        if source_path == failing_pdf:
            raise SecurityMarkingError("두 번째 PDF staging 실패")
        return original_save(
            source_path,
            output_path,
            spec=spec,
            agency_spec=agency_spec,
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
    assert all("security_marking" not in entry for entry in manifest)
    assert not list(tmp_path.glob(".*.security-mark.pdf"))
