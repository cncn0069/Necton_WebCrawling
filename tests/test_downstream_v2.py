from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_llm_augment as run_llm  # noqa: E402
import test_reconstruct_pdf as reconstruct  # noqa: E402

from rd2.augmentation.llm_augment import (  # noqa: E402
    CandidateValidationError,
    augment_document,
    build_user_prompt,
    validate_candidates_for_document,
)
from rd2.extraction.storage import extraction_output_path  # noqa: E402
from rd2.generators.clause_data import CLAUSES  # noqa: E402


def _candidate(
    text: str = "내부검토 기준액",
    *,
    candidate_id: str = "candidate-1",
    line_ids: list[int] | None = None,
    extraction_id: str = "extract-1",
    run_id: str = "run-1",
    source_path: str = "data/moel/notification/sample.pdf",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "rule_version": "rules-v2",
        "extraction_id": extraction_id,
        "source_path": source_path,
        "source": "moel",
        "doc_type": "notification",
        "doc_id": "1",
        "line_ids": line_ids or [1],
        "text": text,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "page": 1,
        "bbox_pt": [1, 2, 3, 4],
        "style_runs": [{"font": "SecretFont"}],
    }


def _write_extracted(
    data_root: Path,
    extracted_root: Path,
    *,
    source_path: str = "data/moel/notification/sample.pdf",
    extraction_id: str = "extract-1",
    texts: list[str] | None = None,
    source_bytes: bytes = b"source bytes",
) -> None:
    texts = texts or ["앞 문단", "내부검토 기준액", "뒤 문단"]
    source_file = data_root.parent / Path(source_path)
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(source_bytes)
    document = {
        "schema_version": 2,
        "extraction_id": extraction_id,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_path": source_path,
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
                "lines": [
                    {
                        "line_id": index,
                        "block_id": index,
                        "order": index,
                        "text": text,
                        "bbox_pt": None,
                        "style_runs": [],
                    }
                    for index, text in enumerate(texts)
                ],
            }
        ],
    }
    path = extraction_output_path(source_file, data_root, extracted_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(document, f, ensure_ascii=False)


def test_prompt_sends_only_candidate_id_and_text():
    candidate = _candidate()

    prompt = build_user_prompt([candidate], CLAUSES["5"])
    payload_text = prompt.split("후보 목록:\n", 1)[1].split("\n\n", 1)[0]
    payload = json.loads(payload_text)

    assert payload == [
        {"candidate_id": candidate["candidate_id"], "text": candidate["text"]}
    ]
    assert "bbox_pt" not in prompt
    assert "style_runs" not in prompt
    assert "line_ids" not in prompt
    assert "text_sha256" not in prompt


class _FakeClient:
    def __init__(self, response_payload: dict):
        message = SimpleNamespace(content=json.dumps(response_payload, ensure_ascii=False))
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: response)
        )


def test_augment_result_carries_v2_identity_from_input_candidate():
    candidate = _candidate(text="내부 기준액 5억원", line_ids=[3, 4])
    response = {
        "selections": [
            {
                "candidate_id": candidate["candidate_id"],
                "extraction_id": "model-forged-extraction",
                "text_sha256": "model-forged-hash",
                "synthetic": "협상 기준액 6억원",
                "transformation": "contract_negotiation_info",
                "reason": "협상 전 비공개 기준가이기 때문",
            }
        ]
    }

    results = augment_document([candidate], "5", client=_FakeClient(response))

    assert results[0]["candidate_id"] == candidate["candidate_id"]
    assert results[0]["extraction_id"] == candidate["extraction_id"]
    assert results[0]["text_sha256"] == candidate["text_sha256"]
    assert results[0]["line_ids"] == [3, 4]
    assert results[0]["page"] == 1
    assert "span_id" not in results[0]
    assert "page_no" not in results[0]


def test_augment_drops_unknown_candidate_id():
    response = {
        "selections": [
            {
                "candidate_id": "hallucinated",
                "synthetic": "협상 기준액",
                "transformation": "contract_negotiation_info",
                "reason": "근거",
            }
        ]
    }

    assert augment_document([_candidate()], "5", client=_FakeClient(response)) == []


def test_canonical_validation_rejects_stale_extraction(tmp_path):
    data_root = tmp_path / "data"
    extracted_root = data_root / "extracted"
    _write_extracted(data_root, extracted_root, extraction_id="current")

    with pytest.raises(CandidateValidationError, match="stale candidate"):
        validate_candidates_for_document(
            [_candidate(extraction_id="old")],
            data_root=data_root,
            extracted_root=extracted_root,
        )


def test_canonical_validation_rejects_source_changed_after_extraction(tmp_path):
    data_root = tmp_path / "data"
    extracted_root = data_root / "extracted"
    candidate = _candidate()
    _write_extracted(data_root, extracted_root)
    source_file = data_root.parent / Path(candidate["source_path"])
    source_file.write_bytes(b"changed after extraction")

    with pytest.raises(CandidateValidationError, match="source_sha256"):
        validate_candidates_for_document(
            [candidate],
            data_root=data_root,
            extracted_root=extracted_root,
        )


def test_partial_manifest_requires_explicit_override(tmp_path):
    manifest = {
        "run_id": "run-1",
        "rule_version": "rules-v2",
        "status": "partial",
        "counts": {},
        "failures": [{"source_path": "data/bad.pdf"}],
    }
    (tmp_path / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="완결되지"):
        run_llm._load_candidate_manifest(tmp_path)
    assert run_llm._load_candidate_manifest(tmp_path, allow_partial=True)["run_id"] == "run-1"


def test_candidate_record_run_id_must_match_manifest(tmp_path, monkeypatch):
    (tmp_path / "clause_5.jsonl").write_text(
        json.dumps(_candidate(run_id="other"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_llm, "_CANDIDATES_ROOT", tmp_path)

    with pytest.raises(ValueError, match="run_id가 manifest와 불일치"):
        run_llm._load_candidates_by_doc("5", expected_run_id="run-1")


def test_candidate_record_count_must_match_manifest(tmp_path, monkeypatch):
    (tmp_path / "clause_5.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(run_llm, "_CANDIDATES_ROOT", tmp_path)

    with pytest.raises(ValueError, match="개수가 manifest와 불일치"):
        run_llm._load_candidates_by_doc(
            "5",
            expected_run_id="run-1",
            expected_count=1,
        )


def test_candidate_rule_version_must_match_manifest(tmp_path, monkeypatch):
    (tmp_path / "clause_5.jsonl").write_text(
        json.dumps(_candidate(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_llm, "_CANDIDATES_ROOT", tmp_path)

    with pytest.raises(ValueError, match="rule_version이 manifest와 불일치"):
        run_llm._load_candidates_by_doc(
            "5",
            expected_run_id="run-1",
            expected_rule_version="different-rules",
        )


def test_augmented_output_path_preserves_original_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(run_llm, "_AUGMENTED_LLM_ROOT", tmp_path)

    pdf = run_llm._output_path("data/moel/notification/sample.pdf", "8")
    hwp = run_llm._output_path("data/moel/notification/sample.hwp", "8")
    hwpx = run_llm._output_path("data/moel/notification/sample.hwpx", "8")

    assert pdf.name == "sample.pdf.clause8.json"
    assert hwp.name == "sample.hwp.clause8.json"
    assert hwpx.name == "sample.hwpx.clause8.json"
    assert len({pdf, hwp, hwpx}) == 3


def _augmented_payload(candidate: dict, **overrides) -> dict:
    payload = {
        "source_path": candidate["source_path"],
        "extraction_id": candidate["extraction_id"],
        "candidate_run_id": candidate["run_id"],
        "candidate_rule_version": candidate["rule_version"],
        "clause_no": "5",
        "selections": [
            {
                "candidate_id": candidate["candidate_id"],
                "extraction_id": candidate["extraction_id"],
                "text_sha256": candidate["text_sha256"],
                "line_ids": candidate["line_ids"],
                "page": candidate["page"],
                "clause": "5",
                "original": candidate["text"],
                "synthetic": "합성문",
                "transformation": "test",
                "reason": "test",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_existing_augmented_output_is_reused_only_when_identity_matches(tmp_path):
    candidate = _candidate()
    path = tmp_path / "current.json"
    path.write_text(
        json.dumps(_augmented_payload(candidate), ensure_ascii=False),
        encoding="utf-8",
    )

    run_llm._validate_existing_augmented_output(
        path,
        source_path=candidate["source_path"],
        candidates=[candidate],
        manifest={"run_id": "run-1", "rule_version": "rules-v2"},
        clause_no="5",
    )


@pytest.mark.parametrize(
    ("field", "stale_value"),
    [
        ("extraction_id", "old-extraction"),
        ("candidate_run_id", "old-run"),
        ("candidate_rule_version", "old-rules"),
    ],
)
def test_existing_augmented_output_rejects_stale_top_level_identity(
    tmp_path, field, stale_value
):
    candidate = _candidate()
    path = tmp_path / "stale.json"
    path.write_text(
        json.dumps(
            _augmented_payload(candidate, **{field: stale_value}),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=field):
        run_llm._validate_existing_augmented_output(
            path,
            source_path=candidate["source_path"],
            candidates=[candidate],
            manifest={"run_id": "run-1", "rule_version": "rules-v2"},
            clause_no="5",
        )


def test_existing_augmented_output_rejects_tampered_selection_identity(tmp_path):
    candidate = _candidate()
    payload = _augmented_payload(candidate)
    payload["selections"][0]["text_sha256"] = "tampered"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="text_sha256"):
        run_llm._validate_existing_augmented_output(
            path,
            source_path=candidate["source_path"],
            candidates=[candidate],
            manifest={"run_id": "run-1", "rule_version": "rules-v2"},
            clause_no="5",
        )


def test_main_does_not_silently_skip_stale_existing_augmented_output(
    tmp_path, monkeypatch
):
    candidate = _candidate()
    path = tmp_path / "stale.json"
    path.write_text(
        json.dumps(
            _augmented_payload(candidate, candidate_run_id="old-run"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_llm,
        "_load_candidate_manifest",
        lambda **_kwargs: {
            "run_id": "run-1",
            "rule_version": "rules-v2",
            "status": "complete",
            "counts": {"5": 1},
        },
    )
    monkeypatch.setattr(
        run_llm,
        "_load_candidates_by_doc",
        lambda *_args, **_kwargs: {candidate["source_path"]: [candidate]},
    )
    monkeypatch.setattr(run_llm, "_output_path", lambda *_args: path)
    monkeypatch.setattr(sys, "argv", ["run_llm_augment.py", "--clause", "5"])

    with pytest.raises(RuntimeError, match="--force"):
        run_llm.main()


def test_current_existing_output_skips_without_llm_or_database(tmp_path, monkeypatch):
    candidate = _candidate()
    path = tmp_path / "current.json"
    path.write_text(
        json.dumps(_augmented_payload(candidate), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_llm,
        "_load_candidate_manifest",
        lambda **_kwargs: {
            "run_id": "run-1",
            "rule_version": "rules-v2",
            "status": "complete",
            "counts": {"5": 1},
        },
    )
    monkeypatch.setattr(
        run_llm,
        "_load_candidates_by_doc",
        lambda *_args, **_kwargs: {candidate["source_path"]: [candidate]},
    )
    monkeypatch.setattr(run_llm, "_output_path", lambda *_args: path)
    monkeypatch.setattr(run_llm, "validate_candidates_for_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        run_llm,
        "augment_document",
        lambda *_args, **_kwargs: pytest.fail("current output must not call the LLM"),
    )
    monkeypatch.setattr(
        run_llm,
        "DocumentStore",
        lambda: pytest.fail("current output must not open the database"),
    )
    monkeypatch.setattr(sys, "argv", ["run_llm_augment.py", "--clause", "5"])

    run_llm.main()


def test_force_with_no_selections_replaces_stale_output_with_empty_current_envelope(
    tmp_path, monkeypatch
):
    candidate = _candidate()
    path = tmp_path / "stale.json"
    path.write_text(
        json.dumps(
            _augmented_payload(candidate, candidate_run_id="old-run"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeStore:
        def get_by_body_file_path(self, _path):
            return None

        def close(self):
            return None

    monkeypatch.setattr(
        run_llm,
        "_load_candidate_manifest",
        lambda **_kwargs: {
            "run_id": "run-1",
            "rule_version": "rules-v2",
            "status": "complete",
            "counts": {"5": 1},
        },
    )
    monkeypatch.setattr(
        run_llm,
        "_load_candidates_by_doc",
        lambda *_args, **_kwargs: {candidate["source_path"]: [candidate]},
    )
    monkeypatch.setattr(run_llm, "_output_path", lambda *_args: path)
    monkeypatch.setattr(run_llm, "validate_candidates_for_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_llm, "augment_document", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(run_llm, "DocumentStore", FakeStore)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_llm_augment.py", "--clause", "5", "--force"],
    )

    run_llm.main()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["candidate_run_id"] == "run-1"
    assert saved["candidate_rule_version"] == "rules-v2"
    assert saved["extraction_id"] == candidate["extraction_id"]
    assert saved["selections"] == []


def test_reconstruction_resolves_multi_line_v2_layout_and_hash():
    original = "입찰 계약 예정가격"
    selection = {
        "candidate_id": "candidate-1",
        "line_ids": [10, 11],
        "original": original,
        "text_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
    }
    line_index = {
        10: {
            "page": 2,
            "cleaned_text": "입찰 계약",
            "bbox_pt": [10.0, 20.0, 100.0, 30.0],
            "style_runs": [{"size_pt": 10.0, "color": 7}],
        },
        11: {
            "page": 2,
            "cleaned_text": "예정가격",
            "bbox_pt": [10.0, 30.2, 80.0, 40.2],
            "style_runs": [],
        },
    }

    layout = reconstruct._selection_layout(selection, line_index)

    assert layout == {
        "page": 2,
        "bbox_pt": [10.0, 20.0, 100.0, 40.2],
        "size_pt": 10.0,
        "color": 7,
    }


def test_reconstruction_rejects_tampered_selection_hash():
    selection = {
        "candidate_id": "candidate-1",
        "line_ids": [10],
        "original": "입찰 계약",
        "text_sha256": "0" * 64,
    }
    line_index = {
        10: {
            "page": 1,
            "cleaned_text": "입찰 계약",
            "bbox_pt": [10.0, 20.0, 100.0, 30.0],
            "style_runs": [],
        }
    }

    with pytest.raises(ValueError, match="text_sha256"):
        reconstruct._selection_layout(selection, line_index)


def _write_candidate_run(
    root: Path,
    candidate: dict,
    *,
    run_id: str = "run-1",
    rule_version: str = "rules-v2",
    include_candidate: bool = True,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    records = []
    if include_candidate:
        records.append({**candidate, "run_id": run_id, "rule_version": rule_version})
    (root / "clause_5.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    (root / "_manifest.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "run_id": run_id,
                "rule_version": rule_version,
                "counts": {"5": len(records)},
            }
        ),
        encoding="utf-8",
    )


def test_reconstruction_accepts_augmented_selection_from_current_candidate_run(tmp_path):
    candidate = _candidate()
    candidates_root = tmp_path / "candidates"
    _write_candidate_run(candidates_root, candidate)

    reconstruct._validate_augmented_candidate_provenance(
        _augmented_payload(candidate),
        candidates_root=candidates_root,
    )


def test_reconstruction_rejects_orphan_from_previous_candidate_run(tmp_path):
    candidate = _candidate()
    candidates_root = tmp_path / "candidates"
    _write_candidate_run(
        candidates_root,
        candidate,
        run_id="new-run",
        rule_version="new-rules",
        include_candidate=False,
    )

    with pytest.raises(ValueError, match="candidate_run_id"):
        reconstruct._validate_augmented_candidate_provenance(
            _augmented_payload(candidate),
            candidates_root=candidates_root,
        )


def test_reconstruction_rejects_selection_missing_from_current_candidate_jsonl(tmp_path):
    candidate = _candidate()
    candidates_root = tmp_path / "candidates"
    _write_candidate_run(
        candidates_root,
        candidate,
        include_candidate=False,
    )

    with pytest.raises(ValueError, match="현재 후보 run에 없습니다"):
        reconstruct._validate_augmented_candidate_provenance(
            _augmented_payload(candidate),
            candidates_root=candidates_root,
        )
