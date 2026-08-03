import json
from pathlib import Path

import fitz
import pytest

from rd2.generators import meeting_minutes_rendering
from rd2.generators.generated_document_pipeline import (
    build_meeting_minutes_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.meeting_minutes_rendering import (
    build_meeting_minutes_variation_specs,
    render_meeting_minutes_variations,
)
from scripts.render_generated_documents import render_input_file


def _payload() -> dict:
    return {
        "result": {
            "contract_version": "2.0.0",
            "source_classification": {
                "document_type": "meeting_minutes",
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generated_document": {
                "contract_version": "2.0.0",
                "title": "2026년 제2차 지역교통 개선협의회 회의록",
                "agency_name": "입력기관명보존원",
                "document_metadata": None,
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "m1",
                        "text": (
                            "첫번째고유문단은 지역교통 개선협의회 개최 "
                            "목적을 설명합니다."
                        ),
                    },
                    {
                        "kind": "key_value",
                        "block_id": "m2",
                        "entries": [
                            {
                                "key": "고유일시",
                                "value": "2026년 7월 24일 14:00-16:00",
                            },
                            {
                                "key": "고유장소",
                                "value": "본관 3층 협의실",
                            },
                            {
                                "key": "고유참석",
                                "value": "관계 부서 담당자 12명",
                            },
                            {
                                "key": "고유주재",
                                "value": "교통정책과장",
                            },
                        ],
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "m3",
                        "items": [
                            "첫번째고유논의항목",
                            "두번째고유논의항목",
                            "세번째고유논의항목",
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "m4",
                        "columns": ["고유안건", "주요논의", "처리결과"],
                        "rows": [
                            [
                                "순환버스 배차",
                                "혼잡 구간 배차 간격 조정",
                                "시범운영 후 재검토",
                            ],
                            [
                                "환승시설 정비",
                                "안내표지와 보행 동선 확인",
                                "합동점검 실시",
                            ],
                        ],
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "m5",
                        "attachment_id": "MM-2026-02",
                        "label": "고유배차조정검토표",
                        "description": "회의자료 1부",
                    },
                ],
                "body_text": (
                    "첫번째고유문단은 지역교통 개선협의회 개최 목적을 "
                    "설명합니다.\n\n"
                    "고유일시: 2026년 7월 24일 14:00-16:00\n"
                    "고유장소: 본관 3층 협의실\n"
                    "고유참석: 관계 부서 담당자 12명\n"
                    "고유주재: 교통정책과장\n\n"
                    "- 첫번째고유논의항목\n"
                    "- 두번째고유논의항목\n"
                    "- 세번째고유논의항목\n\n"
                    "고유안건\t주요논의\t처리결과\n"
                    "순환버스 배차\t혼잡 구간 배차 간격 조정\t"
                    "시범운영 후 재검토\n"
                    "환승시설 정비\t안내표지와 보행 동선 확인\t"
                    "합동점검 실시\n\n"
                    "[첨부] 고유배차조정검토표 (MM-2026-02): 회의자료 1부"
                ),
            },
        },
        "receipt": {
            "model_id": "gpt-4o",
            "response_id": "meeting-response",
            "request_id": "meeting-request",
        },
        "failure": None,
    }


def _pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "".join(
            "".join(
                str(block[4])
                for block in page.get_text("blocks", sort=True)
            )
            for page in document
        )


def test_meeting_context_preserves_blocks_without_inventing_metadata() -> None:
    context = build_meeting_minutes_context(
        parse_generation_payload(_payload()),
        seed=731,
    )

    assert [block["block_id"] for block in context["blocks"]] == [
        "m1",
        "m2",
        "m3",
        "m4",
        "m5",
    ]
    assert [block["kind"] for block in context["blocks"]] == [
        "paragraph",
        "key_value",
        "bullet_list",
        "table",
        "attachment_reference",
    ]
    assert context["agency_name"] == "입력기관명보존원"
    assert context["has_wide_blocks"] is False
    assert context["signers"] == []
    assert context["administrative_events"] == []


def test_meeting_context_preserves_optional_declared_metadata() -> None:
    payload = _payload()
    payload["result"]["generated_document"]["document_metadata"] = {
        "approval_line": {
            "slots": [
                {
                    "role": "확인",
                    "name": "김가온",
                    "status": "approved",
                    "approved_at": "2026-07-30",
                }
            ]
        },
        "administrative_events": [
            {
                "type": "follow_up",
                "date": "2026-08-20",
                "text": "입력된 후속 확인 문구",
            }
        ],
    }

    context = build_meeting_minutes_context(
        parse_generation_payload(payload),
        seed=731,
    )

    assert context["signers"][0]["role"] == "확인"
    assert context["signers"][0]["name"] == "김가온"
    assert context["administrative_events"][0]["text"] == (
        "입력된 후속 확인 문구"
    )


def test_meeting_variations_are_deterministic_and_structural() -> None:
    first = build_meeting_minutes_variation_specs(
        "meeting_03_columns",
        count=3,
        base_seed=1234,
    )
    second = build_meeting_minutes_variation_specs(
        "meeting_03_columns",
        count=3,
        base_seed=1234,
    )

    assert first == second
    assert [spec.density for spec in first] == [
        "balanced",
        "compact",
        "airy",
    ]
    assert [
        (spec.key_value_columns, spec.list_columns)
        for spec in first
    ] == [(1, 1), (2, 2), (1, 2)]


def test_meeting_routes_to_four_templates_and_preserves_source(
    tmp_path: Path,
) -> None:
    payload = _payload()
    envelope = parse_generation_payload(payload)
    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260731,
    )

    assert len(manifest) == 4
    assert {entry["template_slug"] for entry in manifest} == {
        "meeting_01_registry",
        "meeting_02_sequence",
        "meeting_03_columns",
        "meeting_04_docket",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(
        entry["renderer_family"] == "meeting_minutes"
        for entry in manifest
    )
    assert all(entry["actual_pages"] <= 10 for entry in manifest)

    required = source_text_atoms(envelope.result.generated_document)
    expected_order = (
        "첫번째고유문단",
        "고유일시",
        "첫번째고유논의항목",
        "고유안건",
        "고유배차조정검토표",
    )
    for entry in manifest:
        pdf_path = Path(str(entry["pdf"]))
        rendered_text = "".join(_pdf_text(pdf_path).split())
        positions = [
            rendered_text.index("".join(token.split()))
            for token in expected_order
        ]
        if entry["template_slug"] != "meeting_03_columns":
            assert positions == sorted(positions)
        _, source_order_text, _ = meeting_minutes_rendering._inspect_pdf(
            pdf_path
        )
        assert all(
            "".join(atom.split()) in source_order_text
            for atom in required
        )

        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        for invented_text in (
            "수신",
            "시행번호",
            "의결사항",
            "회의 개요",
            "주요 논의사항",
        ):
            assert invented_text not in html


def test_arbitrary_block_order_is_preserved_in_pdf(
    tmp_path: Path,
) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    document["blocks"] = [
        document["blocks"][4],
        document["blocks"][0],
        document["blocks"][2],
        document["blocks"][1],
        document["blocks"][3],
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        template_slugs={"meeting_04_docket"},
    )
    rendered_text = "".join(
        _pdf_text(Path(str(manifest[0]["pdf"]))).split()
    )
    markers = (
        "고유배차조정검토표",
        "첫번째고유문단",
        "첫번째고유논의항목",
        "고유일시",
        "고유안건",
    )
    positions = [rendered_text.index(marker) for marker in markers]

    assert positions == sorted(positions)


def test_long_meeting_minutes_flow_without_truncation(
    tmp_path: Path,
) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    repeated_text = (
        "장문회의록입력은 페이지 경계에서도 원문 순서를 유지해야 합니다. "
        "발언과 논의 결과는 입력된 문장 그대로 이어집니다. "
    )
    document["body_text"] = None
    document["document_metadata"] = None
    document["blocks"] = [
        {
            "kind": "paragraph",
            "block_id": f"long-{index:02d}",
            "text": f"{index:02d} {repeated_text * 16}",
        }
        for index in range(1, 8)
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260731,
    )

    assert len(manifest) == 4
    for entry in manifest:
        assert entry["status"] == "ok"
        assert 2 <= entry["actual_pages"] <= 10
        rendered_text = "".join(
            _pdf_text(Path(str(entry["pdf"]))).split()
        )
        for index in range(1, 8):
            assert f"{index:02d}" in rendered_text


@pytest.mark.parametrize(
    "template_slug",
    (
        "meeting_01_registry",
        "meeting_02_sequence",
        "meeting_03_columns",
        "meeting_04_docket",
    ),
)
def test_wide_tables_use_landscape_pages_after_other_blocks(
    tmp_path: Path,
    template_slug: str,
) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    wide_columns = [f"고유열{index}" for index in range(1, 8)]
    document["blocks"] = [
        document["blocks"][0],
        {
            "kind": "table",
            "block_id": "wide-1",
            "columns": wide_columns,
            "rows": [[f"첫번째값{index}" for index in range(1, 8)]],
        },
        document["blocks"][2],
        {
            "kind": "table",
            "block_id": "wide-2",
            "columns": wide_columns,
            "rows": [[f"두번째값{index}" for index in range(1, 8)]],
        },
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        template_slugs={template_slug},
    )

    with fitz.open(Path(str(manifest[0]["pdf"]))) as document_pdf:
        assert sum(
            page.rect.width > page.rect.height
            for page in document_pdf
        ) == 2


def test_meeting_template_escapes_model_html(tmp_path: Path) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    document["blocks"][0]["text"] = (
        '<img src="file:///private/etc/passwd"> & 본문'
    )

    manifest = render_generation_payload(
        payload,
        tmp_path,
        template_slugs={"meeting_01_registry"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert "&lt;img" in html
    assert '<img src="file:///private/etc/passwd">' not in html


def test_meeting_rejects_unknown_template_and_invalid_counts(
    tmp_path: Path,
) -> None:
    context = build_meeting_minutes_context(
        parse_generation_payload(_payload()),
        seed=731,
    )

    with pytest.raises(ValueError, match="Unknown meeting"):
        build_meeting_minutes_variation_specs("unknown")
    with pytest.raises(ValueError, match="between 1 and 10"):
        build_meeting_minutes_variation_specs(
            "meeting_01_registry",
            count=0,
        )
    with pytest.raises(ValueError, match="between 1 and 10"):
        render_meeting_minutes_variations(
            context,
            tmp_path / "count",
            per_template=11,
        )


def test_meeting_budget_rejection_removes_stale_artifacts(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "oversized"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    with pytest.raises(ValueError, match="exceeds render budget"):
        render_meeting_minutes_variations(
            {
                "title": "제목",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "x" * 4_001,
                        "text": "",
                    }
                ],
            },
            output_dir,
            max_pages=1,
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest[0]["status"] == "rejected"


def test_meeting_rejects_invalid_manifest_metadata_transactionally(
    tmp_path: Path,
) -> None:
    context = build_meeting_minutes_context(
        parse_generation_payload(_payload()),
        seed=731,
    )
    output_dir = tmp_path / "invalid-metadata"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    with pytest.raises(ValueError, match="metadata cannot be encoded"):
        render_meeting_minutes_variations(
            context,
            output_dir,
            template_slugs={"meeting_01_registry"},
            input_metadata={"nested": {"untrusted": "\ud800"}},
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest_text = (
        output_dir / "manifest.json"
    ).read_text(encoding="utf-8")
    assert "metadata cannot be encoded as UTF-8" in manifest_text
    assert "\\ud800" not in manifest_text


def test_meeting_render_error_replaces_stale_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_meeting_minutes_context(
        parse_generation_payload(_payload()),
        seed=731,
    )
    output_dir = tmp_path / "render-error"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    def failed_inspection(_: Path) -> tuple[int, str, str]:
        raise OSError("민감한 원문이 포함될 수 있는 내부 오류")

    monkeypatch.setattr(
        meeting_minutes_rendering,
        "_inspect_pdf",
        failed_inspection,
    )

    with pytest.raises(RuntimeError, match="rendering failed"):
        render_meeting_minutes_variations(
            context,
            output_dir,
            template_slugs={"meeting_01_registry"},
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest_text = (
        output_dir / "manifest.json"
    ).read_text(encoding="utf-8")
    assert "render_error:OSError" in manifest_text
    assert "민감한 원문" not in manifest_text


def test_pattern_matcher_counts_nested_patterns_without_output_expansion() -> None:
    matcher = meeting_minutes_rendering._PatternMatcher(
        {"가", "가가", "가가가"}
    )

    assert matcher.count("가" * 1_000) == {
        "가": 1_000,
        "가가": 999,
        "가가가": 998,
    }


def test_duplicate_title_and_source_require_distinct_pdf_occurrences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {
        "document_type_label": "회의록",
        "title": "중복원문",
        "title_class": "",
        "agency_name": "",
        "blocks": [
            {
                "kind": "paragraph",
                "block_id": "duplicate",
                "text": "중복원문",
                "is_wide": False,
            }
        ],
        "has_wide_blocks": False,
        "signers": [],
        "approval_manifest": [],
        "administrative_events": [],
    }

    monkeypatch.setattr(
        meeting_minutes_rendering,
        "_inspect_pdf",
        lambda _: (1, "회의록중복원문", "회의록중복원문"),
    )

    with pytest.raises(RuntimeError, match="validation failed"):
        render_meeting_minutes_variations(
            context,
            tmp_path,
            template_slugs={"meeting_01_registry"},
            required_source_texts=("중복원문",),
        )

    manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest[0]["status"] == "rejected"
    assert manifest[0]["source_text_present"] is False
    assert not list(tmp_path.rglob("*.pdf"))


def test_page_overflow_rejection_does_not_publish_partial_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_meeting_minutes_context(
        parse_generation_payload(_payload()),
        seed=731,
    )
    original_inspector = meeting_minutes_rendering._inspect_pdf

    def overflow_inspection(path: Path) -> tuple[int, str, str]:
        _, pdf_text, first_page_text = original_inspector(path)
        return 11, pdf_text, first_page_text

    monkeypatch.setattr(
        meeting_minutes_rendering,
        "_inspect_pdf",
        overflow_inspection,
    )

    with pytest.raises(RuntimeError, match="maximum 10 pages"):
        render_meeting_minutes_variations(
            context,
            tmp_path,
            template_slugs={"meeting_01_registry"},
            required_source_texts=source_text_atoms(
                parse_generation_payload(_payload()).result.generated_document
            ),
        )

    assert not list(tmp_path.rglob("*.html"))
    assert not list(tmp_path.rglob("*.pdf"))


def test_batch_routes_meeting_minutes(tmp_path: Path) -> None:
    input_path = tmp_path / "meeting.json"
    input_path.write_text(
        json.dumps(_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    batch = render_input_file(
        input_path,
        tmp_path / "output",
        per_template=1,
        base_seed=20260731,
        template_slugs={"meeting_04_docket"},
    )

    assert batch[0]["status"] == "ok"
    assert batch[0]["render_count"] == 1
    manifest = json.loads(
        (
            Path(str(batch[0]["output_dir"])) / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest[0]["template_slug"] == "meeting_04_docket"
    assert manifest[0]["input"]["document_type"] == "meeting_minutes"
