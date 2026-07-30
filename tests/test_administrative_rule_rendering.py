from pathlib import Path

import fitz
import pytest

from rd2.generators.administrative_rule_rendering import (
    build_administrative_rule_variation_specs,
)
from rd2.generators.generated_document_pipeline import (
    build_administrative_rule_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)


def _payload(document_type: str = "directive") -> dict:
    return {
        "result": {
            "contract_version": "1.0.0",
            "source_classification": {
                "document_type": document_type,
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generated_document": {
                "contract_version": "1.0.0",
                "title": "위원회 운영 및 업무처리 규정 일부개정규정",
                "agency_name": None,
                "document_metadata": {
                    "administrative_events": [
                        {
                            "type": "effective_date",
                            "date": "2026-07-30",
                            "text": "이 규정은 발령한 날부터 시행한다.",
                        }
                    ]
                },
                "blocks": [
                    {
                        "kind": "key_value",
                        "block_id": "r1",
                        "entries": [
                            {
                                "key": "발령번호",
                                "value": "행정운영위원회 훈령 제2026-12호",
                            },
                            {"key": "발령일", "value": "2026년 7월 30일"},
                        ],
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "r2",
                        "text": (
                            "「위원회 운영 및 업무처리 규정」 일부를 "
                            "다음과 같이 개정한다."
                        ),
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "r3",
                        "text": "제1장 총칙",
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "r4",
                        "text": (
                            "제1조(목적) 이 규정은 위원회의 효율적인 운영과 "
                            "업무처리에 필요한 사항을 정함을 목적으로 한다."
                        ),
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "r5",
                        "items": [
                            "회의 안건은 개최일 3일 전까지 배포한다.",
                            "필요한 경우 관계 전문가의 의견을 들을 수 있다.",
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "r6",
                        "columns": ["구분", "보존기간"],
                        "rows": [["회의자료", "5년"], ["심의결과", "영구"]],
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "r7",
                        "text": "부칙",
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "r8",
                        "attachment_id": "rule-appendix-1",
                        "label": "신구조문 대비표 1부",
                        "description": "끝.",
                    },
                ],
            },
        },
        "receipt": {
            "request_id": f"administrative-{document_type}",
            "model_id": "gpt-4o",
        },
        "failure": None,
    }


def _pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "".join(
            "".join(page.get_text().split())
            for page in document
        )


def test_administrative_rule_context_preserves_block_order_and_styles() -> None:
    envelope = parse_generation_payload(_payload())
    context = build_administrative_rule_context(envelope, seed=100)

    assert context["document_type_label"] == "훈령"
    assert context["agency_name"] == ""
    assert [block["block_id"] for block in context["blocks"]] == [
        "r1",
        "r2",
        "r3",
        "r4",
        "r5",
        "r6",
        "r7",
        "r8",
    ]
    assert context["blocks"][2]["style_class"] == "is-chapter"
    assert context["blocks"][3]["style_class"] == "is-article"
    assert context["blocks"][3]["label"] == "제1조(목적)"
    assert context["blocks"][6]["style_class"] == "is-supplement"


def test_three_taxonomy_codes_share_renderer_without_invented_identity(
    tmp_path: Path,
) -> None:
    expected_labels = {
        "directive": "훈령",
        "regulation": "예규",
        "notification": "고시",
    }
    for document_type, label in expected_labels.items():
        output_dir = tmp_path / document_type
        manifest = render_generation_payload(
            _payload(document_type),
            output_dir,
            template_slugs={"rule_01_promulgation"},
        )

        assert manifest[0]["renderer_family"] == "administrative_rule"
        assert manifest[0]["input"]["document_type"] == document_type
        html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")
        assert f'<div class="rule-type">{label}</div>' in html
        assert "대한민국정부" not in html
        assert "행정운영위원회 위원장" not in html


def test_all_four_rule_layouts_render_every_source_block(tmp_path: Path) -> None:
    payload = _payload()
    envelope = parse_generation_payload(payload)
    manifest = render_generation_payload(payload, tmp_path)

    assert len(manifest) == 4
    assert {
        entry["template_slug"] for entry in manifest
    } == {
        "rule_01_promulgation",
        "rule_02_article_rail",
        "rule_03_gazette_columns",
        "rule_04_notice_frame",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["actual_pages"] <= 10 for entry in manifest)
    for entry in manifest:
        rendered_text = _pdf_text(Path(str(entry["pdf"])))
        assert all(
            "".join(atom.split()) in rendered_text
            for atom in source_text_atoms(envelope.result.generated_document)
        )
        assert "위원회운영및업무처리규정일부개정규정" in rendered_text


def test_rule_variations_are_deterministic_and_support_three_densities(
    tmp_path: Path,
) -> None:
    first = build_administrative_rule_variation_specs(
        "rule_02_article_rail",
        count=3,
        base_seed=77,
    )
    second = build_administrative_rule_variation_specs(
        "rule_02_article_rail",
        count=3,
        base_seed=77,
    )
    assert first == second
    assert [spec.density for spec in first] == [
        "balanced",
        "compact",
        "airy",
    ]

    manifest = render_generation_payload(
        _payload("regulation"),
        tmp_path,
        per_template=3,
        template_slugs={"rule_02_article_rail"},
    )
    assert [entry["variation_slug"] for entry in manifest] == [
        "01_balanced",
        "02_compact",
        "03_airy",
    ]


def test_administrative_rule_rejects_wrong_template_family(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Unknown administrative rule template"):
        render_generation_payload(
            _payload(),
            tmp_path,
            template_slugs={"01_classic_municipal"},
        )
