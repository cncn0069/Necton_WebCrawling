import csv
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import generate_cs_pilot as pilot  # noqa: E402

from rd2.generators.generate import SeededResult  # noqa: E402
from rd2.schema.models import CsoClassification, DisclosureStatus, Document  # noqa: E402


class TestFetchAllSpanCandidates:
    def test_buckets_by_clause_file(self, tmp_path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        (candidates_dir / "clause_5.jsonl").write_text(
            json.dumps({"source": "moel", "doc_type": "notification", "doc_id": "1",
                        "span_id": 0, "text": "감사 관련", "source_pdf_path": "data/moel/notification/1.pdf"},
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (candidates_dir / "clause_6.jsonl").write_text("", encoding="utf-8")

        buckets = pilot.fetch_all_span_candidates(candidates_dir)

        assert set(buckets.keys()) == {"5", "6", "7", "8"}
        assert len(buckets["5"]) == 1
        assert buckets["5"][0]["source"] == "moel"
        assert buckets["6"] == []
        assert buckets["7"] == []  # 파일 자체가 없어도 빈 리스트

    def test_missing_file_returns_empty_list(self, tmp_path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        buckets = pilot.fetch_all_span_candidates(candidates_dir)
        assert all(buckets[clause] == [] for clause in ("5", "6", "7", "8"))


class TestSampleCandidates:
    def test_returns_all_when_fewer_than_count(self):
        import random

        candidates = [{"span_id": i} for i in range(3)]
        result = pilot._sample_candidates(candidates, 5, random.Random(42))
        assert len(result) == 3

    def test_samples_exact_count_when_enough(self):
        import random

        candidates = [{"span_id": i} for i in range(10)]
        result = pilot._sample_candidates(candidates, 3, random.Random(42))
        assert len(result) == 3

    def test_deterministic_given_fixed_seed(self):
        import random

        candidates = [{"span_id": i} for i in range(10)]
        result_a = pilot._sample_candidates(candidates, 3, random.Random(42))
        result_b = pilot._sample_candidates(candidates, 3, random.Random(42))
        assert result_a == result_b


class TestLoadAnnotatedDocumentText:
    def _write_annotated_json(self, annotated_root: Path, rel_path: str, pages: list[dict]) -> None:
        json_path = annotated_root / rel_path
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps({"source": "moel", "doc_type": "notification", "doc_id": "1", "pages": pages},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    def test_joins_non_boilerplate_spans_in_page_order(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        pages = [
            {"page_no": 1, "spans": [
                {"is_boilerplate": True, "cleaned_text": "쪽번호 1"},
                {"is_boilerplate": False, "cleaned_text": "첫 문단"},
            ]},
            {"page_no": 2, "spans": [
                {"is_boilerplate": False, "cleaned_text": "둘째 문단"},
            ]},
        ]
        self._write_annotated_json(annotated_root, "moel/notification/1.json", pages)

        text = pilot.load_annotated_document_text("data/moel/notification/1.pdf", annotated_root)

        assert text == "첫 문단\n둘째 문단"

    def test_handles_backslash_separators(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        pages = [{"page_no": 1, "spans": [{"is_boilerplate": False, "cleaned_text": "본문"}]}]
        self._write_annotated_json(annotated_root, "moel/notification/1.json", pages)

        text = pilot.load_annotated_document_text(r"data\moel\notification\1.pdf", annotated_root)

        assert text == "본문"

    def test_missing_json_returns_none(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        annotated_root.mkdir()
        assert pilot.load_annotated_document_text("data/moel/notification/missing.pdf", annotated_root) is None

    def test_empty_source_path_returns_none(self, tmp_path):
        assert pilot.load_annotated_document_text("", tmp_path) is None

    def test_all_boilerplate_returns_none(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        pages = [{"page_no": 1, "spans": [{"is_boilerplate": True, "cleaned_text": "쪽번호"}]}]
        self._write_annotated_json(annotated_root, "moel/notification/1.json", pages)

        assert pilot.load_annotated_document_text("data/moel/notification/1.pdf", annotated_root) is None

    def test_truncates_long_document(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        long_text = "가" * (pilot._MAX_SOURCE_DOCUMENT_CHARS + 500)
        pages = [{"page_no": 1, "spans": [{"is_boilerplate": False, "cleaned_text": long_text}]}]
        self._write_annotated_json(annotated_root, "moel/notification/1.json", pages)

        text = pilot.load_annotated_document_text("data/moel/notification/1.pdf", annotated_root)

        assert len(text) == pilot._MAX_SOURCE_DOCUMENT_CHARS

    def test_rejects_parent_directory_traversal(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        annotated_root.mkdir()
        self._write_annotated_json(tmp_path, "secret.json", [
            {"page_no": 1, "spans": [{"is_boilerplate": False, "cleaned_text": "secret"}]}
        ])

        text = pilot.load_annotated_document_text("data/../secret.pdf", annotated_root)

        assert text is None

    def test_rejects_absolute_data_path(self, tmp_path):
        assert pilot.load_annotated_document_text("/data/moel/1.pdf", tmp_path) is None


class TestGenerateSpanSeededRow:
    def _candidate(self, **overrides) -> dict:
        defaults = dict(
            source="moel", doc_type="notification", doc_id="1", span_id=0,
            text="감사 관련 내부검토", source_pdf_path="data/moel/notification/1.pdf",
        )
        defaults.update(overrides)
        return defaults

    def test_skips_when_agency_cannot_be_resolved(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: None)

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), annotated_root=Path("."),
        )

        assert row is None

    def test_skips_when_annotated_text_not_found(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        monkeypatch.setattr(pilot, "load_annotated_document_text", lambda path, root: None)

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), annotated_root=Path("."),
        )

        assert row is None

    def test_success_path_builds_expected_row(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        monkeypatch.setattr(
            pilot, "load_annotated_document_text", lambda path, root: "문서 원문 전체 내용"
        )

        def fake_generate_span_seeded_body(seed, *, client, model):
            assert seed.ordering_agency == "고용노동부"
            assert seed.source_document_text == "문서 원문 전체 내용"
            assert seed.matched_span_text == "감사 관련 내부검토"
            return SeededResult(
                department="감사담당관실", unit_task="내부감사 계획", production_date="2025-03-01",
                body_text="고용노동부 감사 관련 문서 본문", tokens_in=120, tokens_out=80,
            )

        monkeypatch.setattr(pilot, "generate_span_seeded_body", fake_generate_span_seeded_body)

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), annotated_root=Path("."),
        )

        assert row["status"] == "ok"
        assert row["seed_type"] == "span_seeded"
        assert row["seed_span_id"] == 0
        assert row["clause_no"] == "5"
        assert row["ordering_agency"] == "고용노동부"
        assert row["body_text"] == "고용노동부 감사 관련 문서 본문"
        assert row["matched_span_text"] == "감사 관련 내부검토"
        assert row["doc_type"] == "audit_result"
        assert row["cso_subclause_key"] == "audit_inspection"
        assert row["is_synthetic"] is True
        assert set(row.keys()) == set(pilot.CSV_FIELDNAMES) - {"template_id", "template_violations"}

    def test_llm_failure_produces_llm_error_status(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        monkeypatch.setattr(pilot, "load_annotated_document_text", lambda path, root: "문서 원문")

        def fake_generate_span_seeded_body(seed, *, client, model):
            raise RuntimeError("simulated API failure")

        monkeypatch.setattr(pilot, "generate_span_seeded_body", fake_generate_span_seeded_body)

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), annotated_root=Path("."),
        )

        assert row["status"] == "llm_error"
        assert "simulated API failure" in row["body_text"]


class TestGenerateFallbackRow:
    def test_success_path_preserves_real_agency_and_date(self, monkeypatch):
        def fake_generate_clause_document(clause_no, *, ordering_agency, production_date, client, model):
            assert ordering_agency == "실제기관명"
            assert production_date == "2025-01-01"
            return Document(
                title="[합성] 가상 시나리오",
                ordering_agency=ordering_agency,
                production_date=production_date,
                disclosure_status=DisclosureStatus.CLOSED,
                non_disclosure_reason="제1호 — 법률상 비밀·비공개 규정",
                subject_category="법률상 비밀·비공개 규정",
                body_text="가상 문서 본문",
                cso_classification=CsoClassification.C,
                cso_sub_clause="1",
                source="synthetic-llm",
                source_url=None,
                doc_type="synthetic_document",
                is_synthetic=True,
            )

        monkeypatch.setattr(pilot, "generate_clause_document", fake_generate_clause_document)

        row = pilot.generate_fallback_row(
            "1-fallback-0", "1", client=object(), model="gpt-4o-mini", sampling_seed=42,
            ordering_agency="실제기관명", production_date="2025-01-01",
        )

        assert row["status"] == "ok"
        assert row["seed_type"] == "synthetic_fallback"
        assert row["seed_span_id"] == ""
        assert row["ordering_agency"] == "실제기관명"
        assert row["production_date"] == "2025-01-01"
        assert row["body_text"] == "가상 문서 본문"
        from rd2.storage.naming import DOC_TYPE_OFFICIAL_DOCUMENT

        assert row["doc_type"] == DOC_TYPE_OFFICIAL_DOCUMENT
        assert row["cso_subclause_key"] == "legal_secret"
        assert set(row.keys()) == set(pilot.CSV_FIELDNAMES) - {"template_id", "template_violations"}

    def test_llm_failure_produces_llm_error_status_and_keeps_row(self, monkeypatch):
        def fake_generate_clause_document(clause_no, *, ordering_agency, production_date, client, model):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(pilot, "generate_clause_document", fake_generate_clause_document)

        row = pilot.generate_fallback_row(
            "1-fallback-0", "1", client=object(), model="gpt-4o-mini", sampling_seed=42,
            ordering_agency="실제기관명", production_date="2025-01-01",
        )
        assert row["status"] == "llm_error"
        assert set(row.keys()) == set(pilot.CSV_FIELDNAMES) - {"template_id", "template_violations"}


class TestGenerateAdministrativeStatusSample:
    def test_attachment_missing_sample_is_partial_without_clause(self):
        row = pilot.generate_admin_status_sample_row(sampling_seed=42)

        assert row["row_id"] == "admin-status-attachment-missing-0"
        assert row["cso_classification"] == "S"
        assert row["clause_no"] == ""
        assert row["disclosure_status"] == "부분공개"
        assert row["document_status"] == "첨부미등록"
        assert "첨부파일 미등록" in row["non_disclosure_reason"]
        assert set(row.keys()) == set(pilot.CSV_FIELDNAMES) - {"template_id", "template_violations"}


class TestApplyTemplateValidation:
    def test_no_template_leaves_row_unchanged(self):
        row = {"clause_no": "5", "doc_type": "notification", "status": "ok", "body_text": "본문"}
        pilot._apply_template_validation(row)
        assert row["template_id"] == ""
        assert row["template_violations"] == ""
        assert row["status"] == "ok"

    def test_template_with_no_violations_keeps_status_ok(self):
        from rd2.storage.naming import DOC_TYPE_APPROVAL

        row = {
            "clause_no": "5", "doc_type": DOC_TYPE_APPROVAL, "status": "ok",
            "body_text": "내부 검토를 진행 중임", "document_status": "",
        }
        pilot._apply_template_validation(row)
        assert row["template_id"] == "T5-1"
        assert row["template_violations"] == ""
        assert row["status"] == "ok"

    def test_violation_downgrades_ok_status_to_template_violation(self):
        from rd2.storage.naming import DOC_TYPE_APPROVAL

        row = {
            "clause_no": "5", "doc_type": DOC_TYPE_APPROVAL, "status": "ok",
            "body_text": "결재 완료되었음", "document_status": "",
        }
        pilot._apply_template_validation(row)
        assert row["template_id"] == "T5-1"
        assert "결재 완료" in row["template_violations"]
        assert row["status"] == "template_violation"

    def test_violation_does_not_override_non_ok_status(self):
        from rd2.storage.naming import DOC_TYPE_APPROVAL

        row = {
            "clause_no": "5", "doc_type": DOC_TYPE_APPROVAL, "status": "llm_error",
            "body_text": "결재 완료되었음", "document_status": "",
        }
        pilot._apply_template_validation(row)
        assert row["status"] == "llm_error"


class TestConnectMariadb:
    def test_missing_env_var_raises_clear_runtime_error(self, monkeypatch):
        monkeypatch.delenv("MARIADB_HOST", raising=False)
        with pytest.raises(RuntimeError, match="MariaDB 접속 정보 누락"):
            pilot.connect_mariadb()


class TestRenderPdfsForCsv:
    def _write_sample_csv(self, csv_path):
        rows = [
            {name: "" for name in pilot.CSV_FIELDNAMES},
        ]
        rows[0].update(
            {
                "row_id": "5-span-0",
                "seed_type": "span_seeded",
                "clause_no": "5",
                "cso_classification": "S",
                "title": "테스트 문서",
                "ordering_agency": "경상북도",
                "body_text": "본문 내용입니다.",
                "status": "ok",
            }
        )
        rows.append({name: "" for name in pilot.CSV_FIELDNAMES})
        rows[1].update(
            {
                "row_id": "1-fallback-0",
                "seed_type": "synthetic_fallback",
                "clause_no": "1",
                "cso_classification": "C",
                "title": "합성 폴백 문서",
                "ordering_agency": "실제기관명",
                "body_text": "폴백 본문입니다.",
                "status": "ok",
            }
        )
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

    def test_renders_one_pdf_per_row_and_shared_security_mark(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_sample_csv(csv_path)
        pdf_dir = tmp_path / "pdfs"

        rendered = pilot.render_pdfs_for_csv(csv_path, pdf_dir, sampling_seed=42)

        assert rendered == 2
        assert (pdf_dir / "5-span-0.pdf").exists()  # S — 마크 없음
        assert (pdf_dir / "1-fallback-0.pdf").exists()  # C — 워터마크+스탬프 적용
        assert (pdf_dir / "_stamp_confidential.png").exists()
        # 워터마크는 row별 캐시 파일(_watermark_char_N.png)로 생성된다 —
        # C 행이 하나뿐이므로 정확히 1개가 생겨야 한다.
        watermark_files = list(pdf_dir.glob("_watermark_char_*.png"))
        assert len(watermark_files) == 1

    def test_watermark_cache_reused_across_c_rows_sharing_same_character(self, tmp_path):
        """워터마크 문자는 row별로 다시 그리되, 같은 문자가 나오는 행끼리는
        캐시를 재사용해 중복 파일을 만들지 않는다."""
        csv_path = tmp_path / "cs_pilot_output.csv"
        rows = []
        for i in range(5):
            row = {name: "" for name in pilot.CSV_FIELDNAMES}
            row.update(
                {
                    "row_id": f"1-fallback-{i}",
                    "seed_type": "synthetic_fallback",
                    "clause_no": "1",
                    "cso_classification": "C",
                    "title": f"합성 폴백 문서 {i}",
                    "ordering_agency": "실제기관명",
                    "body_text": "폴백 본문입니다.",
                    "status": "ok",
                }
            )
            rows.append(row)
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

        pdf_dir = tmp_path / "pdfs"
        rendered = pilot.render_pdfs_for_csv(csv_path, pdf_dir, sampling_seed=42)

        assert rendered == 5
        # 문자는 최대 7종류(security_mark._WATERMARK_CHARS)뿐이므로
        # 워터마크 캐시 파일 개수는 5개를 넘을 수 없다(캐시가 안 됐다면 5개).
        watermark_files = list(pdf_dir.glob("_watermark_char_*.png"))
        assert 1 <= len(watermark_files) <= 5

    def test_watermark_seed_for_row_is_deterministic(self):
        row = {"row_id": "5-span-0", "seed_span_id": "123"}
        seed_a = pilot._watermark_seed_for_row(row, sampling_seed=42)
        seed_b = pilot._watermark_seed_for_row(row, sampling_seed=42)
        assert seed_a == seed_b

    def test_watermark_seed_has_stable_expected_value(self):
        row = {"row_id": "5-span-0", "seed_span_id": "123"}
        assert pilot._watermark_seed_for_row(row, sampling_seed=42) == 158676051

    def test_rejects_row_id_path_traversal(self, tmp_path):
        csv_path = tmp_path / "unsafe.csv"
        row = {name: "" for name in pilot.CSV_FIELDNAMES}
        row.update({"row_id": "../outside", "status": "ok"})
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerow(row)

        with pytest.raises(ValueError, match="안전하지 않은 row_id"):
            pilot.render_pdfs_for_csv(csv_path, tmp_path / "pdfs", sampling_seed=42)

        assert not (tmp_path / "outside.pdf").exists()

    def test_template_violation_row_is_skipped_without_aborting_batch(self, tmp_path):
        csv_path = tmp_path / "violations.csv"
        row = {name: "" for name in pilot.CSV_FIELDNAMES}
        row.update({
            "row_id": "5-span-0",
            "status": "template_violation",
            "template_violations": "최종 승인 문구 금지",
        })
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerow(row)

        rendered = pilot.render_pdfs_for_csv(csv_path, tmp_path / "pdfs", sampling_seed=42)

        assert rendered == 0
        assert not (tmp_path / "pdfs" / "5-span-0.pdf").exists()

    def test_missing_csv_raises_clear_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="CSV 파일이 없습니다"):
            pilot.render_pdfs_for_csv(tmp_path / "does_not_exist.csv", tmp_path / "pdfs", sampling_seed=42)


class TestExistingRowIds:
    def test_missing_file_returns_empty_set(self, tmp_path):
        assert pilot._existing_row_ids(tmp_path / "does_not_exist.csv") == set()

    def test_reads_row_ids_from_existing_csv(self, tmp_path):
        csv_path = tmp_path / "out.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
            writer.writeheader()
            row = {name: "" for name in pilot.CSV_FIELDNAMES}
            row["row_id"] = "5-span-0"
            writer.writerow(row)

        assert pilot._existing_row_ids(csv_path) == {"5-span-0"}
