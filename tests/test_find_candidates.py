import json
import sys
from collections import Counter
from pathlib import Path

import pytest

import find_candidates as fc  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _clause_span(*, source="test_src", doc_type, doc_id, text, extraction_id=None):
    extraction_id = extraction_id or f"{source}:{doc_type}:{doc_id}"
    return {
        "candidate_id": f"candidate:{extraction_id}:5",
        "extraction_id": extraction_id,
        "source_path": f"data/{source}/{doc_type}/{doc_id}.pdf",
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "line_ids": [1],
        "text": text,
        "text_sha256": "sha",
        "page": 1,
        "candidate_kind": "clause",
        "clause": "5",
    }


def _admin_span(*, source="test_src", doc_type, doc_id, document_status, extraction_id=None):
    extraction_id = extraction_id or f"{source}:{doc_type}:{doc_id}"
    return {
        "candidate_id": f"candidate:{extraction_id}:{document_status}",
        "extraction_id": extraction_id,
        "source_path": f"data/{source}/{doc_type}/{doc_id}.pdf",
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "line_ids": [2],
        "text": "행정상태 근거",
        "text_sha256": "sha",
        "page": 1,
        "document_status": document_status,
        "candidate_kind": "administrative_status",
    }


class TestDocKey:
    def test_uses_extraction_id(self):
        record = {
            "extraction_id": "extract-42",
            "source_path": "data/moel/report/42.pdf",
            "source": "moel",
            "doc_id": "42",
        }
        assert fc._doc_key(record) == ("extract-42", "data/moel/report/42.pdf")

    def test_rejects_missing_extraction_id(self):
        try:
            fc._doc_key({"source": "moel", "doc_id": "42"})
        except ValueError as exc:
            assert "extraction_id" in str(exc)
        else:
            raise AssertionError("missing extraction_id must not be silently correlated")


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


def _v2_document(*, extraction_id: str = "extract-1", status: str = "ok") -> dict:
    return {
        "schema_version": 2,
        "extraction_id": extraction_id,
        "source_sha256": "source-sha",
        "source_path": f"data/moel/notification/{extraction_id}.pdf",
        "source": "moel",
        "doc_type": "notification",
        "doc_id": extraction_id,
        "source_format": "pdf",
        "status": status,
        "error": None,
        "pages": [
            {
                "page": 1,
                "width_pt": 595.0,
                "height_pt": 842.0,
                "rotation": 0,
                "lines": [
                    {
                        "line_id": 0,
                        "block_id": 0,
                        "order": 0,
                        "text": "입찰 계약 검토안",
                        "bbox_pt": [10.0, 10.0, 200.0, 20.0],
                        "style_runs": [],
                    }
                ],
            }
        ],
    }


def _artifact_entry(
    output_path: str = "sample.pdf.json.gz",
    *,
    extraction_id: str = "extract-1",
    status: str = "ok",
    source_path: str | None = None,
) -> dict:
    return {
        "source_path": source_path or f"data/moel/notification/{extraction_id}.pdf",
        "output_path": output_path,
        "extraction_id": extraction_id,
        "status": status,
    }


def _upstream_manifest(
    *artifacts: dict,
    status: str = "complete",
    run_id: str = "extraction-run-1",
) -> dict:
    return {
        "run_id": run_id,
        "status": status,
        "artifacts": list(artifacts),
    }


class TestCandidateScan:
    def test_global_publish_rejects_subset_extraction_manifest(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        candidates_root = data_root / "candidates"
        extracted_root.mkdir(parents=True)
        candidates_root.mkdir(parents=True)
        (data_root / "moe" / "notification").mkdir(parents=True)
        (data_root / "molit" / "notification").mkdir(parents=True)
        (data_root / "moe" / "notification" / "one.pdf").write_bytes(b"one")
        (data_root / "molit" / "notification" / "two.hwp").write_bytes(b"two")
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        existing_candidates = candidates_root / "clause_5.jsonl"
        existing_candidates.write_text("keep-existing-run\n", encoding="utf-8")
        existing_manifest = candidates_root / "_manifest.json"
        existing_manifest.write_bytes(b"keep-existing-manifest")

        subset_manifest = _upstream_manifest(
            _artifact_entry(
                source_path="data/moe/notification/one.pdf",
                output_path="moe/notification/one.pdf.json.gz",
            )
        )

        with pytest.raises(RuntimeError, match="not full-corpus"):
            fc.run_candidate_scan(
                extracted_root=extracted_root,
                candidates_root=candidates_root,
                reader=lambda path: subset_manifest,
                run_id="unsafe-subset",
                allow_partial=True,
                require_full_corpus=True,
            )

        assert existing_candidates.read_text(encoding="utf-8") == "keep-existing-run\n"
        assert existing_manifest.read_bytes() == b"keep-existing-manifest"
        assert not list(candidates_root.glob("*.tmp"))

    def test_full_scope_partial_manifest_counts_failures_as_covered_sources(self, tmp_path):
        data_root = tmp_path / "data"
        extracted_root = data_root / "extracted"
        candidates_root = data_root / "candidates"
        extracted_root.mkdir(parents=True)
        source_one = data_root / "moel" / "notification" / "extract-1.pdf"
        source_two = data_root / "molit" / "notification" / "two.hwp"
        source_one.parent.mkdir(parents=True)
        source_two.parent.mkdir(parents=True)
        source_one.write_bytes(b"one")
        source_two.write_bytes(b"two")
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        artifact = extracted_root / "sample.pdf.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        artifact.write_bytes(b"document")
        full_scope_partial = _upstream_manifest(
            _artifact_entry(),
            status="partial",
        )
        full_scope_partial["failures"] = [
            {"source_path": "data/molit/notification/two.hwp", "status": "error"}
        ]

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=lambda path: (
                full_scope_partial if path == upstream_manifest else _v2_document()
            ),
            run_id="full-scope-partial",
            allow_partial=True,
            require_full_corpus=True,
        )

        assert manifest["status"] == "partial"
        assert manifest["counts"]["documents_succeeded"] == 1

    def test_loads_annotates_and_traverses_each_document_once(self, tmp_path, monkeypatch):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        artifact = extracted_root / "sample.pdf.json.gz"
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        artifact.write_bytes(b"placeholder")
        upstream_manifest.write_bytes(b"manifest")

        calls = Counter()
        real_annotate = fc.annotate_document_in_place
        real_find_all = fc.find_all_candidates

        def reader(path):
            if path == upstream_manifest:
                return _upstream_manifest(_artifact_entry())
            calls["read"] += 1
            assert path == artifact
            return _v2_document()

        def annotate(document):
            calls["annotate"] += 1
            return real_annotate(document)

        def find_all(document):
            calls["traverse"] += 1
            return real_find_all(document)

        monkeypatch.setattr(fc, "annotate_document_in_place", annotate)
        monkeypatch.setattr(fc, "find_all_candidates", find_all)

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=reader,
            run_id="run-once",
        )

        assert calls == {"read": 1, "annotate": 1, "traverse": 1}
        assert manifest["status"] == "complete"
        assert manifest["extraction_run_id"] == "extraction-run-1"
        assert manifest["counts"]["documents_succeeded"] == 1
        record = json.loads((candidates_root / "clause_5.jsonl").read_text(encoding="utf-8"))
        assert record["run_id"] == "run-once"
        assert record["rule_version"] == manifest["rule_version"]

    def test_manifest_is_replaced_after_all_candidate_files(self, tmp_path, monkeypatch):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        (extracted_root / "sample.pdf.json.gz").write_bytes(b"placeholder")
        replace_destinations = []
        real_replace = fc.os.replace

        def recording_replace(source, destination):
            replace_destinations.append(Path(destination).name)
            real_replace(source, destination)

        monkeypatch.setattr(fc.os, "replace", recording_replace)

        fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=lambda path: (
                _upstream_manifest(_artifact_entry())
                if path == upstream_manifest
                else _v2_document()
            ),
            run_id="atomic-run",
        )

        assert replace_destinations[-1] == "_manifest.json"
        assert set(replace_destinations[:-1]) == set(fc._OUTPUT_FILENAMES.values())
        assert not list(candidates_root.glob("*.tmp"))

    def test_document_failure_publishes_partial_manifest_and_main_defaults_nonzero(
        self, tmp_path, monkeypatch
    ):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        good = extracted_root / "good.pdf.json.gz"
        bad = extracted_root / "bad.pdf.json.gz"
        good.write_bytes(b"good")
        bad.write_bytes(b"bad")

        def reader(path):
            if path == upstream_manifest:
                return _upstream_manifest(
                    _artifact_entry("good.pdf.json.gz"),
                    _artifact_entry("bad.pdf.json.gz", extraction_id="extract-bad"),
                )
            if path == bad:
                raise ValueError("corrupt gzip")
            return _v2_document()

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=reader,
            run_id="partial-run",
        )

        assert manifest["status"] == "partial"
        assert manifest["counts"]["documents_failed"] == 1
        assert manifest["failures"][0]["path"] == "bad.pdf.json.gz"
        persisted = json.loads((candidates_root / "_manifest.json").read_text(encoding="utf-8"))
        assert persisted == manifest

        monkeypatch.setattr(fc, "run_candidate_scan", lambda **_kwargs: manifest)
        monkeypatch.setattr(sys, "argv", ["find_candidates.py"])
        assert fc.main() == 1
        monkeypatch.setattr(sys, "argv", ["find_candidates.py", "--allow-partial"])
        assert fc.main() == 0

    def test_partial_upstream_manifest_blocks_unless_explicitly_allowed(self, tmp_path):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        (extracted_root / "sample.pdf.json.gz").write_bytes(b"document")

        def reader(path):
            if path == upstream_manifest:
                return _upstream_manifest(_artifact_entry(), status="partial")
            return _v2_document()

        with pytest.raises(RuntimeError, match="--allow-partial"):
            fc.run_candidate_scan(
                extracted_root=extracted_root,
                candidates_root=candidates_root,
                reader=reader,
                run_id="blocked-run",
            )

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=reader,
            run_id="allowed-run",
            allow_partial=True,
        )
        assert manifest["status"] == "partial"
        assert manifest["failures"][0]["path"] == "_run_manifest.json.gz"

    def test_missing_upstream_manifest_blocks_unless_explicitly_allowed(self, tmp_path):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        (extracted_root / "stale.pdf.json.gz").write_bytes(b"must not be scanned")

        with pytest.raises(RuntimeError, match="manifest is missing"):
            fc.run_candidate_scan(
                extracted_root=extracted_root,
                candidates_root=candidates_root,
                run_id="missing-manifest",
            )

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            run_id="allowed-missing-manifest",
            allow_partial=True,
        )
        assert manifest["status"] == "partial"
        assert manifest["failures"][0]["path"] == "_run_manifest.json.gz"
        assert manifest["counts"]["documents_seen"] == 0

    def test_missing_artifact_inventory_blocks_unless_explicitly_allowed(self, tmp_path):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        upstream_manifest.write_bytes(b"manifest")

        def reader(path):
            assert path == upstream_manifest
            return {"run_id": "extraction-no-artifacts", "status": "complete"}

        with pytest.raises(RuntimeError, match="artifacts are missing"):
            fc.run_candidate_scan(
                extracted_root=extracted_root,
                candidates_root=candidates_root,
                reader=reader,
                run_id="blocked-no-artifacts",
            )

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=reader,
            run_id="allowed-no-artifacts",
            allow_partial=True,
        )
        assert manifest["status"] == "partial"
        assert manifest["extraction_run_id"] == "extraction-no-artifacts"
        assert manifest["counts"]["documents_seen"] == 0

    def test_only_manifest_allowlisted_artifacts_are_scanned(self, tmp_path):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        allowlisted = extracted_root / "allowlisted.pdf.json.gz"
        stale = extracted_root / "stale.pdf.json.gz"
        for path in (upstream_manifest, allowlisted, stale):
            path.write_bytes(b"placeholder")
        reads = []

        def reader(path):
            if path == upstream_manifest:
                return _upstream_manifest(_artifact_entry("allowlisted.pdf.json.gz"))
            reads.append(path)
            return _v2_document()

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=reader,
            run_id="allowlist-only",
        )

        assert reads == [allowlisted.resolve()]
        assert stale.resolve() not in reads
        assert manifest["counts"]["documents_seen"] == 1

    def test_manifest_artifact_cannot_escape_extracted_root(self, tmp_path):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        (tmp_path / "outside.pdf.json.gz").write_bytes(b"must not be read")

        def reader(path):
            assert path == upstream_manifest
            return _upstream_manifest(_artifact_entry("../outside.pdf.json.gz"))

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=reader,
            run_id="path-escape",
            allow_partial=True,
        )

        assert manifest["status"] == "partial"
        assert manifest["counts"]["documents_failed"] == 1
        assert "escapes extracted_root" in manifest["failures"][0]["error"]

    @pytest.mark.parametrize("field", ["source_path", "extraction_id", "status"])
    def test_manifest_identity_must_match_document(self, tmp_path, field):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        artifact = extracted_root / "sample.pdf.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        artifact.write_bytes(b"document")
        entry = _artifact_entry()
        entry[field] = "different"

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=lambda path: (
                _upstream_manifest(entry) if path == upstream_manifest else _v2_document()
            ),
            run_id=f"mismatch-{field}",
            allow_partial=True,
        )

        assert manifest["status"] == "partial"
        assert manifest["counts"]["documents_succeeded"] == 0
        assert f"{field} mismatch" in manifest["failures"][0]["error"]

    def test_needs_ocr_artifact_scans_available_text_without_partial_run(self, tmp_path):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        artifact = extracted_root / "sample.pdf.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        artifact.write_bytes(b"document")
        document = _v2_document(status="needs_ocr")
        document["pages"][0]["page"] = 2
        document["pages"].insert(
            0,
            {
                "page": 1,
                "width_pt": 595.0,
                "height_pt": 842.0,
                "rotation": 0,
                "lines": [],
            },
        )

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=lambda path: (
                _upstream_manifest(_artifact_entry(status="needs_ocr"))
                if path == upstream_manifest
                else document
            ),
            run_id="needs-ocr-run",
        )

        assert manifest["status"] == "complete"
        assert manifest["counts"]["documents_succeeded"] == 1
        assert manifest["counts"]["documents_failed"] == 0

    def test_quarantined_document_keeps_candidate_run_partial(self, tmp_path):
        extracted_root = tmp_path / "extracted"
        candidates_root = tmp_path / "candidates"
        extracted_root.mkdir()
        upstream_manifest = extracted_root / "_run_manifest.json.gz"
        upstream_manifest.write_bytes(b"manifest")
        (extracted_root / "bad.pdf.json.gz").write_bytes(b"document")

        manifest = fc.run_candidate_scan(
            extracted_root=extracted_root,
            candidates_root=candidates_root,
            reader=lambda path: (
                _upstream_manifest(
                    _artifact_entry(
                        "bad.pdf.json.gz",
                        status="quarantine",
                    )
                )
                if path == upstream_manifest
                else _v2_document(status="quarantine")
            ),
            run_id="quarantined-run",
            allow_partial=True,
        )

        assert manifest["status"] == "partial"
        assert manifest["counts"]["documents_succeeded"] == 0
        assert "quarantine" in manifest["failures"][0]["error"]
