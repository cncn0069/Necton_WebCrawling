from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from rd2.generators.document_security_marking import (
    SecurityMarkingError,
    _general_mark_rect,
    _image_ratio,
    apply_security_marking_to_manifest,
    resolve_security_marking,
)
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    GenerationMode,
    GenerationTarget,
    TargetClassification,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIDENTIAL_ASSET = REPO_ROOT / "logo" / "대외비.png"


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
            f"Body page {page_number + 1}\n" + ("Central body text remains intact. " * 8),
            fontsize=11,
        )
    document.save(path)
    document.close()


def _add_blocking_image(path: Path, *, page_number: int, slot: int) -> None:
    document = fitz.open(path)
    page = document[page_number]
    rect = _general_mark_rect(
        page,
        slot,
        image_ratio=_image_ratio(CONFIDENTIAL_ASSET),
    )
    page.insert_image(rect, filename=str(CONFIDENTIAL_ASSET), overlay=True)
    staged = path.with_suffix(".blocked.pdf")
    document.save(staged)
    document.close()
    staged.replace(path)


def _manifest(path: Path) -> list[dict[str, object]]:
    return [{"status": "ok", "pdf": str(path)}]


def test_resolve_security_marking_gates_by_classification_agency_and_grade():
    assert (
        resolve_security_marking(_target(TargetClassification.S), agency_name="국방부")
        is None
    )

    general = resolve_security_marking(
        _target(TargetClassification.C),
        agency_name="행정안전부",
    )
    assert general is not None
    assert general.kind == "confidential"
    assert general.asset_path.name == "대외비.png"

    compatible_general = resolve_security_marking(
        {"classification": " c ", "legacy_metadata": "kept"},
        agency_name="행정안전부",
    )
    assert compatible_general is not None
    assert compatible_general.kind == "confidential"

    military = resolve_security_marking(
        _target(TargetClassification.C, grade="2급"),
        agency_name="국방부",
    )
    assert military is not None
    assert military.kind == "military_secret"
    assert military.asset_path.name == "2급_비밀.png"

    with pytest.raises(SecurityMarkingError, match="military_secret_grade"):
        resolve_security_marking(
            _target(TargetClassification.C),
            agency_name="국가정보원",
        )

    with pytest.raises(SecurityMarkingError, match="국방부·국가정보원"):
        resolve_security_marking(
            _target(TargetClassification.C, grade="1급"),
            agency_name="행정안전부",
        )

    with pytest.raises(SecurityMarkingError, match="1급, 2급, 3급"):
        resolve_security_marking(
            {"classification": "C", "military_secret_grade": "4급"},
            agency_name="국방부",
        )


def test_general_mark_uses_one_collision_free_slot_on_every_page(tmp_path: Path):
    pdf_path = tmp_path / "general.pdf"
    _write_pdf(pdf_path)
    _add_blocking_image(pdf_path, page_number=1, slot=0)
    manifest = _manifest(pdf_path)

    apply_security_marking_to_manifest(
        manifest,
        target=_target(TargetClassification.C),
        agency_name="행정안전부",
        content_sha256="0" * 64,
    )

    marking = manifest[0]["security_marking"]
    assert marking["kind"] == "confidential"
    assert marking["placement"] == {"strategy": "perimeter_slot", "slot": 1}
    with fitz.open(pdf_path) as document:
        assert document.page_count == 2
        assert len(document[0].get_images(full=True)) == 1
        assert len(document[1].get_images(full=True)) == 2
        assert "Central body text" in "".join(page.get_text() for page in document)


def test_military_mark_is_added_to_top_and_bottom_of_every_page(tmp_path: Path):
    pdf_path = tmp_path / "military.pdf"
    _write_pdf(pdf_path)
    manifest = _manifest(pdf_path)

    apply_security_marking_to_manifest(
        manifest,
        target=_target(TargetClassification.C, grade="3급"),
        agency_name="국방부",
        content_sha256="a" * 64,
    )

    marking = manifest[0]["security_marking"]
    assert marking["kind"] == "military_secret"
    assert marking["military_secret_grade"] == "3급"
    assert marking["placement"]["strategy"] == "top_bottom_center"
    with fitz.open(pdf_path) as document:
        assert all(len(page.get_images(full=True)) == 2 for page in document)


def test_batch_is_not_modified_when_any_pdf_has_no_clear_slot(tmp_path: Path):
    clear_pdf = tmp_path / "clear.pdf"
    blocked_pdf = tmp_path / "blocked.pdf"
    _write_pdf(clear_pdf, pages=1)
    _write_pdf(blocked_pdf, pages=1)
    for slot in range(10):
        _add_blocking_image(blocked_pdf, page_number=0, slot=slot)

    clear_before = clear_pdf.read_bytes()
    blocked_before = blocked_pdf.read_bytes()
    manifest = [
        {"status": "ok", "pdf": str(clear_pdf)},
        {"status": "ok", "pdf": str(blocked_pdf)},
    ]

    with pytest.raises(SecurityMarkingError, match="공통으로 비어 있는"):
        apply_security_marking_to_manifest(
            manifest,
            target=_target(TargetClassification.C),
            agency_name="행정안전부",
            content_sha256="0" * 64,
        )

    assert clear_pdf.read_bytes() == clear_before
    assert blocked_pdf.read_bytes() == blocked_before
    assert all("security_marking" not in entry for entry in manifest)
    assert not list(tmp_path.glob(".*.security-mark.pdf"))
