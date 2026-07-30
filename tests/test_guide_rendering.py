import json
from pathlib import Path

import fitz
import pytest

from rd2.generators import guide_rendering
from rd2.generators.generated_document_pipeline import (
    build_guide_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.guide_rendering import (
    build_guide_variation_specs,
    render_guide_variations,
)


def _payload() -> dict:
    return {
        "result": {
            "contract_version": "1.0.0",
            "source_classification": {
                "document_type": "guide",
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generation_target": {
                "classification": "S",
                "clause_no": "5",
                "subclause_key": "audit_inspection",
                "generation_mode": "counterfactual",
            },
            "generated_document": {
                "contract_version": "1.0.0",
                "title": "공공시설 안전점검 및 운영 지침",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "g1",
                        "text": (
                            "이 지침은 공공시설의 정기점검과 운영 기록에 "
                            "필요한 공통 기준을 정합니다."
                        ),
                    },
                    {
                        "kind": "key_value",
                        "block_id": "g2",
                        "entries": [
                            {"key": "적용 대상", "value": "공공시설 운영부서"},
                            {"key": "점검 주기", "value": "분기별 1회"},
                            {
                                "key": "관리 부서",
                                "value": "시설안전 담당부서",
                            },
                        ],
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "g3",
                        "items": [
                            "점검 전에 시설 현황을 확인합니다.",
                            "현장 확인사항은 항목별로 기록합니다.",
                            "보완사항은 처리기한과 담당자를 기록합니다.",
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "g4",
                        "columns": ["구분", "확인 내용", "처리 기준"],
                        "rows": [
                            [
                                "시설 상태",
                                "구조물과 주요 설비의 이상 유무",
                                "이상 발견 시 사용 제한 검토",
                            ],
                            [
                                "운영 기록",
                                "점검일지 및 유지보수 이력",
                                "누락 자료를 보완하여 보관",
                            ],
                        ],
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "g5",
                        "attachment_id": "A-01",
                        "label": "공공시설 정기점검표",
                        "description": "시설별 확인 항목과 조치 결과 기록 양식",
                    },
                ],
                "body_text": (
                    "이 지침은 공공시설의 정기점검과 운영 기록에 "
                    "필요한 공통 기준을 정합니다.\n\n"
                    "적용 대상: 공공시설 운영부서\n"
                    "점검 주기: 분기별 1회\n"
                    "관리 부서: 시설안전 담당부서\n\n"
                    "- 점검 전에 시설 현황을 확인합니다.\n"
                    "- 현장 확인사항은 항목별로 기록합니다.\n"
                    "- 보완사항은 처리기한과 담당자를 기록합니다.\n\n"
                    "구분\t확인 내용\t처리 기준\n"
                    "시설 상태\t구조물과 주요 설비의 이상 유무\t"
                    "이상 발견 시 사용 제한 검토\n"
                    "운영 기록\t점검일지 및 유지보수 이력\t"
                    "누락 자료를 보완하여 보관\n\n"
                    "[첨부] 공공시설 정기점검표 (A-01): "
                    "시설별 확인 항목과 조치 결과 기록 양식"
                ),
            },
        },
        "receipt": {
            "stage": "pass1",
            "model_id": "gpt-4o",
            "response_id": "guide-response",
            "request_id": "guide-request",
        },
        "provenance": None,
        "failure": None,
    }


def test_context_uses_official_block_contract_without_inference() -> None:
    envelope = parse_generation_payload(_payload())
    context = build_guide_context(envelope, seed=800)

    assert [block["block_id"] for block in context["blocks"]] == [
        "g1",
        "g2",
        "g3",
        "g4",
        "g5",
    ]
    assert [block["kind"] for block in context["blocks"]] == [
        "paragraph",
        "key_value",
        "bullet_list",
        "table",
        "attachment_reference",
    ]
    assert context["agency_name"] == ""
    assert context["signers"] == []
    assert context["administrative_events"] == []
    serialized = json.dumps(context, ensure_ascii=False)
    for invented_label in ("제1장", "목차", "절차 1", "점검 결과 요약"):
        assert invented_label not in serialized


def test_arbitrary_block_order_is_preserved() -> None:
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

    context = build_guide_context(parse_generation_payload(payload), seed=801)

    assert [block["block_id"] for block in context["blocks"]] == [
        "g4",
        "g1",
        "g5",
        "g3",
        "g2",
    ]


def test_guide_routes_to_four_templates_and_preserves_text(
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
        "guide_01_classic",
        "guide_02_index",
        "guide_03_cards",
        "guide_04_field",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["renderer_family"] == "guide" for entry in manifest)
    assert all(
        entry["input"]["document_type"] == "guide"
        for entry in manifest
    )
    assert all(entry["actual_pages"] <= 10 for entry in manifest)

    required = source_text_atoms(envelope.result.generated_document)
    for entry in manifest:
        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        positions = [
            html.index(f'data-block-id="g{index}"')
            for index in range(1, 6)
        ]
        assert positions == sorted(positions)
        for invented_label in ("제1장", "목차", "절차 1", "점검 결과 요약"):
            assert invented_label not in html
        with fitz.open(str(entry["pdf"])) as pdf:
            rendered_text = "".join(
                "".join(page.get_text().split())
                for page in pdf
            )
        assert all(
            "".join(atom.split()) in rendered_text
            for atom in required
        )


def test_long_guide_flows_across_pages_without_losing_text(
    tmp_path: Path,
) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    repeated_text = (
        "시설 운영자는 정기점검 결과와 후속 조치의 완료 여부를 "
        "기록하고 관련 자료를 관리하여야 합니다. "
    )
    document["body_text"] = None
    document["blocks"] = [
        {
            "kind": "paragraph",
            "block_id": f"long-{index:02d}",
            "text": f"{index:02d} {repeated_text * 12}",
        }
        for index in range(1, 9)
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260730,
        template_slugs={"guide_01_classic"},
    )

    assert len(manifest) == 1
    assert 2 <= manifest[0]["actual_pages"] <= 10
    with fitz.open(str(manifest[0]["pdf"])) as pdf:
        rendered_text = "".join(
            "".join(page.get_text().split())
            for page in pdf
        )
    for index in range(1, 9):
        assert f"{index:02d}" in rendered_text


def test_variations_are_deterministic_and_change_density() -> None:
    first = build_guide_variation_specs(
        "guide_03_cards",
        count=3,
        base_seed=1234,
    )
    second = build_guide_variation_specs(
        "guide_03_cards",
        count=3,
        base_seed=1234,
    )

    assert first == second
    assert [spec.density for spec in first] == [
        "balanced",
        "compact",
        "airy",
    ]
    assert [spec.key_value_columns for spec in first] == [2, 3, 1]
    assert [spec.list_columns for spec in first] == [1, 2, 1]
    assert len({spec.horizontal_margin_mm for spec in first}) == 3


@pytest.mark.parametrize(
    "text_scale",
    [1, 4, 8],
    ids=["short", "medium", "long"],
)
def test_variations_preserve_text_across_input_lengths(
    tmp_path: Path,
    text_scale: int,
) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    sentence = (
        "담당자는 점검 결과와 후속 조치의 완료 여부를 확인하고 "
        "관련 자료를 운영 기록과 함께 관리합니다. "
    )
    document["body_text"] = None
    document["blocks"][0]["text"] = sentence * text_scale
    document["blocks"][1]["entries"] = [
        {
            "key": f"점검항목 {index}",
            "value": f"{index:02d} {sentence * text_scale}",
        }
        for index in range(1, 7)
    ]
    document["blocks"][2]["items"] = [
        f"{index:02d} {sentence * text_scale}"
        for index in range(1, 7)
    ]

    envelope = parse_generation_payload(payload)
    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=3,
        template_slugs={"guide_04_field"},
    )

    assert len(manifest) == 3
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(1 <= entry["actual_pages"] <= 10 for entry in manifest)
    assert all(entry["source_text_present"] for entry in manifest)
    if text_scale == 8:
        assert all(
            entry["parameters"]["key_value_columns"] == 1
            and entry["parameters"]["list_columns"] == 1
            for entry in manifest
        )
    # Long atoms may be interrupted by page metadata in PDF extraction.
    if text_scale < 8:
        required = source_text_atoms(envelope.result.generated_document)
        for entry in manifest:
            with fitz.open(str(entry["pdf"])) as pdf:
                rendered_text = "".join(
                    "".join(page.get_text().split())
                    for page in pdf
                )
            assert all(
                "".join(atom.split()) in rendered_text
                for atom in required
            )


def test_input_text_is_html_escaped(tmp_path: Path) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    document["blocks"][0]["text"] = (
        '<img src="file:///private/etc/passwd"> & 본문'
    )

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260730,
        template_slugs={"guide_01_classic"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert '&lt;img src=&#34;file:///private/etc/passwd&#34;&gt;' in html
    assert '<img src="file:///private/etc/passwd">' not in html
    assert "&amp; 본문" in html


@pytest.mark.parametrize("count", [0, 11])
def test_variation_count_rejects_out_of_range_values(count: int) -> None:
    with pytest.raises(ValueError, match="count must be between"):
        build_guide_variation_specs("guide_01_classic", count=count)


def test_variation_count_accepts_public_boundaries() -> None:
    assert len(
        build_guide_variation_specs("guide_01_classic", count=1)
    ) == 1
    assert len(
        build_guide_variation_specs("guide_01_classic", count=10)
    ) == 10


@pytest.mark.parametrize("per_template", [0, 11])
def test_renderer_rejects_invalid_per_template(
    tmp_path: Path,
    per_template: int,
) -> None:
    with pytest.raises(ValueError, match="per_template must be between"):
        render_guide_variations(
            {"title": "제목", "blocks": []},
            tmp_path / "output",
            per_template=per_template,
        )


def test_renderer_rejects_invalid_page_and_template_selection(
    tmp_path: Path,
) -> None:
    context = {"title": "제목", "blocks": []}
    with pytest.raises(ValueError, match="max_pages must be at least 1"):
        render_guide_variations(
            context,
            tmp_path / "pages",
            max_pages=0,
        )
    with pytest.raises(ValueError, match="At least one"):
        render_guide_variations(
            context,
            tmp_path / "empty",
            template_slugs=set(),
        )
    with pytest.raises(ValueError, match="Unknown guide template"):
        render_guide_variations(
            context,
            tmp_path / "unknown",
            template_slugs={"unknown"},
        )


def test_oversized_input_is_rejected_before_rendering(tmp_path: Path) -> None:
    output_dir = tmp_path / "oversized"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    with pytest.raises(ValueError, match="exceeds render budget"):
        render_guide_variations(
            {
                "title": "가" * 40_001,
                "blocks": [],
            },
            output_dir,
            max_pages=10,
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest[0]["status"] == "rejected"
    assert manifest[0]["artifact_published"] is False
    assert manifest[0]["reason"].startswith(
        "Guide input exceeds render budget:"
    )


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
            "collection_items=61>60",
        ),
        (
            [
                {
                    "kind": "table",
                    "block_id": "wide",
                    "columns": [""] * 21,
                    "rows": [],
                }
            ],
            "table_columns=21>20",
        ),
        (
            [
                {
                    "kind": "paragraph",
                    "block_id": "x" * 4_001,
                    "text": "",
                }
            ],
            "text_characters=4003>4000",
        ),
    ],
)
def test_render_budget_scales_with_page_limit(
    tmp_path: Path,
    blocks: list[dict],
    reason: str,
) -> None:
    output_dir = tmp_path / reason.split("=")[0]

    with pytest.raises(ValueError, match=reason):
        render_guide_variations(
            {"title": "제목", "blocks": blocks},
            output_dir,
            max_pages=1,
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))


def test_rejected_render_publishes_no_html_or_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = parse_generation_payload(_payload())
    context = build_guide_context(envelope, seed=802)
    output_dir = tmp_path / "rejected"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    monkeypatch.setattr(
        guide_rendering,
        "_inspect_pdf",
        lambda _: (11, ""),
    )

    with pytest.raises(RuntimeError, match="validation failed"):
        render_guide_variations(
            context,
            output_dir,
            per_template=10,
            template_slugs={"guide_01_classic"},
            required_source_texts=source_text_atoms(
                envelope.result.generated_document
            ),
            max_pages=10,
        )

    assert not list(output_dir.rglob("*.html"))
    assert not list(output_dir.rglob("*.pdf"))
    manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest) == 1
    assert manifest[0]["status"] == "rejected"
    assert manifest[0]["artifact_published"] is False
    assert manifest[0]["html"] is None
    assert manifest[0]["pdf"] is None
