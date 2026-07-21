import pytest

from rd2.generators import pdf_render
from rd2.generators.agency_categories import (
    CATEGORY_CENTRAL_MINISTRY,
    CATEGORY_EDUCATION_OFFICE,
    CATEGORY_METRO_LOCAL_GOVERNMENT,
    CATEGORY_PUBLIC_CORPORATION,
    CATEGORY_RESEARCH_INSTITUTE,
)
from rd2.generators.doc_templates import TEMPLATE_VARIANTS
from rd2.generators.pdf_render import (
    LAYOUT_SPECS,
    render_document_pdf,
)
from rd2.generators.security_mark import (
    generate_agency_letterhead_mark,
    generate_classification_stamp,
    generate_page_watermark,
)
from rd2.storage.naming import DOC_TYPE_AUDIT_RESULT, DOC_TYPE_OFFICIAL_DOCUMENT
import fitz


def _pdf_text(path) -> str:
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def _sample_row(**overrides) -> dict:
    defaults = {
        "row_id": "5-prism-0",
        "title": "테스트 문서 제목",
        "ordering_agency": "경상북도",
        "department": "감사담당관실",
        "production_date": "2025-03-01",
        "cso_classification": "S",
        "body_text": "첫 번째 문단입니다.\n두 번째 문단입니다.",
        "status": "ok",
    }
    defaults.update(overrides)
    return defaults


class TestRenderDocumentPdf:
    def test_produces_valid_pdf_file(self, tmp_path):
        output = tmp_path / "out.pdf"
        result = render_document_pdf(_sample_row(), CATEGORY_METRO_LOCAL_GOVERNMENT, output)
        assert result == output
        assert output.exists()
        assert output.read_bytes()[:4] == b"%PDF"

    def test_all_five_categories_render_without_error(self, tmp_path):
        for category in [
            CATEGORY_CENTRAL_MINISTRY,
            CATEGORY_METRO_LOCAL_GOVERNMENT,
            CATEGORY_RESEARCH_INSTITUTE,
            CATEGORY_EDUCATION_OFFICE,
            CATEGORY_PUBLIC_CORPORATION,
        ]:
            output = tmp_path / f"{category}.pdf"
            render_document_pdf(_sample_row(), category, output)
            assert output.exists()
            assert output.stat().st_size > 0

    def test_unknown_category_falls_back_to_public_corporation_layout(self, tmp_path):
        output = tmp_path / "unknown.pdf"
        # 방어적 동작 확인 — LAYOUT_SPECS에 없는 카테고리를 줘도 죽지 않아야 한다.
        render_document_pdf(_sample_row(), "not_a_real_category", output)
        assert output.exists()

    def test_non_ok_status_row_still_renders(self, tmp_path):
        output = tmp_path / "error_row.pdf"
        row = _sample_row(status="llm_error", body_text="[에러: simulated failure]")
        render_document_pdf(row, CATEGORY_PUBLIC_CORPORATION, output)
        assert output.exists()
        assert output.stat().st_size > 0

    def test_empty_body_text_does_not_crash(self, tmp_path):
        output = tmp_path / "empty_body.pdf"
        row = _sample_row(body_text="", status="empty_body")
        render_document_pdf(row, CATEGORY_RESEARCH_INSTITUTE, output)
        assert output.exists()

    def test_all_categories_have_layout_specs(self):
        for category in [
            CATEGORY_CENTRAL_MINISTRY,
            CATEGORY_METRO_LOCAL_GOVERNMENT,
            CATEGORY_RESEARCH_INSTITUTE,
            CATEGORY_EDUCATION_OFFICE,
            CATEGORY_PUBLIC_CORPORATION,
        ]:
            assert category in LAYOUT_SPECS

    def test_template_violation_is_rejected_before_file_creation(self, tmp_path):
        output = tmp_path / "nested" / "invalid.pdf"
        row = _sample_row(
            clause_no="5",
            doc_type="approval",
            body_text="관련 부서 협의를 거쳐 최종 승인 처리하였음.",
        )

        with pytest.raises(ValueError, match="템플릿 검증 실패"):
            render_document_pdf(row, CATEGORY_PUBLIC_CORPORATION, output)

        assert not output.exists()

    def test_unknown_explicit_subclause_is_rejected(self, tmp_path):
        output = tmp_path / "unknown-subclause.pdf"
        row = _sample_row(
            clause_no="5",
            doc_type="approval",
            cso_subclause_key="not-a-real-subclause",
        )

        with pytest.raises(ValueError, match="세부조항에 맞는 문서 템플릿"):
            render_document_pdf(row, CATEGORY_PUBLIC_CORPORATION, output)

        assert not output.exists()

    @pytest.mark.parametrize(
        "spec",
        TEMPLATE_VARIANTS.values(),
        ids=lambda spec: spec.template_id,
    )
    def test_all_template_variants_use_declared_source_form(self, tmp_path, monkeypatch, spec):
        rendered: dict[str, str] = {}

        def fake_html_to_pdf(html, output_path, layout, **_kwargs):
            rendered["html"] = html
            output_path.write_bytes(b"%PDF-test")

        monkeypatch.setattr(pdf_render, "_html_to_pdf", fake_html_to_pdf)
        output = tmp_path / f"{spec.template_id}.pdf"
        row = _sample_row(
            clause_no=spec.clause_no,
            doc_type=spec.doc_type,
            cso_subclause_key=spec.subclause_key,
            body_text="템플릿 렌더링 검증용 본문입니다.",
        )

        render_document_pdf(row, CATEGORY_PUBLIC_CORPORATION, output)

        assert output.exists()
        marker = {
            "official_form": 'class="official-fields"',
            "policy_brief_form": 'class="brief-heading"',
            "meeting_record_form": 'class="meeting-title"',
        }[spec.form_format]
        assert marker in rendered["html"]


class TestClassificationGatedMarking:
    """C(기밀)만 대외비 워터마크/스탬프, S(민감)는 마크 없음 — 2026-07-14 결정."""

    def test_s_classification_renders_fine_without_watermark_assets(self, tmp_path):
        output = tmp_path / "s_doc.pdf"
        row = _sample_row(cso_classification="S")
        # watermark_path/stamp_path를 아예 안 줘도 S는 마크를 안 그리니 문제 없어야 한다.
        render_document_pdf(row, CATEGORY_METRO_LOCAL_GOVERNMENT, output)
        assert output.exists()
        assert output.read_bytes()[:4] == b"%PDF"

    def test_c_classification_with_watermark_and_stamp_renders_larger_pdf(self, tmp_path):
        """워터마크/스탬프 이미지가 실제로 삽입되면 PDF 파일 크기가 눈에 띄게 커진다 —
        간접적으로 "마크가 실제로 그려졌는지"를 확인하는 근사 신호로 쓴다."""
        watermark_path = generate_page_watermark(tmp_path / "wm.png", seed=1)
        stamp_path = generate_classification_stamp(tmp_path / "stamp.png", seed=1)

        c_output = tmp_path / "c_doc.pdf"
        render_document_pdf(
            _sample_row(cso_classification="C"), CATEGORY_METRO_LOCAL_GOVERNMENT, c_output,
            watermark_path=watermark_path, stamp_path=stamp_path,
        )

        s_output = tmp_path / "s_doc.pdf"
        render_document_pdf(
            _sample_row(cso_classification="S"), CATEGORY_METRO_LOCAL_GOVERNMENT, s_output,
            watermark_path=watermark_path, stamp_path=stamp_path,
        )

        assert c_output.stat().st_size > s_output.stat().st_size

    def test_c_classification_without_watermark_path_does_not_crash(self, tmp_path):
        """워터마크 에셋이 아직 없어도(예: 초기 스모크 테스트) C 문서 렌더링이 죽지 않아야 한다."""
        output = tmp_path / "c_no_assets.pdf"
        render_document_pdf(_sample_row(cso_classification="C"), CATEGORY_CENTRAL_MINISTRY, output)
        assert output.exists()

    def test_lowercase_c_is_still_treated_as_confidential(self, tmp_path):
        watermark_path = generate_page_watermark(tmp_path / "wm.png", seed=1)
        stamp_path = generate_classification_stamp(tmp_path / "stamp.png", seed=1)
        output = tmp_path / "lowercase_c.pdf"
        render_document_pdf(
            _sample_row(cso_classification="c"), CATEGORY_CENTRAL_MINISTRY, output,
            watermark_path=watermark_path, stamp_path=stamp_path,
        )
        assert output.exists()

    def test_agency_mark_only_applied_for_c_classification(self, tmp_path):
        """agency_mark_path(좌상단 기관 마크)도 대외비/워터마크와 같은 C 전용 게이팅을 따른다."""
        agency_mark_path = generate_agency_letterhead_mark(tmp_path / "mark.png", "국정원.png", seed=1)

        c_output = tmp_path / "c_doc.pdf"
        render_document_pdf(
            _sample_row(cso_classification="C"), CATEGORY_METRO_LOCAL_GOVERNMENT, c_output,
            agency_mark_path=agency_mark_path,
        )

        s_output = tmp_path / "s_doc.pdf"
        render_document_pdf(
            _sample_row(cso_classification="S"), CATEGORY_METRO_LOCAL_GOVERNMENT, s_output,
            agency_mark_path=agency_mark_path,
        )

        assert c_output.stat().st_size > s_output.stat().st_size


class TestRenderedBodyStructure:
    """구현 객체가 아니라 최종 PDF에서 선택 가능한 구조 텍스트를 검증한다."""

    def test_audit_result_uses_numbered_overview_with_ganadara_sub_items(self, tmp_path):
        output = tmp_path / "audit-structure.pdf"
        render_document_pdf(_sample_row(doc_type=DOC_TYPE_AUDIT_RESULT, body_text="첫 문단\n둘째 문단\n셋째 문단"), CATEGORY_PUBLIC_CORPORATION, output)
        text = _pdf_text(output)
        assert "1. 개요" in text
        assert "가. 첫 문단" in text
        assert "나. 둘째 문단" in text

    def test_non_audit_doc_type_uses_standard_bullet_style(self, tmp_path):
        output = tmp_path / "standard-structure.pdf"
        render_document_pdf(_sample_row(doc_type=DOC_TYPE_OFFICIAL_DOCUMENT), CATEGORY_PUBLIC_CORPORATION, output)
        text = _pdf_text(output)
        assert "아 래" in text
        assert "o 첫 번째 문단입니다." in text

    def test_all_doc_types_end_with_geut_marker(self, tmp_path):
        for doc_type in (DOC_TYPE_AUDIT_RESULT, DOC_TYPE_OFFICIAL_DOCUMENT):
            output = tmp_path / f"{doc_type}.pdf"
            render_document_pdf(_sample_row(doc_type=doc_type, body_text="문단 하나"), CATEGORY_PUBLIC_CORPORATION, output)
            assert "끝." in _pdf_text(output)

class TestRenderedApprovalBox:
    def test_matching_department_gets_approval_box(self, tmp_path):
        output = tmp_path / "approval.pdf"
        render_document_pdf(_sample_row(department="감사담당관실"), CATEGORY_PUBLIC_CORPORATION, output)
        text = _pdf_text(output)
        assert "담당" in text and "검토" in text and "결재" in text

    def test_unrelated_department_gets_no_approval_box(self, tmp_path):
        output = tmp_path / "no-approval.pdf"
        render_document_pdf(_sample_row(department="홍보담당관실"), CATEGORY_PUBLIC_CORPORATION, output)
        text = _pdf_text(output)
        assert "주무관" not in text and "부서장" not in text


class TestDocTypeAffectsRenderedPdf:
    def test_audit_result_doc_type_renders_without_error(self, tmp_path):
        output = tmp_path / "audit.pdf"
        row = _sample_row(doc_type=DOC_TYPE_AUDIT_RESULT, body_text="문단 하나\n문단 둘")
        render_document_pdf(row, CATEGORY_METRO_LOCAL_GOVERNMENT, output)
        assert output.exists()
        assert output.read_bytes()[:4] == b"%PDF"

    def test_missing_doc_type_falls_back_to_standard_style(self, tmp_path):
        output = tmp_path / "no_doc_type.pdf"
        row = _sample_row()
        row.pop("doc_type", None)
        render_document_pdf(row, CATEGORY_PUBLIC_CORPORATION, output)
        assert output.exists()
