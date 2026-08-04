import csv
import gzip
import hashlib
import json
import random
import threading
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
from openai import RateLimitError

import generate_cs_pilot as pilot  # noqa: E402

from rd2.generators.generate import SeededResult  # noqa: E402
from rd2.generators.template_matrix import TARGET_BY_KEY  # noqa: E402
from rd2.schema.models import CsoClassification, DisclosureStatus, Document  # noqa: E402


def _make_rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


class TestFetchAllSpanCandidates:
    def test_buckets_by_clause_file(self, tmp_path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        (candidates_dir / "clause_5.jsonl").write_text(
            json.dumps({"source": "moel", "doc_type": "notification", "doc_id": "1",
                        "candidate_id": "c1", "run_id": "run-1", "line_ids": [0],
                        "text": "감사 관련", "source_path": "data/moel/notification/1.pdf"},
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

    def test_record_run_id_must_match_manifest_run(self, tmp_path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        (candidates_dir / "clause_5.jsonl").write_text(
            json.dumps({"candidate_id": "c1", "run_id": "other"}) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="run_id가 manifest와 불일치"):
            pilot.fetch_all_span_candidates(candidates_dir, expected_run_id="run-1")

    def test_record_rule_version_must_match_manifest(self, tmp_path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        (candidates_dir / "clause_5.jsonl").write_text(
            json.dumps(
                {"candidate_id": "c1", "run_id": "run-1", "rule_version": "old-rules"}
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="rule_version이 manifest와 불일치"):
            pilot.fetch_all_span_candidates(
                candidates_dir,
                expected_run_id="run-1",
                expected_rule_version="new-rules",
            )

    def test_record_counts_must_match_manifest(self, tmp_path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        (candidates_dir / "clause_5.jsonl").write_text("", encoding="utf-8")
        expected_counts = {clause_no: 0 for clause_no in ("5", "6", "7", "8")}
        expected_counts["5"] = 1

        with pytest.raises(ValueError, match="개수가 manifest와 불일치"):
            pilot.fetch_all_span_candidates(
                candidates_dir,
                expected_run_id="run-1",
                expected_counts=expected_counts,
            )

    def test_partial_manifest_requires_explicit_override(self, tmp_path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        manifest = {
            "run_id": "run-1",
            "rule_version": "rules-v2",
            "status": "partial",
            "counts": {},
        }
        (candidates_dir / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        with pytest.raises(RuntimeError, match="완결되지"):
            pilot.load_candidate_manifest(candidates_dir)
        assert pilot.load_candidate_manifest(candidates_dir, allow_partial=True) == manifest


class TestSampleCandidates:
    def test_returns_all_when_fewer_than_count(self):
        import random

        candidates = [{"candidate_id": str(i)} for i in range(3)]
        result = pilot._sample_candidates(candidates, 5, random.Random(42))
        assert len(result) == 3

    def test_samples_exact_count_when_enough(self):
        import random

        candidates = [{"candidate_id": str(i)} for i in range(10)]
        result = pilot._sample_candidates(candidates, 3, random.Random(42))
        assert len(result) == 3

    def test_deterministic_given_fixed_seed(self):
        import random

        candidates = [{"candidate_id": str(i)} for i in range(10)]
        result_a = pilot._sample_candidates(candidates, 3, random.Random(42))
        result_b = pilot._sample_candidates(candidates, 3, random.Random(42))
        assert result_a == result_b

    def test_removes_exact_text_duplicates_within_same_document(self):
        import random

        candidates = [
            {"candidate_id": "first", "source_path": "data/a.pdf", "text": "반복 문구"},
            {"candidate_id": "duplicate", "source_path": "data/a.pdf", "text": "반복 문구"},
            {"candidate_id": "other", "source_path": "data/a.pdf", "text": "다른 문구"},
        ]

        result = pilot._sample_candidates(candidates, 10, random.Random(42))

        assert [candidate["candidate_id"] for candidate in result] == ["first", "other"]

    def test_preserves_same_text_from_different_documents(self):
        import random

        candidates = [
            {"candidate_id": "a", "source_path": "data/a.pdf", "text": "공통 문구"},
            {"candidate_id": "b", "source_path": "data/b.pdf", "text": "공통 문구"},
        ]

        result = pilot._sample_candidates(candidates, 10, random.Random(42))

        assert [candidate["candidate_id"] for candidate in result] == ["a", "b"]

    def test_deduplicates_before_sampling(self):
        import random

        candidates = [
            {"candidate_id": "first", "source_path": "data/a.pdf", "text": "반복 문구"},
            {"candidate_id": "duplicate", "source_path": "data/a.pdf", "text": "반복 문구"},
            {"candidate_id": "b", "source_path": "data/b.pdf", "text": "문구 B"},
            {"candidate_id": "c", "source_path": "data/c.pdf", "text": "문구 C"},
        ]

        result = pilot._sample_candidates(candidates, 3, random.Random(42))

        assert [candidate["candidate_id"] for candidate in result] == ["first", "b", "c"]


class TestSynthesizeReleaseDueDate:
    def test_uses_production_date_when_it_is_in_the_future(self):
        due = pilot._synthesize_release_due_date(
            "2027-01-01", "row-1", today=date(2026, 7, 27)
        )

        delta = date.fromisoformat(due) - date(2027, 1, 1)
        assert 30 <= delta.days <= 180

    def test_never_returns_a_past_due_date_for_an_old_document(self):
        due = pilot._synthesize_release_due_date(
            "2023-01-01", "row-1", today=date(2026, 7, 27)
        )

        delta = date.fromisoformat(due) - date(2026, 7, 27)
        assert 30 <= delta.days <= 180

    def test_invalid_production_date_returns_empty_string(self):
        assert pilot._synthesize_release_due_date(
            "not-a-date", "row-1", today=date(2026, 7, 27)
        ) == ""


class TestLoadExtractedDocumentText:
    SOURCE_PATH = "data/moel/notification/1.pdf"
    EXTRACTION_ID = "extract-v2-1"

    def _write_extracted(
        self,
        data_root: Path,
        extracted_root: Path,
        texts: list[str],
        *,
        extraction_id: str | None = None,
        source_bytes: bytes = b"source bytes",
    ) -> None:
        source_file = data_root.parent / Path(self.SOURCE_PATH)
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(source_bytes)
        lines = [
            {
                "line_id": i,
                "block_id": i,
                "order": i,
                "text": text,
                "bbox_pt": None,
                "style_runs": [],
            }
            for i, text in enumerate(texts)
        ]
        document = {
            "schema_version": 2,
            "extraction_id": extraction_id or self.EXTRACTION_ID,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_path": self.SOURCE_PATH,
            "source": "moel",
            "doc_type": "notification",
            "doc_id": "1",
            "source_format": "pdf",
            "extraction": {},
            "status": "ok",
            "error": None,
            "quality": {},
            "pages": [
                {
                    "page": 1,
                    "width_pt": 595.0,
                    "height_pt": 842.0,
                    "rotation": 0,
                    "lines": lines,
                }
            ],
        }
        path = pilot.extraction_output_path(source_file, data_root, extracted_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(document, f, ensure_ascii=False)

    def _candidate(self, text: str, line_ids: list[int], **overrides) -> dict:
        candidate = {
            "candidate_id": "candidate-1",
            "run_id": "run-1",
            "extraction_id": self.EXTRACTION_ID,
            "source_path": self.SOURCE_PATH,
            "line_ids": line_ids,
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "page": 1,
        }
        candidate.update(overrides)
        return candidate

    def test_centers_context_on_candidate_line_ids(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        self._write_extracted(
            data_root,
            extracted_root,
            ["앞 문단1", "앞 문단2", "근거 문단", "뒤 문단1", "뒤 문단2"],
        )

        text = pilot.load_extracted_document_text(
            self._candidate("근거 문단", [2]), data_root, extracted_root,
            cache=pilot._BoundedDocumentCache(2),
        )

        assert text == "앞 문단1\n앞 문단2\n근거 문단\n뒤 문단1\n뒤 문단2"

    def test_multi_line_candidate_uses_extraction_join_convention(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        self._write_extracted(data_root, extracted_root, ["앞", "근거", "문단", "뒤"])

        text = pilot.load_extracted_document_text(
            self._candidate("근거 문단", [1, 2]), data_root, extracted_root,
            cache=pilot._BoundedDocumentCache(2),
        )

        assert text == "앞\n근거\n문단\n뒤"

    def test_rejects_stale_extraction_id(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        self._write_extracted(data_root, extracted_root, ["근거"], extraction_id="new-id")

        with pytest.raises(pilot.CandidateSourceMismatch, match="stale candidate"):
            pilot.load_extracted_document_text(
                self._candidate("근거", [0]), data_root, extracted_root,
                cache=pilot._BoundedDocumentCache(2),
            )

    def test_rejects_source_changed_after_extraction(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        self._write_extracted(data_root, extracted_root, ["근거"])
        source_file = data_root.parent / Path(self.SOURCE_PATH)
        source_file.write_bytes(b"changed after extraction")

        with pytest.raises(pilot.CandidateSourceMismatch, match="source_sha256"):
            pilot.load_extracted_document_text(
                self._candidate("근거", [0]), data_root, extracted_root,
                cache=pilot._BoundedDocumentCache(2),
            )

    def test_rejects_text_hash_mismatch_before_loading(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pilot, "read_json_gz", lambda path: pytest.fail("must not load"))
        candidate = self._candidate("근거", [0], text_sha256="0" * 64)

        with pytest.raises(pilot.CandidateSourceMismatch, match="text_sha256"):
            pilot.load_extracted_document_text(
                candidate, tmp_path / "data", tmp_path / "data/extracted",
                cache=pilot._BoundedDocumentCache(2),
            )

    def test_rejects_changed_line_text_instead_of_falling_back(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        self._write_extracted(data_root, extracted_root, ["현재 텍스트"])

        with pytest.raises(pilot.CandidateSourceMismatch, match="line_ids 텍스트"):
            pilot.load_extracted_document_text(
                self._candidate("예전 텍스트", [0]), data_root, extracted_root,
                cache=pilot._BoundedDocumentCache(2),
            )

    def test_rejects_missing_line_id_instead_of_falling_back(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        self._write_extracted(data_root, extracted_root, ["근거"])

        with pytest.raises(pilot.CandidateSourceMismatch, match="찾지 못함"):
            pilot.load_extracted_document_text(
                self._candidate("근거", [999]), data_root, extracted_root,
                cache=pilot._BoundedDocumentCache(2),
            )

    def test_window_respects_context_and_total_budgets(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        chunk_size = 300
        budget_per_side = pilot._CONTEXT_TOTAL_BUDGET_CHARS // 2
        num_before = budget_per_side // chunk_size + 3
        num_after = budget_per_side // chunk_size + 3
        before = [f"앞{i}" + "가" * chunk_size for i in range(num_before)]
        after = [f"뒤{i}" + "나" * chunk_size for i in range(num_after)]
        texts = [*before, "근거", *after]
        self._write_extracted(data_root, extracted_root, texts)

        text = pilot.load_extracted_document_text(
            self._candidate("근거", [len(before)]), data_root, extracted_root,
            cache=pilot._BoundedDocumentCache(2),
        )

        assert len(text) <= pilot._ANCHOR_HARD_CEILING_CHARS
        assert "근거" in text
        assert before[0] not in text
        assert after[-1] not in text
        assert before[-1] in text
        assert after[0] in text

    def _write_extracted_with_block_ids(
        self, data_root: Path, extracted_root: Path, texts: list[str], block_ids: list[object]
    ) -> None:
        """_write_extracted()와 같지만 줄마다 block_id를 직접 지정한다(기본
        fixture는 줄마다 다른 block_id를 쓰므로, block 경계 로직을 검증하려면
        여러 줄이 같은 block_id를 공유하는 케이스가 따로 필요하다)."""
        source_file = data_root.parent / Path(self.SOURCE_PATH)
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_bytes = b"source bytes"
        source_file.write_bytes(source_bytes)
        lines = [
            {
                "line_id": i, "block_id": block_ids[i], "order": i, "text": text,
                "bbox_pt": None, "style_runs": [],
            }
            for i, text in enumerate(texts)
        ]
        document = {
            "schema_version": 2, "extraction_id": self.EXTRACTION_ID,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_path": self.SOURCE_PATH, "source": "moel", "doc_type": "notification",
            "doc_id": "1", "source_format": "pdf", "extraction": {}, "status": "ok",
            "error": None, "quality": {},
            "pages": [{"page": 1, "width_pt": 595.0, "height_pt": 842.0, "rotation": 0, "lines": lines}],
        }
        path = pilot.extraction_output_path(source_file, data_root, extracted_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(document, f, ensure_ascii=False)

    def test_window_never_splits_a_block(self, tmp_path):
        """근거 span 앞에 같은 block_id를 공유하는 표 행 여러 개가 있을 때,
        예산이 그 block 전체를 다 못 담으면 block을 반만 넣지 않고 통째로
        건너뛴다 -- 표 중간이 잘려서 LLM에 들어가는 걸 막는 게 목적이다."""
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        chunk_size = 300
        # 예산의 상당 부분을 채우는 큰 block 하나(줄 3개, 같은 block_id) 바로
        # 앞에, 그 block을 통째로 넣기엔 부족한 정도의 남은 예산만 두도록 배치.
        filler = ["채움" + "다" * chunk_size for _ in range(2)]
        big_block_lines = ["행1" + "가" * chunk_size, "행2" + "가" * chunk_size, "행3" + "가" * chunk_size]
        texts = [*filler, *big_block_lines, "근거"]
        block_ids = [*range(len(filler)), *(["shared-block"] * len(big_block_lines)), len(filler) + len(big_block_lines)]
        self._write_extracted_with_block_ids(data_root, extracted_root, texts, block_ids)

        anchor_index = len(filler) + len(big_block_lines)
        text = pilot.load_extracted_document_text(
            self._candidate("근거", [anchor_index]), data_root, extracted_root,
            cache=pilot._BoundedDocumentCache(2),
        )

        assert "근거" in text
        # 예산이 3줄짜리 block 전체를 못 담으면, 1~2줄만 포함되는 대신 아예 빠져야 한다.
        included = sum(1 for line in big_block_lines if line in text)
        assert included in (0, len(big_block_lines))

    def test_null_block_ids_still_get_context(self, tmp_path):
        """HWP/HWPX 추출본은 block_id가 전부 None이다. None끼리 같은 block으로
        묶으면 문서 전체가 한 block이 되어 예산 초과로 문맥이 하나도 안 붙는
        버그가 있었다(2026-07-27 EC2 실측에서 창 길이 37자로 발견). 줄 단위로
        취급해 정상적으로 앞뒤 문맥이 붙어야 한다."""
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        texts = ["앞2", "앞1", "근거", "뒤1", "뒤2"]
        self._write_extracted_with_block_ids(
            data_root, extracted_root, texts, [None] * len(texts)
        )

        text = pilot.load_extracted_document_text(
            self._candidate("근거", [2]), data_root, extracted_root,
            cache=pilot._BoundedDocumentCache(2),
        )

        assert "근거" in text
        assert "앞1" in text
        assert "뒤1" in text

    def test_anchor_block_is_included_once_without_duplicate_context(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        texts = ["같은 블록 앞", "근거", "같은 블록 뒤", "다음 블록"]
        self._write_extracted_with_block_ids(
            data_root,
            extracted_root,
            texts,
            ["anchor-block", "anchor-block", "anchor-block", "next-block"],
        )

        text = pilot.load_extracted_document_text(
            self._candidate("근거", [1]),
            data_root,
            extracted_root,
            cache=pilot._BoundedDocumentCache(2),
        )

        assert text.splitlines() == texts
        assert text.count("근거") == 1

    def test_anchor_larger_than_budget_is_kept_without_context(self, tmp_path):
        """anchor(근거 span) 자신이 문맥 예산보다 큰 드문 경우 -- 앞뒤 문맥은
        전혀 안 붙지만 anchor 자체는 보존 원칙에 따라 잘리지 않는다(안전장치
        상한 이내에서)."""
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        huge_anchor = "근거" + "가" * (pilot._CONTEXT_TOTAL_BUDGET_CHARS * 2)
        texts = ["앞", huge_anchor, "뒤"]
        self._write_extracted(data_root, extracted_root, texts)

        text = pilot.load_extracted_document_text(
            self._candidate(huge_anchor, [1]), data_root, extracted_root,
            cache=pilot._BoundedDocumentCache(2),
        )

        assert text.startswith("근거")
        assert "앞" not in text
        assert "뒤" not in text
        assert len(text) <= pilot._ANCHOR_HARD_CEILING_CHARS

    def test_repeated_candidates_load_and_annotate_document_once(self, tmp_path, monkeypatch):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        self._write_extracted(data_root, extracted_root, ["후보 1", "후보 2"])
        reads = 0
        annotations = 0
        source_hashes = 0
        real_read = pilot.read_json_gz
        real_annotate = pilot.annotate_document_in_place
        real_compute_source_sha256 = pilot.compute_source_sha256

        def counted_read(path):
            nonlocal reads
            reads += 1
            time.sleep(0.05)
            return real_read(path)

        def counted_annotate(document):
            nonlocal annotations
            annotations += 1
            return real_annotate(document)

        def counted_compute_source_sha256(path):
            nonlocal source_hashes
            source_hashes += 1
            return real_compute_source_sha256(path)

        monkeypatch.setattr(pilot, "read_json_gz", counted_read)
        monkeypatch.setattr(pilot, "annotate_document_in_place", counted_annotate)
        monkeypatch.setattr(pilot, "compute_source_sha256", counted_compute_source_sha256)
        cache = pilot._BoundedDocumentCache(2)
        candidates = [self._candidate("후보 1", [0]), self._candidate("후보 2", [1])]
        tasks = [
            lambda candidate=candidate: pilot.load_extracted_document_text(
                candidate, data_root, extracted_root, cache=cache,
            )
            for candidate in candidates * 2
        ]

        results = pilot._run_concurrently(tasks, concurrency=4)

        assert len(results) == 4
        assert reads == 1
        assert annotations == 1
        assert source_hashes == 1


class TestGenerateSpanSeededRow:
    def _candidate(self, **overrides) -> dict:
        text = "감사 관련 내부검토"
        defaults = dict(
            source="moel", doc_type="notification", doc_id="1",
            candidate_id="candidate-0", run_id="run-1", extraction_id="extract-1",
            line_ids=[0], text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            page=1, source_path="data/moel/notification/1.pdf",
        )
        defaults.update(overrides)
        return defaults

    def test_skips_when_agency_cannot_be_resolved(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: None)

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), data_root=Path("data"),
            extracted_root=Path("data/extracted"),
        )

        assert row is None

    def test_skips_when_extracted_text_not_found(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        monkeypatch.setattr(
            pilot, "load_extracted_document_text", lambda candidate, data, extracted: None
        )

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), data_root=Path("data"),
            extracted_root=Path("data/extracted"),
        )

        assert row is None

    def test_success_path_builds_expected_row(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        captured_kwargs = {}

        def fake_load_extracted_document_text(candidate, data_root, extracted_root):
            captured_kwargs["candidate"] = candidate
            return "문서 원문 전체 내용"

        monkeypatch.setattr(pilot, "load_extracted_document_text", fake_load_extracted_document_text)

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
            sampling_seed=42, conn=object(), data_root=Path("data"),
            extracted_root=Path("data/extracted"),
        )

        assert row["status"] == "ok"
        assert row["seed_type"] == "span_seeded"
        assert row["seed_candidate_id"] == "candidate-0"
        assert row["seed_candidate_run_id"] == "run-1"
        assert json.loads(row["seed_line_ids"]) == [0]
        assert row["seed_extraction_id"] == "extract-1"
        assert row["clause_no"] == "5"
        assert row["ordering_agency"] == "고용노동부"
        assert row["title"] == "고용노동부 내부감사 계획"
        assert "[문서 근거]" not in row["title"]
        assert row["body_text"] == "고용노동부 감사 관련 문서 본문"
        assert row["matched_span_text"] == "감사 관련 내부검토"
        assert row["doc_type"] == "audit_result"
        assert row["cso_subclause_key"] == "audit_inspection"
        assert row["is_synthetic"] is True
        assert set(row.keys()) == set(pilot.CSV_FIELDNAMES) - {"template_id", "template_violations"}
        assert captured_kwargs["candidate"]["candidate_id"] == "candidate-0"

    def test_uses_real_source_filename_as_title(self, monkeypatch):
        monkeypatch.setattr(
            pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부"
        )
        monkeypatch.setattr(
            pilot,
            "load_extracted_document_text",
            lambda candidate, data, extracted: "원문 전체 내용",
        )
        monkeypatch.setattr(
            pilot,
            "generate_span_seeded_body",
            lambda seed, *, client, model: SeededResult(
                department="고용정책실",
                unit_task="장려금 지급 규정 개정",
                production_date="2025-03-01",
                body_text="규정 개정 검토 본문",
                tokens_in=10,
                tokens_out=10,
            ),
        )
        candidate = self._candidate(
            source_path=(
                "data/moel/notification/"
                "20220700805_고용창출장려금 고용안정장려금의 신청 및 지급에 관한 규정 일부개정(안).hwp"
            )
        )

        row = pilot.generate_span_seeded_row(
            "5-span-0",
            "5",
            candidate,
            client=object(),
            model="gpt-4o-mini",
            sampling_seed=42,
            conn=object(),
            data_root=Path("data"),
            extracted_root=Path("data/extracted"),
        )

        assert row["title"] == "고용창출장려금 고용안정장려금의 신청 및 지급에 관한 규정 일부개정(안)"

    def test_llm_failure_produces_llm_error_status(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        monkeypatch.setattr(
            pilot, "load_extracted_document_text", lambda candidate, data, extracted: "문서 원문"
        )

        def fake_generate_span_seeded_body(seed, *, client, model):
            raise RuntimeError("simulated API failure")

        monkeypatch.setattr(pilot, "generate_span_seeded_body", fake_generate_span_seeded_body)

        row = pilot.generate_span_seeded_row(
            "5-span-0", "5", self._candidate(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), data_root=Path("data"),
            extracted_root=Path("data/extracted"),
        )

        assert row["status"] == "llm_error"
        assert "simulated API failure" in row["body_text"]


class TestClause6SpanPiiEvidence:
    def test_adds_deterministic_pii_evidence(self, monkeypatch):
        candidate_text = "성명 주민등록번호 연락처"
        candidate = {
            "source": "moel",
            "doc_type": "notification",
            "doc_id": "1",
            "candidate_id": "candidate-6",
            "run_id": "run-1",
            "extraction_id": "extract-1",
            "line_ids": [0],
            "text": candidate_text,
            "text_sha256": hashlib.sha256(candidate_text.encode("utf-8")).hexdigest(),
            "page": 1,
            "source_path": "data/moel/notification/1.pdf",
        }
        monkeypatch.setattr(
            pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부"
        )
        monkeypatch.setattr(
            pilot,
            "load_extracted_document_text",
            lambda candidate, data, extracted: "개인정보 신청서 원문",
        )
        monkeypatch.setattr(
            pilot,
            "generate_span_seeded_body",
            lambda seed, *, client, model: SeededResult(
                department="복지과",
                unit_task="개인정보 신청",
                production_date="2025-03-01",
                body_text="신청인 성명: 홍길동",
                tokens_in=10,
                tokens_out=10,
            ),
        )

        row = pilot.generate_span_seeded_row(
            "6-span-0",
            "6",
            candidate,
            client=object(),
            model="gpt-4o-mini",
            sampling_seed=42,
            conn=object(),
            data_root=Path("data"),
            extracted_root=Path("data/extracted"),
        )

        assert pilot.validate_clause6_pii_evidence(row["body_text"], None) == []


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
        assert row["seed_candidate_id"] == ""
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

    def test_military_secret_content_notice_forwarded_to_row(self, monkeypatch):
        def fake_generate_clause_document(
            clause_no, *, ordering_agency, production_date, client, model, **kwargs
        ):
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
            ordering_agency="외교부", production_date="2026-01-01",
            agency_source="whitelist_synthetic", scenario_index=4,
            military_secret_content_notice=True,
        )

        assert row["military_secret_content_notice"] == "true"

    def test_military_secret_content_notice_defaults_to_empty_string(self, monkeypatch):
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

        assert row["military_secret_content_notice"] == ""

    def test_reclassification_forwarded_to_row_as_json(self, monkeypatch):
        def fake_generate_clause_document(
            clause_no, *, ordering_agency, production_date, client, model, **kwargs
        ):
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

        reclass = {
            "old_grade": "1급", "basis_text": "군사기밀 보호법 시행령 제7조",
            "reclass_date": "2026-03-15", "position": "보안담당관", "rank": "대령", "name": "김도현",
        }
        row = pilot.generate_fallback_row(
            "2-fallback-0", "2", client=object(), model="gpt-4o-mini", sampling_seed=42,
            ordering_agency="국방부", production_date="2026-01-01",
            agency_source="whitelist_synthetic", military_secret_grade="2급",
            reclassification=reclass,
        )

        assert json.loads(row["reclassification_json"]) == reclass

    def test_reclassification_defaults_to_empty_string(self, monkeypatch):
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

        assert row["reclassification_json"] == ""


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


def test_only_clauses_1_to_4_use_whitelist_agency_fallback():
    assert all(
        pilot._uses_whitelist_agency_fallback(clause_no)
        for clause_no in ("1", "2", "3", "4")
    )
    assert all(
        not pilot._uses_whitelist_agency_fallback(clause_no)
        for clause_no in ("5", "6", "7", "8")
    )


def test_deprecated_agency_logo_column_preserves_resume_csv_alignment(tmp_path):
    adjacent_columns = (
        "military_secret_grade",
        "agency_logo_filename",
        "military_secret_content_notice",
        "reclassification_json",
    )
    start = pilot.CSV_FIELDNAMES.index("military_secret_grade")
    assert tuple(pilot.CSV_FIELDNAMES[start : start + 4]) == adjacent_columns

    csv_path = tmp_path / "legacy-resume.csv"
    row = {name: "" for name in pilot.CSV_FIELDNAMES}
    row.update(
        {
            "row_id": "2-fallback-legacy",
            "military_secret_grade": "2급",
            "military_secret_content_notice": "true",
            "reclassification_json": '{"old_grade":"1급"}',
        }
    )
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow(row)

    with csv_path.open(encoding="utf-8", newline="") as f:
        resumed = next(csv.DictReader(f))

    assert resumed["agency_logo_filename"] == ""
    assert resumed["military_secret_content_notice"] == "true"
    assert resumed["reclassification_json"] == '{"old_grade":"1급"}'


def test_atomic_pdf_publish_does_not_leave_uncovered_military_pdf(
    tmp_path,
    monkeypatch,
):
    output_path = tmp_path / "military.pdf"

    def fake_render(_row, _category, staged_path, **_kwargs):
        staged_path.write_bytes(b"%PDF-uncovered")

    def fail_cover(_path, _grade):
        raise RuntimeError("cover failed")

    monkeypatch.setattr(pilot, "render_document_pdf", fake_render)
    monkeypatch.setattr(pilot, "prepend_military_secret_cover", fail_cover)

    with pytest.raises(RuntimeError, match="cover failed"):
        pilot._render_pdf_atomically(
            {},
            "central_government",
            output_path,
            military_secret_grade="1급",
        )

    assert not output_path.exists()
    assert not list(tmp_path.glob(".*.staged.pdf"))


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

        # pre_mark_ratio=0.0: 이 테스트는 마크없는 변형(별도 기능, DEFAULT_PRE_MARK_RATIO=1.0)이
        # 아니라 기본 렌더링·워터마크 캐시 동작만 검증한다.
        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, pdf_dir, sampling_seed=42, pre_mark_ratio=0.0
        )

        assert rendered == 2
        assert inserted == 0  # store를 안 넘겼으니 RDS 삽입 없음
        assert dup_skipped == 0
        assert rds_errors == 0
        # 2026-07-21 사용자 결정: PDF 파일명은 row_id가 아니라 문서 제목(title)
        # 기반이어야 한다 — row_id는 여전히 CSV 컬럼으로만 남는다.
        assert (pdf_dir / "테스트_문서.pdf").exists()  # S — 마크 없음
        assert (pdf_dir / "합성_폴백_문서.pdf").exists()  # C — 분류표시 적용
        assert (pdf_dir / "_stamp_confidential.png").exists()
        assert not list(pdf_dir.glob("_watermark_*.png"))
        assert not list(pdf_dir.glob("_letterhead_*.png"))

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

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, tmp_path / "pdfs", sampling_seed=42
        )

        assert rendered == 0
        assert inserted == 0
        assert dup_skipped == 0
        assert rds_errors == 0
        assert not (tmp_path / "pdfs" / "5-span-0.pdf").exists()

    def test_missing_csv_raises_clear_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="CSV 파일이 없습니다"):
            pilot.render_pdfs_for_csv(tmp_path / "does_not_exist.csv", tmp_path / "pdfs", sampling_seed=42)


class _FakeDocumentStore:
    """render_pdfs_for_csv(store=...) 테스트용 — 실제 RDS 연결 없이 upsert 호출만 기록한다."""

    def __init__(self, dup_source_urls: set | None = None):
        self.upserted: list = []
        self._dup_source_urls = dup_source_urls or set()

    def upsert(self, doc) -> bool:
        if doc.source_url in self._dup_source_urls:
            return False
        self.upserted.append(doc)
        return True


class TestRenderPdfsForCsvCommitsToRds:
    """2026-07-21: render_pdfs_for_csv(store=...)가 렌더링 시점에 바로 RDS에
    upsert하는 경로(별도 반영 스크립트 없이 --commit-to-rds로 통합)."""

    def _write_csv(self, csv_path, rows):
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

    def _row(self, **overrides):
        row = {name: "" for name in pilot.CSV_FIELDNAMES}
        row.update(
            {
                "row_id": "5-span-0",
                "clause_no": "5",
                "cso_classification": "S",
                "title": "테스트 문서",
                "ordering_agency": "경상북도",
                "body_text": "본문 내용입니다.",
                "disclosure_status": "비공개",
                "non_disclosure_reason": "제5호 — 감사·감독·검사",
                "source": "synthetic-llm",
                "status": "ok",
            }
        )
        row.update(overrides)
        return row

    def test_inserts_ok_rows_with_deterministic_source_url(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row()])
        store = _FakeDocumentStore()

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, tmp_path / "pdfs", sampling_seed=42, store=store, repo_root=tmp_path
        )

        assert rendered == 1
        assert inserted == 1
        assert dup_skipped == 0
        assert rds_errors == 0
        assert len(store.upserted) == 1
        doc = store.upserted[0]
        assert doc.source_url == "synthetic://cs-pilot/5-span-0"
        assert doc.body_file_path == str((tmp_path / "pdfs" / "테스트_문서.pdf").relative_to(tmp_path))

    def test_skips_non_ok_rows(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row(status="empty_body", body_text="")])
        store = _FakeDocumentStore()

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, tmp_path / "pdfs", sampling_seed=42, store=store, repo_root=tmp_path
        )

        assert rendered == 1  # PDF 렌더링 자체는 status와 무관(기존 동작 유지)
        assert inserted == 0
        assert store.upserted == []

    def test_duplicate_row_is_skipped_not_reinserted(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row()])
        store = _FakeDocumentStore(dup_source_urls={"synthetic://cs-pilot/5-span-0"})

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, tmp_path / "pdfs", sampling_seed=42, store=store, repo_root=tmp_path
        )

        assert inserted == 0
        assert dup_skipped == 1
        assert rds_errors == 0

    def test_malformed_row_records_error_without_aborting_batch(self, tmp_path):
        """disclosure_status가 비어있으면 Document 생성이 실패한다(잘못된 enum
        값) — 이 행은 rds_errors로 세고, 다음 행 렌더링/삽입은 계속돼야 한다."""
        csv_path = tmp_path / "cs_pilot_output.csv"
        rows = [
            self._row(row_id="5-span-0", disclosure_status=""),
            self._row(row_id="5-span-1"),
        ]
        self._write_csv(csv_path, rows)
        store = _FakeDocumentStore()

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, tmp_path / "pdfs", sampling_seed=42, store=store, repo_root=tmp_path
        )

        assert rendered == 2  # 두 행 다 PDF는 렌더링됨
        assert rds_errors == 1
        assert inserted == 1
        assert len(store.upserted) == 1

    def test_no_store_means_no_rds_calls(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row()])

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, tmp_path / "pdfs", sampling_seed=42
        )

        assert rendered == 1
        assert inserted == 0
        assert dup_skipped == 0
        assert rds_errors == 0


class TestSelectPreMarkVariant:
    def test_ratio_zero_never_selects(self):
        assert pilot._select_pre_mark_variant("5-span-0", 42, 0.0) is False

    def test_ratio_one_always_selects(self):
        assert pilot._select_pre_mark_variant("5-span-0", 42, 1.0) is True

    def test_deterministic_for_same_row_id_and_seed(self):
        results = {pilot._select_pre_mark_variant("1-fallback-3", 42, 0.5) for _ in range(20)}
        assert len(results) == 1


class TestRenderPdfsForCsvPreMarkVariant:
    """2026-07-22: C 행 일부를 마크 없이 한 번 더 렌더링하는 --pre-mark-ratio.
    분류기가 본문 대신 대외비/군사기밀 마크 픽셀만 보고 C/S를 구분하지 않도록,
    실제 배포 대상엔 마크가 아직 안 찍힌 문서도 섞여 있다는 전제로 도입됨."""

    def _row(self, **overrides):
        row = {name: "" for name in pilot.CSV_FIELDNAMES}
        row.update(
            {
                "row_id": "1-fallback-0",
                "clause_no": "1",
                "cso_classification": "C",
                "title": "합성 폴백 문서",
                "ordering_agency": "실제기관명",
                "body_text": "폴백 본문입니다.",
                "disclosure_status": "비공개",
                "non_disclosure_reason": "제1호 — 법률상 비밀",
                "source": "synthetic-llm",
                "status": "ok",
            }
        )
        row.update(overrides)
        return row

    def _write_csv(self, csv_path, rows):
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=pilot.CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

    def test_ratio_zero_explicitly_disables_variant(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row()])
        pdf_dir = tmp_path / "pdfs"

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, pdf_dir, sampling_seed=42, pre_mark_ratio=0.0
        )

        assert rendered == 1
        assert not any("_2" in p.name for p in pdf_dir.glob("*.pdf"))

    def test_default_ratio_is_one_and_renders_variant_for_c_row(self, tmp_path):
        """DEFAULT_PRE_MARK_RATIO=1.0 — pre_mark_ratio를 안 주면 C행마다
        마크있음:마크없음 50:50이 기본 동작이다(2026-07-22 결정)."""
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row()])
        pdf_dir = tmp_path / "pdfs"

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, pdf_dir, sampling_seed=42
        )

        assert rendered == 2

    def test_ratio_one_renders_additional_unmarked_variant_for_c_row(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row()])
        pdf_dir = tmp_path / "pdfs"

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, pdf_dir, sampling_seed=42, pre_mark_ratio=1.0
        )

        assert rendered == 2
        pdfs = sorted(p.name for p in pdf_dir.glob("*.pdf"))
        assert pdfs == ["합성_폴백_문서.pdf", "합성_폴백_문서_2.pdf"]

    def test_ratio_one_does_not_affect_s_row(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(
            csv_path,
            [
                self._row(
                    row_id="5-span-0", clause_no="5", cso_classification="S",
                    non_disclosure_reason="제5호 — 감사·감독·검사",
                )
            ],
        )
        pdf_dir = tmp_path / "pdfs"

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, pdf_dir, sampling_seed=42, pre_mark_ratio=1.0
        )

        assert rendered == 1

    def test_variant_inserted_to_rds_with_distinct_source_url(self, tmp_path):
        csv_path = tmp_path / "cs_pilot_output.csv"
        self._write_csv(csv_path, [self._row()])
        store = _FakeDocumentStore()

        rendered, inserted, dup_skipped, rds_errors = pilot.render_pdfs_for_csv(
            csv_path, tmp_path / "pdfs", sampling_seed=42, store=store,
            repo_root=tmp_path, pre_mark_ratio=1.0,
        )

        assert rendered == 2
        assert inserted == 2
        assert rds_errors == 0
        source_urls = sorted(doc.source_url for doc in store.upserted)
        assert source_urls == [
            "synthetic://cs-pilot/1-fallback-0",
            "synthetic://cs-pilot/1-fallback-0-premark",
        ]
        # 본문 내용은 마크 유무와 무관하게 동일해야 한다 — 변형이 만드는 건
        # 시각적 마크 차이뿐, 라벨/본문은 그대로.
        bodies = {doc.body_text for doc in store.upserted}
        assert bodies == {"폴백 본문입니다."}
        classifications = {doc.cso_classification.value for doc in store.upserted}
        assert classifications == {"C"}


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


class TestBuildTargetCells:
    """--target-matrix 모드의 4축(조항,세부조항,문서유형,행정상태) 셀 생성.

    Lane B(template_matrix.py 상태축 추가, candidates.py 공개 이름 변경)가 아직
    이 브랜치에 들어오지 않은 상태를 전제로 한다 — build_target_cells()는
    template_matrix.TARGET_BY_KEY(3축, 기존 구조 그대로)와 candidates.py의
    문서유형별 행정상태 규칙(현재는 private 이름)을 조합해서 만든다.
    """

    def test_only_includes_clauses_5_to_8(self):
        cells = pilot.build_target_cells()
        assert cells  # 최소 1개 이상 생성돼야 함
        assert {cell.clause_no for cell in cells} <= {"5", "6", "7", "8"}

    def test_cell_count_matches_doc_type_conditional_status_expansion(self):
        """설계 문서 "조합 규모" 절: 완전 격자가 아니라 문서유형-조건부 구조다 —
        (조항,세부조항,문서유형) 삼중쌍마다 그 문서유형에 등록된 상태 수만큼만
        늘어나야 하고, 상태가 없으면 정확히 1개(빈 상태)여야 한다."""
        rules_by_doc_type = pilot._load_admin_status_rules_by_doc_type()
        expected = 0
        for (clause_no, _subclause_key, doc_type), _target in TARGET_BY_KEY.items():
            if clause_no not in ("5", "6", "7", "8"):
                continue
            n_statuses = len(rules_by_doc_type.get(doc_type, ()))
            expected += n_statuses if n_statuses else 1

        cells = pilot.build_target_cells()
        assert len(cells) == expected

    def test_every_cell_key_is_unique(self):
        cells = pilot.build_target_cells()
        keys = [cell.cell_key for cell in cells]
        assert len(keys) == len(set(keys))

    def test_every_cell_maps_back_to_a_real_target_by_key_entry(self):
        cells = pilot.build_target_cells()
        for cell in cells:
            assert (cell.clause_no, cell.subclause_key, cell.doc_type) in TARGET_BY_KEY

    def test_falls_back_to_single_status_less_cell_when_doc_type_has_no_rule(self, monkeypatch):
        """행정상태 규칙이 아예 없는 문서유형(no_rule_defined)이라도 그
        (조항,세부조항,문서유형) 삼중쌍 자체는 매트릭스에서 빠지면 안 된다 —
        상태 축 없는 셀 1개로 남아야 한다."""
        monkeypatch.setattr(pilot, "_load_admin_status_rules_by_doc_type", lambda: {})

        cells = pilot.build_target_cells()

        expected_triples = {
            (clause_no, subclause_key, doc_type)
            for (clause_no, subclause_key, doc_type) in TARGET_BY_KEY
            if clause_no in ("5", "6", "7", "8")
        }
        actual_triples = {(c.clause_no, c.subclause_key, c.doc_type) for c in cells}
        assert actual_triples == expected_triples
        assert len(cells) == len(expected_triples)  # 삼중쌍당 정확히 1개
        assert all(cell.admin_status == "" for cell in cells)

    def test_respects_clause_nos_argument(self):
        cells = pilot.build_target_cells(("5",))
        assert {cell.clause_no for cell in cells} == {"5"}


class TestPlanCell:
    def _cell(self) -> pilot.TargetCell:
        return pilot.TargetCell("5", "audit_inspection", "audit_result", "감사진행중", "T5-2")

    def test_zero_real_candidates_lowers_effective_target(self):
        plan = pilot.plan_cell(
            self._cell(), [],
            per_cell_target=2000, zero_candidate_target=50,
            force_full_target_for_zero_cells=False,
        )
        assert plan.effective_target == 50
        assert plan.is_zero_candidate_exception is True

    def test_nonzero_real_candidates_uses_full_per_cell_target(self):
        plan = pilot.plan_cell(
            self._cell(), [{"candidate_id": "c1"}],
            per_cell_target=2000, zero_candidate_target=50,
            force_full_target_for_zero_cells=False,
        )
        assert plan.effective_target == 2000
        assert plan.is_zero_candidate_exception is False

    def test_force_full_target_overrides_reduction_but_keeps_exception_flag(self):
        plan = pilot.plan_cell(
            self._cell(), [],
            per_cell_target=2000, zero_candidate_target=50,
            force_full_target_for_zero_cells=True,
        )
        assert plan.effective_target == 2000
        assert plan.is_zero_candidate_exception is True  # 예외 사실 자체는 계속 표시됨


class TestBucketSpanCandidatesBySubclauseDocType:
    def test_buckets_by_inferred_subclause_and_doc_type(self):
        candidates = [
            {"text": "감사 관련 내부검토"},  # audit_inspection 계열 키워드
            {"text": "입찰 계약 관련 낙찰"},  # bid_contract 계열 키워드
        ]
        buckets = pilot._bucket_span_candidates_by_subclause_doc_type(candidates, "5")
        assert sum(len(v) for v in buckets.values()) == 2

    def test_empty_input_returns_empty_dict(self):
        assert pilot._bucket_span_candidates_by_subclause_doc_type([], "5") == {}


class TestRunConcurrently:
    def test_concurrency_one_runs_sequentially_and_preserves_order(self):
        calls = []

        def make_task(i):
            def _task():
                calls.append(i)
                return i
            return _task

        tasks = [make_task(i) for i in range(5)]
        results = pilot._run_concurrently(tasks, concurrency=1)
        assert results == [0, 1, 2, 3, 4]
        assert calls == [0, 1, 2, 3, 4]

    def test_concurrency_above_one_preserves_submission_order(self):
        import time as time_module

        def make_task(i):
            def _task():
                time_module.sleep(0.01 * (5 - i))  # 역순으로 끝나도 결과 순서는 그대로여야 함
                return i
            return _task

        tasks = [make_task(i) for i in range(5)]
        results = pilot._run_concurrently(tasks, concurrency=4)
        assert results == [0, 1, 2, 3, 4]

    def test_empty_task_list_returns_empty_list(self):
        assert pilot._run_concurrently([], concurrency=4) == []

    def test_retry_backoff_still_triggers_under_concurrent_execution(self, monkeypatch):
        """동시성을 켜도 각 태스크 내부의 _with_retry(재시도/지수 백오프)가
        무시되거나 우회되지 않아야 한다 — 여러 스레드가 동시에 rate limit을
        맞아도 각자 재시도해서 결국 성공해야 한다."""
        monkeypatch.setattr(pilot.time, "sleep", lambda seconds: None)  # 백오프 대기 스킵

        call_counts: dict[str, int] = {}
        lock = threading.Lock()

        def flaky_generate_clause_document(clause_no, *, ordering_agency, production_date, client, model, **kwargs):
            with lock:
                call_counts[ordering_agency] = call_counts.get(ordering_agency, 0) + 1
                attempt = call_counts[ordering_agency]
            if attempt == 1:
                raise _make_rate_limit_error()
            return Document(
                title="[합성] 테스트", ordering_agency=ordering_agency, production_date=production_date,
                disclosure_status=DisclosureStatus.CLOSED, non_disclosure_reason="제1호",
                subject_category="법률상 비밀·비공개 규정", body_text="본문",
                cso_classification=CsoClassification.C, cso_sub_clause=clause_no,
                source="synthetic-llm", source_url=None, doc_type="synthetic_document",
                is_synthetic=True,
            )

        monkeypatch.setattr(pilot, "generate_clause_document", flaky_generate_clause_document)

        tasks = [
            (lambda i=i: pilot.generate_fallback_row(
                f"row-{i}", "1", client=object(), model="gpt-4o-mini", sampling_seed=1,
                ordering_agency=f"기관{i}", production_date="2025-01-01",
            ))
            for i in range(8)
        ]

        results = pilot._run_concurrently(tasks, concurrency=4)

        assert len(results) == 8
        assert all(row["status"] == "ok" for row in results)
        # 모든 기관이 정확히 2번(1회 실패 + 1회 재시도 성공) 호출됐어야 함 —
        # 동시 실행 중에도 재시도 로직이 각 태스크별로 정상 작동했다는 뜻.
        assert all(count == 2 for count in call_counts.values())
        assert len(call_counts) == 8


class TestApplyCellMetadata:
    def test_stamps_cell_key_and_ratio_and_exception_flag(self):
        row = {name: "" for name in pilot.CSV_FIELDNAMES}
        cell = pilot.TargetCell("5", "audit_inspection", "audit_result", "감사진행중", "T5-2")

        pilot._apply_cell_metadata(row, cell, fallback_ratio=1.0, is_zero_candidate_exception=True)

        assert row["cell_key"] == "5|audit_inspection|audit_result|감사진행중"
        assert row["cell_fallback_ratio"] == "1.0000"
        assert row["cell_zero_candidate_exception"] == "true"

    def test_non_exception_cell_is_flagged_false(self):
        row = {name: "" for name in pilot.CSV_FIELDNAMES}
        cell = pilot.TargetCell("5", "audit_inspection", "audit_result", "감사진행중", "T5-2")

        pilot._apply_cell_metadata(row, cell, fallback_ratio=0.25, is_zero_candidate_exception=False)

        assert row["cell_fallback_ratio"] == "0.2500"
        assert row["cell_zero_candidate_exception"] == "false"


class TestGenerateCellRows:
    def _stub_fallback_row(self, row_id, clause_no, **kwargs):
        row = {name: "" for name in pilot.CSV_FIELDNAMES}
        row.update({
            "row_id": row_id, "status": "ok", "clause_no": clause_no,
            "doc_type": "audit_result", "seed_type": "synthetic_fallback",
            "ordering_agency": kwargs.get("ordering_agency", ""),
        })
        return row

    def test_zero_candidate_cell_is_100_percent_fallback_and_flagged(self, monkeypatch):
        monkeypatch.setattr(pilot, "generate_fallback_row", self._stub_fallback_row)

        cell = pilot.TargetCell("5", "audit_inspection", "audit_result", "감사진행중", "T5-2")
        cell_plan = pilot.plan_cell(
            cell, [],  # 실측 후보 0건 — 영구 0건 셀 예외
            per_cell_target=2000, zero_candidate_target=5,
            force_full_target_for_zero_cells=False,
        )

        rows, report_row = pilot.generate_cell_rows(
            cell_plan, resumed_row_ids=set(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), data_root=Path("data"),
            extracted_root=Path("data/extracted"),
            rng=random.Random(1), fallback_samples=[("고용노동부", "2025-01-01")],
            concurrency=2, db_lock=threading.Lock(),
        )

        assert len(rows) == 5  # zero_candidate_target만큼만(2000 아님)
        assert all(row["seed_type"] == "synthetic_fallback" for row in rows)
        assert all(row["cell_zero_candidate_exception"] == "true" for row in rows)
        assert all(row["cell_fallback_ratio"] == "1.0000" for row in rows)
        assert all(row["cell_key"] == cell.cell_key for row in rows)
        assert report_row["real_candidates"] == 0
        assert report_row["effective_target"] == 5
        assert report_row["produced_span_seeded"] == 0
        assert report_row["produced_fallback"] == 5
        assert report_row["zero_candidate_exception"] == "true"

    def test_force_full_target_generates_full_count_via_fallback(self, monkeypatch):
        monkeypatch.setattr(pilot, "generate_fallback_row", self._stub_fallback_row)

        cell = pilot.TargetCell("5", "audit_inspection", "audit_result", "감사진행중", "T5-2")
        cell_plan = pilot.plan_cell(
            cell, [],
            per_cell_target=12, zero_candidate_target=2,
            force_full_target_for_zero_cells=True,
        )

        rows, report_row = pilot.generate_cell_rows(
            cell_plan, resumed_row_ids=set(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), data_root=Path("data"),
            extracted_root=Path("data/extracted"),
            rng=random.Random(1), fallback_samples=[("고용노동부", "2025-01-01")],
            concurrency=2, db_lock=threading.Lock(),
        )

        assert len(rows) == 12
        assert report_row["zero_candidate_exception"] == "true"  # 여전히 표시됨

    def test_mixes_span_seeded_and_fallback_and_computes_partial_ratio(self, monkeypatch):
        monkeypatch.setattr(pilot, "resolve_agency_for_candidate", lambda candidate, conn: "고용노동부")
        monkeypatch.setattr(
            pilot, "load_extracted_document_text", lambda candidate, data, extracted: "문서 원문"
        )

        def fake_generate_span_seeded_body(seed, *, client, model):
            return SeededResult(
                department="감사담당관실", unit_task="내부감사", production_date="2025-03-01",
                body_text="본문", tokens_in=10, tokens_out=10,
            )

        monkeypatch.setattr(pilot, "generate_span_seeded_body", fake_generate_span_seeded_body)
        monkeypatch.setattr(pilot, "generate_fallback_row", self._stub_fallback_row)

        cell = pilot.TargetCell("5", "audit_inspection", "audit_result", "감사진행중", "T5-2")
        real_candidates = [
            {"source": "moel", "doc_type": "notification", "doc_id": "1",
             "candidate_id": "c1", "run_id": "run-1", "extraction_id": "extract-1",
             "line_ids": [0], "page": 1, "text_sha256": "hash",
             "text": "감사 관련 내부검토", "source_path": "data/moel/notification/1.pdf"},
        ]
        cell_plan = pilot.plan_cell(
            cell, real_candidates,
            per_cell_target=4, zero_candidate_target=50,
            force_full_target_for_zero_cells=False,
        )

        rows, report_row = pilot.generate_cell_rows(
            cell_plan, resumed_row_ids=set(), client=object(), model="gpt-4o-mini",
            sampling_seed=42, conn=object(), data_root=Path("data"),
            extracted_root=Path("data/extracted"),
            rng=random.Random(1), fallback_samples=[("고용노동부", "2025-01-01")],
            concurrency=2, db_lock=threading.Lock(),
        )

        assert report_row["real_candidates"] == 1
        assert report_row["effective_target"] == 4
        assert report_row["produced_span_seeded"] == 1
        assert report_row["produced_fallback"] == 3
        assert report_row["zero_candidate_exception"] == "false"
        assert report_row["fallback_ratio"] == "0.7500"
        assert len(rows) == 4
        span_rows = [r for r in rows if r["seed_type"] == "span_seeded"]
        assert len(span_rows) == 1
        assert span_rows[0]["cell_fallback_ratio"] == "0.7500"
        assert span_rows[0]["cell_zero_candidate_exception"] == "false"


class TestWriteCellReport:
    def test_writes_expected_header_and_rows(self, tmp_path):
        report_path = tmp_path / "cell_report.csv"
        cell_reports = [
            {
                "cell_key": "5|audit_inspection|audit_result|감사진행중",
                "clause_no": "5", "subclause_key": "audit_inspection", "doc_type": "audit_result",
                "admin_status": "감사진행중", "template_id": "T5-2", "real_candidates": 0,
                "effective_target": 50, "produced_span_seeded": 0, "produced_fallback": 50,
                "fallback_ratio": "1.0000", "zero_candidate_exception": "true",
            }
        ]

        pilot.write_cell_report(report_path, cell_reports)

        with report_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["cell_key"] == "5|audit_inspection|audit_result|감사진행중"
        assert rows[0]["zero_candidate_exception"] == "true"
