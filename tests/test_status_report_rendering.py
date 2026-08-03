import json
from pathlib import Path

import fitz
import pytest

from rd2.generators import status_report_rendering
from rd2.generators.generated_document_pipeline import (
    build_status_report_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.status_report_rendering import (
    build_status_report_variation_specs,
    render_status_report_variations,
)
from scripts.render_generated_documents import render_input_file


def _payload() -> dict:
    return {
        "result": {
            "contract_version": "1.0.0",
            "source_classification": {
                "document_type": "status_report",
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generated_document": {
                "contract_version": "1.0.0",
                "title": "지역 서비스 운영 및 이용 현황",
                "agency_name": "입력기관명보존원",
                "document_metadata": {
                    "approval_line": {
                        "slots": [
                            {
                                "role": "검토",
                                "name": "김가온",
                                "status": "approved",
                                "approved_at": "2026-07-30",
                                "stamp": {
                                    "mode": "synthetic",
                                    "stamp_text": "김가온인",
                                    "seed": 730,
                                    "profile": "dry_ink",
                                    "shape": "round",
                                },
                            }
                        ]
                    },
                    "administrative_events": [
                        {
                            "type": "follow_up",
                            "date": "2026-08-20",
                            "text": "후속확인고유문구는 입력에 포함된 내용입니다.",
                        }
                    ],
                },
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "s1",
                        "text": "첫번째고유문단은 운영 현황의 범위를 설명합니다.",
                    },
                    {
                        "kind": "key_value",
                        "block_id": "s2",
                        "entries": [
                            {"key": "고유기준일", "value": "2026년 6월 30일"},
                            {"key": "고유대상", "value": "운영시설 24개소"},
                            {"key": "고유부서", "value": "현황관리부"},
                        ],
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "s3",
                        "items": [
                            "첫번째고유확인항목",
                            "두번째고유확인항목",
                            "세번째고유확인항목",
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "s4",
                        "columns": [
                            "고유구분",
                            "대상수",
                            "운영",
                            "점검",
                            "조치",
                            "완료",
                            "비고",
                        ],
                        "rows": [
                            [
                                "생활지원",
                                "8",
                                "7",
                                "1",
                                "보완",
                                "진행",
                                "계속",
                            ]
                        ],
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "s5",
                        "attachment_id": "status-appendix-001",
                        "label": "고유세부현황표",
                        "description": "시설별 확인 내역",
                    },
                ],
                "body_text": (
                    "첫번째고유문단은 운영 현황의 범위를 설명합니다.\n\n"
                    "고유기준일: 2026년 6월 30일\n"
                    "고유대상: 운영시설 24개소\n"
                    "고유부서: 현황관리부\n\n"
                    "- 첫번째고유확인항목\n"
                    "- 두번째고유확인항목\n"
                    "- 세번째고유확인항목\n\n"
                    "고유구분\t대상수\t운영\t점검\t조치\t완료\t비고\n"
                    "생활지원\t8\t7\t1\t보완\t진행\t계속\n\n"
                    "[첨부] 고유세부현황표 (status-appendix-001): "
                    "시설별 확인 내역"
                ),
            },
        },
        "receipt": {
            "model_id": "gpt-4o",
            "response_id": "status-response",
            "request_id": "status-request",
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


def test_status_context_preserves_generic_blocks_and_metadata() -> None:
    envelope = parse_generation_payload(_payload())
    context = build_status_report_context(envelope, seed=730)

    assert [block["block_id"] for block in context["blocks"]] == [
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
    ]
    assert [block["kind"] for block in context["blocks"]] == [
        "paragraph",
        "key_value",
        "bullet_list",
        "table",
        "attachment_reference",
    ]
    assert context["blocks"][3]["column_count"] == 7
    assert context["blocks"][3]["is_wide"] is True
    assert context["agency_name"] == "입력기관명보존원"
    assert context["signers"][0]["stamp_data_uri"].startswith(
        "data:image/png;base64,"
    )
    assert context["administrative_events"][0]["text"].startswith(
        "후속확인고유문구"
    )


def test_status_context_preserves_arbitrary_block_order() -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    document["blocks"] = [
        document["blocks"][3],
        document["blocks"][0],
        document["blocks"][4],
        document["blocks"][2],
        document["blocks"][1],
    ]

    context = build_status_report_context(
        parse_generation_payload(payload),
        seed=731,
    )

    assert [block["block_id"] for block in context["blocks"]] == [
        "s4",
        "s1",
        "s5",
        "s3",
        "s2",
    ]


def test_status_context_does_not_invent_missing_metadata() -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["agency_name"] = None
    document["document_metadata"] = None

    context = build_status_report_context(
        parse_generation_payload(payload),
        seed=731,
    )

    assert context["agency_name"] == ""
    assert context["signers"] == []
    assert context["administrative_events"] == []


def test_status_variations_are_deterministic_and_structural() -> None:
    first = build_status_report_variation_specs(
        "status_03_columns",
        count=3,
        base_seed=1234,
    )
    second = build_status_report_variation_specs(
        "status_03_columns",
        count=3,
        base_seed=1234,
    )

    assert first == second
    assert [spec.density for spec in first] == [
        "balanced",
        "compact",
        "airy",
    ]
    assert [(spec.key_value_columns, spec.list_columns) for spec in first] == [
        (3, 1),
        (2, 2),
        (1, 2),
    ]


def test_status_routes_to_four_templates_and_preserves_source(
    tmp_path: Path,
) -> None:
    payload = _payload()
    envelope = parse_generation_payload(payload)
    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260730,
    )

    assert len(manifest) == 4
    assert {entry["template_slug"] for entry in manifest} == {
        "status_01_brief",
        "status_02_ledger",
        "status_03_columns",
        "status_04_chapter",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["renderer_family"] == "status_report" for entry in manifest)
    assert all(entry["input"]["document_type"] == "status_report" for entry in manifest)
    assert all(entry["actual_pages"] <= 10 for entry in manifest)

    expected_order = (
        "첫번째고유문단",
        "고유기준일",
        "첫번째고유확인항목",
        "고유구분",
        "고유세부현황표",
    )
    required = source_text_atoms(
        envelope.result.generated_document,
        include_administrative_event_dates=True,
    )
    for entry in manifest:
        pdf_path = Path(str(entry["pdf"]))
        rendered_text = "".join(_pdf_text(pdf_path).split())
        positions = [
            rendered_text.index("".join(token.split()))
            for token in expected_order
        ]
        assert positions == sorted(positions)
        assert all("".join(atom.split()) in rendered_text for atom in required)
        with fitz.open(pdf_path) as pdf:
            assert any(page.rect.width > page.rect.height for page in pdf)

        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        for invented_preview_text in (
            "주요 확인사항",
            "후속 관리",
            "공공서비스관리원",
            "2026 / 2분기",
        ):
            assert invented_preview_text not in html


def test_long_status_report_flows_without_truncation(tmp_path: Path) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    repeated_text = (
        "장문현황입력은 페이지 경계에서도 원문 순서를 유지해야 합니다. "
        "시설별 상태와 후속 조치 내역은 입력된 문장 그대로 이어집니다. "
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
        base_seed=20260730,
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


def test_arbitrary_block_order_is_preserved_in_pdf(tmp_path: Path) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    document["document_metadata"] = None
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
        template_slugs={"status_04_chapter"},
    )
    rendered_text = "".join(
        _pdf_text(Path(str(manifest[0]["pdf"]))).split()
    )
    markers = (
        "고유세부현황표",
        "첫번째고유문단",
        "첫번째고유확인항목",
        "고유기준일",
        "고유구분",
    )
    positions = [rendered_text.index(marker) for marker in markers]

    assert positions == sorted(positions)


def test_status_template_escapes_model_html(tmp_path: Path) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    document["blocks"][0]["text"] = (
        '<img src="file:///private/etc/passwd"> & 본문'
    )

    manifest = render_generation_payload(
        payload,
        tmp_path,
        template_slugs={"status_01_brief"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert '&lt;img src=&#34;file:///private/etc/passwd&#34;&gt;' in html
    assert '<img src="file:///private/etc/passwd">' not in html
    assert "&amp; 본문" in html


@pytest.mark.parametrize("count", [0, 11])
def test_status_variation_count_rejects_invalid_values(count: int) -> None:
    with pytest.raises(ValueError, match="count must be between"):
        build_status_report_variation_specs(
            "status_01_brief",
            count=count,
        )


def test_status_variation_count_accepts_boundaries() -> None:
    assert len(
        build_status_report_variation_specs("status_01_brief", count=1)
    ) == 1
    assert len(
        build_status_report_variation_specs("status_01_brief", count=10)
    ) == 10


def test_status_renderer_rejects_invalid_public_arguments(
    tmp_path: Path,
) -> None:
    context = {"title": "제목", "blocks": []}
    with pytest.raises(ValueError, match="per_template must be between"):
        render_status_report_variations(
            context,
            tmp_path / "count",
            per_template=0,
        )
    with pytest.raises(ValueError, match="per_template must be between"):
        render_status_report_variations(
            context,
            tmp_path / "count-high",
            per_template=11,
        )
    with pytest.raises(ValueError, match="max_pages must be at least 1"):
        render_status_report_variations(
            context,
            tmp_path / "pages",
            max_pages=0,
        )
    with pytest.raises(ValueError, match="At least one"):
        render_status_report_variations(
            context,
            tmp_path / "empty",
            template_slugs=set(),
        )
    with pytest.raises(ValueError, match="Unknown status report template"):
        render_status_report_variations(
            context,
            tmp_path / "unknown",
            template_slugs={"unknown"},
        )


def test_status_budget_rejection_removes_stale_artifacts(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "oversized"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    with pytest.raises(ValueError, match="exceeds render budget"):
        render_status_report_variations(
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
    assert manifest[0]["artifact_published"] is False


def test_status_rejects_invalid_utf8_before_rendering(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "invalid-unicode"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    with pytest.raises(ValueError, match="cannot be encoded as UTF-8"):
        render_status_report_variations(
            {
                "title": "제목",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "invalid",
                        "text": "\ud800",
                    }
                ],
            },
            output_dir,
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest[0]["status"] == "rejected"


def test_status_rejects_invalid_manifest_metadata_transactionally(
    tmp_path: Path,
) -> None:
    envelope = parse_generation_payload(_payload())
    context = build_status_report_context(envelope, seed=734)
    output_dir = tmp_path / "invalid-metadata"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    with pytest.raises(ValueError, match="metadata cannot be encoded"):
        render_status_report_variations(
            context,
            output_dir,
            template_slugs={"status_01_brief"},
            input_metadata={
                "generation_target": {"untrusted": "\ud800"},
            },
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest_text = (
        output_dir / "manifest.json"
    ).read_text(encoding="utf-8")
    assert "status report metadata cannot be encoded as UTF-8" in manifest_text
    assert "\\ud800" not in manifest_text


@pytest.mark.parametrize(
    ("blocks", "reason"),
    [
        (
            [
                {
                    "kind": "paragraph",
                    "block_id": f"b{index}",
                    "text": "",
                }
                for index in range(25)
            ],
            "blocks=25>24",
        ),
        (
            [
                {
                    "kind": "bullet_list",
                    "block_id": "items",
                    "items": [""] * 61,
                }
            ],
            "collection items=61>60",
        ),
        (
            [
                {
                    "kind": "table",
                    "block_id": "cells",
                    "columns": [""] * 20,
                    "rows": [[""] * 20 for _ in range(12)],
                }
            ],
            "table cells=260>250",
        ),
        (
            [
                {
                    "kind": "table",
                    "block_id": "columns",
                    "columns": [""] * 21,
                    "rows": [],
                }
            ],
            "table columns=21>20",
        ),
    ],
)
def test_status_budget_scales_with_page_limit(
    tmp_path: Path,
    blocks: list[dict],
    reason: str,
) -> None:
    output_dir = tmp_path / reason.split("=")[0].replace(" ", "-")

    with pytest.raises(ValueError, match=reason):
        render_status_report_variations(
            {"title": "제목", "blocks": blocks},
            output_dir,
            max_pages=1,
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))


def test_status_validation_rejection_is_fail_fast_and_transactional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = parse_generation_payload(_payload())
    context = build_status_report_context(envelope, seed=732)
    output_dir = tmp_path / "rejected"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")
    inspect_calls = 0

    def rejected_inspection(_: Path) -> tuple[int, str, str]:
        nonlocal inspect_calls
        inspect_calls += 1
        return 11, "", ""

    monkeypatch.setattr(
        status_report_rendering,
        "_inspect_pdf",
        rejected_inspection,
    )

    with pytest.raises(RuntimeError, match="validation failed"):
        render_status_report_variations(
            context,
            output_dir,
            per_template=10,
            template_slugs={"status_01_brief"},
            required_source_texts=source_text_atoms(
                envelope.result.generated_document
            ),
        )

    assert inspect_calls == 1
    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest) == 1
    assert manifest[0]["status"] == "rejected"
    assert manifest[0]["html"] is None
    assert manifest[0]["pdf"] is None


def test_status_render_error_replaces_stale_artifacts_without_source_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = parse_generation_payload(_payload())
    context = build_status_report_context(envelope, seed=733)
    output_dir = tmp_path / "render-error"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    def failed_inspection(_: Path) -> tuple[int, str, str]:
        raise OSError("민감한 원문이 포함될 수 있는 내부 오류")

    monkeypatch.setattr(
        status_report_rendering,
        "_inspect_pdf",
        failed_inspection,
    )

    with pytest.raises(RuntimeError, match="rendering failed"):
        render_status_report_variations(
            context,
            output_dir,
            template_slugs={"status_01_brief"},
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest_text = (
        output_dir / "manifest.json"
    ).read_text(encoding="utf-8")
    assert "render_error:OSError" in manifest_text
    assert "민감한 원문" not in manifest_text


def test_batch_routes_status_report(tmp_path: Path) -> None:
    input_path = tmp_path / "status.json"
    input_path.write_text(
        json.dumps(_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    batch = render_input_file(
        input_path,
        tmp_path / "output",
        per_template=1,
        base_seed=20260730,
        template_slugs={"status_04_chapter"},
    )

    assert batch[0]["status"] == "ok"
    assert batch[0]["render_count"] == 1
    manifest = json.loads(
        (
            Path(str(batch[0]["output_dir"])) / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest[0]["template_slug"] == "status_04_chapter"
    assert manifest[0]["input"]["document_type"] == "status_report"
