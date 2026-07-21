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
    def _write_annotated_json_with_span_ids(
        self, annotated_root: Path, rel_path: str, texts: list[str]
    ) -> None:
        pages = [
            {
                "page_no": 1,
                "spans": [
                    {"span_id": i, "is_boilerplate": False, "cleaned_text": text}
                    for i, text in enumerate(texts)
                ],
            }
        ]
        self._write_annotated_json(annotated_root, rel_path, pages)

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

    def test_target_span_id_centers_window_on_matched_span(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        self._write_annotated_json_with_span_ids(
            annotated_root, "moel/notification/1.json",
            ["앞 문단1", "앞 문단2", "근거 문단", "뒤 문단1", "뒤 문단2"],
        )

        text = pilot.load_annotated_document_text(
            "data/moel/notification/1.pdf", annotated_root, target_span_id=2,
        )

        assert text == "앞 문단1\n앞 문단2\n근거 문단\n뒤 문단1\n뒤 문단2"

    def test_target_span_id_not_found_falls_back_to_document_start(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        self._write_annotated_json_with_span_ids(
            annotated_root, "moel/notification/1.json", ["첫 문단", "둘째 문단"],
        )

        text = pilot.load_annotated_document_text(
            "data/moel/notification/1.pdf", annotated_root, target_span_id=999,
        )

        assert text == "첫 문단\n둘째 문단"

    def test_window_excludes_far_context_beyond_budget(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        chunk_size = 500
        num_before = pilot._CONTEXT_CHARS_BEFORE // chunk_size + 3  # 예산을 넘치도록 여유 있게
        num_after = pilot._CONTEXT_CHARS_AFTER // chunk_size + 3
        before_chunks = [f"앞문단{i}" + "가" * chunk_size for i in range(num_before)]
        after_chunks = [f"뒤문단{i}" + "나" * chunk_size for i in range(num_after)]
        texts = before_chunks + ["근거 문단"] + after_chunks
        self._write_annotated_json_with_span_ids(
            annotated_root, "moel/notification/1.json", texts,
        )
        target_span_id = len(before_chunks)

        text = pilot.load_annotated_document_text(
            "data/moel/notification/1.pdf", annotated_root, target_span_id=target_span_id,
        )

        assert "근거 문단" in text
        assert before_chunks[0] not in text  # 창 밖으로 밀려난 가장 먼 앞 문단
        assert after_chunks[-1] not in text  # 창 밖으로 밀려난 가장 먼 뒤 문단
        assert before_chunks[-1] in text  # 근거 span 바로 앞 문단은 창 안에 있어야 함
        assert after_chunks[0] in text  # 근거 span 바로 뒤 문단은 창 안에 있어야 함

    def test_window_still_respects_total_char_cap(self, tmp_path):
        annotated_root = tmp_path / "annotated"
        chunk = "다" * 200
        texts = [chunk for _ in range(60)]  # span_id 0..59, target in the middle
        self._write_annotated_json_with_span_ids(
            annotated_root, "moel/notification/1.json", texts,
        )

        text = pilot.load_annotated_document_text(
            "data/moel/notification/1.pdf", annotated_root, target_span_id=30,
        )

        assert len(text) <= pilot._MAX_SOURCE_DOCUMENT_CHARS


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
        monkeypatch.setattr(
            pilot, "load_annotated_document_text", lambda path, root, **kwargs: None
        )

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), annotated_root=Path("."),
        )

        assert row is None

    def test_success_path_builds_expected_row(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        captured_kwargs = {}

        def fake_load_annotated_document_text(path, root, **kwargs):
            captured_kwargs.update(kwargs)
            return "문서 원문 전체 내용"

        monkeypatch.setattr(pilot, "load_annotated_document_text", fake_load_annotated_document_text)

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
        assert captured_kwargs.get("target_span_id") == 0

    def test_llm_failure_produces_llm_error_status(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        monkeypatch.setattr(
            pilot, "load_annotated_document_text", lambda path, root, **kwargs: "문서 원문"
        )

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
        def fake_generate_clause_document(
            clause_no, *, ordering_agency, production_date, client, model, **kwargs
        ):
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
        def fake_generate_clause_document(
            clause_no, *, ordering_agency, production_date, client, model, **kwargs
        ):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(pilot, "generate_clause_document", fake_generate_clause_document)

        row = pilot.generate_fallback_row(
            "1-fallback-0", "1", client=object(), model="gpt-4o-mini", sampling_seed=42,
            ordering_agency="실제기관명", production_date="2025-01-01",
        )
        assert row["status"] == "llm_error"
        assert set(row.keys()) == set(pilot.CSV_FIELDNAMES) - {"template_id", "template_violations"}

    def test_whitelist_synthetic_agency_source_is_recorded_honestly(self, monkeypatch):
        """1~4호(C트랙)는 화이트리스트 기관을 쓰므로, 실제 DB 값인 척하면 안 된다
        (2026-07-20 plan-eng-review, Approach D)."""

        def fake_generate_clause_document(
            clause_no, *, ordering_agency, production_date, client, model, **kwargs
        ):
            return Document(
                title="[합성] 가상 시나리오",
                ordering_agency=ordering_agency,
                production_date=production_date,
                disclosure_status=DisclosureStatus.CLOSED,
                non_disclosure_reason="제2호 — 안보·국방·통일·외교 국익저해",
                subject_category="안보·국방·통일·외교 국익저해",
                body_text="가상 문서 본문",
                cso_classification=CsoClassification.C,
                cso_sub_clause="2",
                source="synthetic-llm",
                source_url=None,
                doc_type="synthetic_document",
                is_synthetic=True,
            )

        monkeypatch.setattr(pilot, "generate_clause_document", fake_generate_clause_document)

        row = pilot.generate_fallback_row(
            "2-fallback-0", "2", client=object(), model="gpt-4o-mini", sampling_seed=42,
            ordering_agency="국방부", production_date="2026-01-01",
            agency_source="whitelist_synthetic",
        )

        assert row["status"] == "ok"
        assert "화이트리스트 기반 합성" in row["non_disclosure_reason"]
        field_source = json.loads(row["field_source"])
        assert field_source["ordering_agency"] == "whitelist_synthetic_no_db_history"
        assert field_source["production_date"] == "synthesized_plausible_range"

    def test_military_secret_grade_forwarded_to_prompt_and_row(self, monkeypatch):
        captured_kwargs = {}

        def fake_generate_clause_document(
            clause_no, *, ordering_agency, production_date, client, model, **kwargs
        ):
            captured_kwargs.update(kwargs)
            return Document(
                title="[합성] 가상 시나리오", ordering_agency=ordering_agency,
                production_date=production_date, disclosure_status=DisclosureStatus.CLOSED,
                non_disclosure_reason="제2호 — 안보·국방·통일·외교 국익저해",
                subject_category="안보·국방·통일·외교 국익저해", body_text="가상 문서 본문",
                cso_classification=CsoClassification.C, cso_sub_clause="2",
                source="synthetic-llm", source_url=None, doc_type="synthetic_document",
                is_synthetic=True,
            )

        monkeypatch.setattr(pilot, "generate_clause_document", fake_generate_clause_document)

        row = pilot.generate_fallback_row(
            "2-fallback-0", "2", client=object(), model="gpt-4o-mini", sampling_seed=42,
            ordering_agency="국방부", production_date="2026-01-01",
            agency_source="whitelist_synthetic", military_secret_grade="1급",
        )

        assert captured_kwargs.get("military_secret_grade") == "1급"
        assert row["military_secret_grade"] == "1급"

    def test_military_secret_grade_defaults_to_empty_string(self, monkeypatch):
        def fake_generate_clause_document(
            clause_no, *, ordering_agency, production_date, client, model, **kwargs
        ):
            return Document(
                title="[합성] 가상 시나리오", ordering_agency=ordering_agency,
                production_date=production_date, disclosure_status=DisclosureStatus.CLOSED,
                non_disclosure_reason="제1호 — 법률상 비밀·비공개 규정",
                subject_category="법률상 비밀·비공개 규정", body_text="가상 문서 본문",
                cso_classification=CsoClassification.C, cso_sub_clause="1",
                source="synthetic-llm", source_url=None, doc_type="synthetic_document",
                is_synthetic=True,
            )

        monkeypatch.setattr(pilot, "generate_clause_document", fake_generate_clause_document)

        row = pilot.generate_fallback_row(
            "1-fallback-0", "1", client=object(), model="gpt-4o-mini", sampling_seed=42,
            ordering_agency="검찰청", production_date="2026-01-01",
        )

        assert row["military_secret_grade"] == ""


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


class TestFilenameFromTitle:
    def test_strips_brackets_and_converts_spaces_and_dashes(self):
        result = pilot._filename_from_title("[문서 근거] 국가안보 관련 사안 — 국방부", fallback="fallback-id")
        assert result == "문서_근거_국가안보_관련_사안_-_국방부"

    def test_removes_commas_and_other_disallowed_punctuation(self):
        result = pilot._filename_from_title("보고서, 요약(안)", fallback="fallback-id")
        assert result == "보고서_요약안"

    def test_empty_title_falls_back(self):
        assert pilot._filename_from_title("", fallback="5-span-0") == "5-span-0"

    def test_title_of_only_disallowed_chars_falls_back(self):
        assert pilot._filename_from_title("···///", fallback="5-span-0") == "5-span-0"


class TestUniquePdfOutputPath:
    def test_duplicate_titles_get_disambiguating_suffix(self, tmp_path):
        used: dict[str, int] = {}
        row_a = {"title": "대북 접경지역 군사대비태세 강화", "row_id": "1-fallback-0"}
        row_b = {"title": "대북 접경지역 군사대비태세 강화", "row_id": "1-fallback-1"}
        path_a = pilot._unique_pdf_output_path(tmp_path, row_a, used)
        path_b = pilot._unique_pdf_output_path(tmp_path, row_b, used)
        assert path_a != path_b
        assert path_a.name == "대북_접경지역_군사대비태세_강화.pdf"
        assert path_b.name == "대북_접경지역_군사대비태세_강화_2.pdf"


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
        # 2026-07-21 사용자 결정: PDF 파일명은 row_id가 아니라 문서 제목(title)
        # 기반이어야 한다 — row_id는 여전히 CSV 컬럼으로만 남는다.
        assert (pdf_dir / "테스트_문서.pdf").exists()  # S — 마크 없음
        assert (pdf_dir / "합성_폴백_문서.pdf").exists()  # C — 워터마크+스탬프+기관마크 적용
        assert (pdf_dir / "_stamp_confidential.png").exists()
        # 워터마크/좌상단 기관마크는 기관 로고 파일명별 캐시 파일로 생성된다 —
        # agency_logo_filename이 비어 있으면 정부부처 공용 마크로 폴백하므로
        # C 행이 하나뿐이면 정확히 1개씩 생겨야 한다.
        watermark_files = list(pdf_dir.glob("_watermark_*.png"))
        assert len(watermark_files) == 1
        letterhead_files = list(pdf_dir.glob("_letterhead_*.png"))
        assert len(letterhead_files) == 1

    def test_watermark_cache_reused_across_c_rows_sharing_same_agency_logo(self, tmp_path):
        """워터마크/기관마크는 기관 로고 파일명별로 캐시된다 — 같은 로고를 쓰는
        행끼리는 재사용하고, 다른 로고는 별도 파일을 만든다."""
        csv_path = tmp_path / "cs_pilot_output.csv"
        logo_filenames = ["국정원.png"] * 3 + ["정부부처.png"] * 2
        rows = []
        for i, logo_filename in enumerate(logo_filenames):
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
                    "agency_logo_filename": logo_filename,
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
        # 로고는 2종류(국정원/정부부처)뿐이므로 캐시가 재사용되면 파일도 2개여야 한다.
        watermark_files = list(pdf_dir.glob("_watermark_*.png"))
        assert len(watermark_files) == 2
        letterhead_files = list(pdf_dir.glob("_letterhead_*.png"))
        assert len(letterhead_files) == 2

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
