import json
import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import find_candidates as fc  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _clause_span(*, source="test_src", doc_type, doc_id, text, span_id=1, source_pdf_path="x.pdf"):
    return {
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "span_id": span_id,
        "text": text,
        "source_pdf_path": source_pdf_path,
        "bbox": [0, 0, 1, 1],
        "page_no": 1,
    }


def _admin_span(*, source="test_src", doc_type, doc_id, document_status, span_id=2, source_pdf_path="x.pdf"):
    return {
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "span_id": span_id,
        "text": "행정상태 근거",
        "source_pdf_path": source_pdf_path,
        "document_status": document_status,
        "candidate_kind": "administrative_status",
    }


class TestDocKey:
    def test_prefers_doc_id_over_source_pdf_path(self):
        record = {"source": "moel", "doc_id": "42", "source_pdf_path": "a/b.pdf"}
        assert fc._doc_key(record) == ("moel", "42")

    def test_falls_back_to_source_pdf_path_when_no_doc_id(self):
        record = {"source": "moel", "doc_id": None, "source_pdf_path": "a/b.pdf"}
        assert fc._doc_key(record) == ("moel", "a/b.pdf")


class TestMeasureCells:
    def test_no_rule_defined_when_doc_type_has_no_admin_rules(self, tmp_path, monkeypatch):
        # audit_result는 clause 5의 audit_inspection 세부조항에 유일하게 매핑된
        # doc_type이라 infer_subclause_key가 모호함 없이 바로 audit_inspection을 반환한다.
        _write_jsonl(
            tmp_path / "clause_5.jsonl",
            [_clause_span(doc_type="audit_result", doc_id="d1", text="감사 관련 검토")],
        )
        rules_without_audit_result = {k: v for k, v in fc.ADMIN_STATUS_RULES_BY_DOC_TYPE.items() if k != "audit_result"}
        monkeypatch.setattr(fc, "ADMIN_STATUS_RULES_BY_DOC_TYPE", rules_without_audit_result)

        cells = fc.measure_cells(candidates_root=tmp_path)

        cell = cells[("5", "audit_inspection", "audit_result")]
        assert cell["state"] == fc.NO_RULE_DEFINED
        assert cell["span_candidates"] == 1

    def test_rule_defined_zero_matches_when_no_administrative_candidates(self, tmp_path):
        _write_jsonl(
            tmp_path / "clause_5.jsonl",
            [_clause_span(doc_type="audit_result", doc_id="d1", text="감사 관련 검토")],
        )
        # administrative.jsonl이 아예 없거나 비어 있는 상황

        cells = fc.measure_cells(candidates_root=tmp_path)

        cell = cells[("5", "audit_inspection", "audit_result")]
        assert cell["state"] == fc.RULE_DEFINED_ZERO_MATCHES
        assert cell["span_candidates"] == 1
        assert "by_admin_status" not in cell

    def test_counted_with_per_status_breakdown_when_same_document_matches(self, tmp_path):
        _write_jsonl(
            tmp_path / "clause_5.jsonl",
            [_clause_span(doc_type="audit_result", doc_id="d1", text="감사 관련 검토")],
        )
        _write_jsonl(
            tmp_path / "administrative.jsonl",
            [_admin_span(doc_type="audit_result", doc_id="d1", document_status="감사진행중")],
        )

        cells = fc.measure_cells(candidates_root=tmp_path)

        cell = cells[("5", "audit_inspection", "audit_result")]
        assert cell["state"] == fc.COUNTED
        assert cell["by_admin_status"] == {"감사진행중": 1}

    def test_administrative_candidate_from_unrelated_document_does_not_count(self, tmp_path):
        """같은 doc_type이라도 다른 문서(doc_id가 다름)의 행정상태는 이 셀에
        섞이면 안 된다 — 문서 단위로 매칭해야 한다."""
        _write_jsonl(
            tmp_path / "clause_5.jsonl",
            [_clause_span(doc_type="audit_result", doc_id="d1", text="감사 관련 검토")],
        )
        _write_jsonl(
            tmp_path / "administrative.jsonl",
            [_admin_span(doc_type="audit_result", doc_id="d2", document_status="감사진행중")],
        )

        cells = fc.measure_cells(candidates_root=tmp_path)

        cell = cells[("5", "audit_inspection", "audit_result")]
        assert cell["state"] == fc.RULE_DEFINED_ZERO_MATCHES

    def test_covers_all_36_clause_5_to_8_triples(self, tmp_path):
        cells = fc.measure_cells(candidates_root=tmp_path)
        assert len(cells) == 36
        assert all(clause_no in ("5", "6", "7", "8") for clause_no, _, _ in cells)


class TestIterAnnotatedDocsErrorHandling:
    """이슈 4 회귀 테스트 — 손상된 주석 파일 하나가 전체 실행을 죽이면 안 된다."""

    def test_skips_malformed_json_and_continues(self, tmp_path, capsys):
        good_doc = {"source": "moel", "doc_type": "notification", "doc_id": "1", "pages": []}
        (tmp_path / "good.json").write_text(json.dumps(good_doc, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "bad.json").write_text("{ this is not valid json", encoding="utf-8")

        results = list(fc._iter_annotated_docs(root=tmp_path))

        assert len(results) == 1
        path, doc = results[0]
        assert path.name == "good.json"
        assert doc == good_doc

        captured = capsys.readouterr()
        assert "bad.json" in captured.out

    def test_all_malformed_yields_nothing_without_raising(self, tmp_path):
        (tmp_path / "bad1.json").write_text("not json at all", encoding="utf-8")
        (tmp_path / "bad2.json").write_text("{broken", encoding="utf-8")

        results = list(fc._iter_annotated_docs(root=tmp_path))

        assert results == []
