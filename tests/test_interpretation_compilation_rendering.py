import json
from pathlib import Path

import fitz
import pytest

from rd2.generators import interpretation_compilation_rendering
from rd2.generators.generated_document_pipeline import (
    build_interpretation_compilation_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.interpretation_compilation_rendering import (
    build_interpretation_variation_specs,
    render_interpretation_compilation_variations,
)


def _payload() -> dict:
    return {
        "result": {
            "contract_version": "1.0.0",
            "source_classification": {
                "document_type": "interpretation_compilation",
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generation_target": {
                "classification": "S",
                "clause_no": "5",
                "subclause_key": "decision_review",
                "generation_mode": "counterfactual",
            },
            "generated_document": {
                "contract_version": "1.0.0",
                "title": "휴게시설 공동사용 시 설치 의무에 관한 검토",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "g1",
                        "text": (
                            "동일 건물에서 여러 사업장이 하나의 휴게시설을 "
                            "공동으로 사용하는 경우를 검토하였습니다."
                        ),
                    },
                    {
                        "kind": "key_value",
                        "block_id": "g2",
                        "entries": [
                            {"key": "문의 기관", "value": "한빛산업안전협회"},
                            {"key": "대상 시설", "value": "공동 휴게시설"},
                            {
                                "key": "검토 범위",
                                "value": "이용 가능성 및 설치·관리 기준",
                            },
                        ],
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "g3",
                        "text": (
                            "이용 인원에 적합한 면적과 냉난방, 환기 및 "
                            "접근성 기준을 충족하여야 합니다."
                        ),
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "g4",
                        "items": [
                            "근로자가 자유롭게 이용할 수 있을 것",
                            "사업장별 관리 책임을 명확히 정할 것",
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "g5",
                        "columns": ["구분", "검토 내용", "판단"],
                        "rows": [
                            ["이용 가능성", "이용시간 제한 없음", "적정"],
                            ["관리 책임", "담당자 지정", "보완 필요"],
                        ],
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "g6",
                        "attachment_id": "A-01",
                        "label": "휴게시설 설치·관리 기준",
                        "description": "공동사용 시설 점검표 포함",
                    },
                ],
                "body_text": (
                    "동일 건물에서 여러 사업장이 하나의 휴게시설을 "
                    "공동으로 사용하는 경우를 검토하였습니다.\n\n"
                    "문의 기관: 한빛산업안전협회\n"
                    "대상 시설: 공동 휴게시설\n"
                    "검토 범위: 이용 가능성 및 설치·관리 기준\n\n"
                    "이용 인원에 적합한 면적과 냉난방, 환기 및 "
                    "접근성 기준을 충족하여야 합니다.\n\n"
                    "- 근로자가 자유롭게 이용할 수 있을 것\n"
                    "- 사업장별 관리 책임을 명확히 정할 것\n\n"
                    "구분\t검토 내용\t판단\n"
                    "이용 가능성\t이용시간 제한 없음\t적정\n"
                    "관리 책임\t담당자 지정\t보완 필요\n\n"
                    "[첨부] 휴게시설 설치·관리 기준 (A-01): "
                    "공동사용 시설 점검표 포함"
                ),
            },
        },
        "receipt": {
            "stage": "pass1",
            "model_id": "gpt-4o",
            "response_id": "interpretation-response",
            "request_id": "interpretation-request",
        },
        "provenance": None,
        "failure": None,
    }


def test_context_uses_the_official_document_block_contract_without_inference() -> None:
    envelope = parse_generation_payload(_payload())
    context = build_interpretation_compilation_context(envelope, seed=700)

    assert [block["block_id"] for block in context["blocks"]] == [
        "g1",
        "g2",
        "g3",
        "g4",
        "g5",
        "g6",
    ]
    assert [block["kind"] for block in context["blocks"]] == [
        "paragraph",
        "key_value",
        "paragraph",
        "bullet_list",
        "table",
        "attachment_reference",
    ]
    assert context["agency_name"] == ""
    assert context["signers"] == []
    assert context["administrative_events"] == []
    serialized = json.dumps(context, ensure_ascii=False)
    for invented_label in ("질의요지", "회시요지", "답변내용", "CASE"):
        assert invented_label not in serialized


def test_arbitrary_block_order_is_preserved() -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["body_text"] = None
    document["blocks"] = [
        document["blocks"][4],
        document["blocks"][0],
        document["blocks"][5],
        document["blocks"][1],
        document["blocks"][3],
        document["blocks"][2],
    ]

    context = build_interpretation_compilation_context(
        parse_generation_payload(payload),
        seed=701,
    )

    assert [block["block_id"] for block in context["blocks"]] == [
        "g5",
        "g1",
        "g6",
        "g2",
        "g4",
        "g3",
    ]


def test_interpretation_document_routes_to_four_templates_and_preserves_text(
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
        "interpretation_01_sequence",
        "interpretation_02_index",
        "interpretation_03_cards",
        "interpretation_04_margin",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(
        entry["renderer_family"] == "interpretation_compilation"
        for entry in manifest
    )
    assert all(
        entry["input"]["document_type"] == "interpretation_compilation"
        for entry in manifest
    )
    assert all(entry["actual_pages"] <= 10 for entry in manifest)

    required = source_text_atoms(envelope.result.generated_document)
    for entry in manifest:
        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        positions = [html.index(f'data-block-id="g{index}"') for index in range(1, 7)]
        assert positions == sorted(positions)
        for invented_label in ("질의요지", "회시요지", "답변내용", "CASE"):
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


def test_variations_are_deterministic_and_change_density() -> None:
    first = build_interpretation_variation_specs(
        "interpretation_03_cards",
        count=3,
        base_seed=1234,
    )
    second = build_interpretation_variation_specs(
        "interpretation_03_cards",
        count=3,
        base_seed=1234,
    )

    assert first == second
    assert [spec.density for spec in first] == [
        "balanced",
        "compact",
        "airy",
    ]
    assert len({spec.horizontal_margin_mm for spec in first}) == 3


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
        template_slugs={"interpretation_01_sequence"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert '&lt;img src=&#34;file:///private/etc/passwd&#34;&gt;' in html
    assert '<img src="file:///private/etc/passwd">' not in html
    assert "&amp; 본문" in html


@pytest.mark.parametrize("count", [0, 11])
def test_variation_count_rejects_out_of_range_values(count: int) -> None:
    with pytest.raises(ValueError, match="count must be between"):
        build_interpretation_variation_specs(
            "interpretation_01_sequence",
            count=count,
        )


def test_variation_count_accepts_public_boundaries() -> None:
    assert len(
        build_interpretation_variation_specs(
            "interpretation_01_sequence",
            count=1,
        )
    ) == 1
    assert len(
        build_interpretation_variation_specs(
            "interpretation_01_sequence",
            count=10,
        )
    ) == 10


@pytest.mark.parametrize("per_template", [0, 11])
def test_renderer_rejects_invalid_per_template(
    tmp_path: Path,
    per_template: int,
) -> None:
    with pytest.raises(ValueError, match="per_template must be between"):
        render_interpretation_compilation_variations(
            {"title": "제목", "blocks": []},
            tmp_path / "output",
            per_template=per_template,
        )


def test_renderer_rejects_invalid_page_and_template_selection(
    tmp_path: Path,
) -> None:
    context = {"title": "제목", "blocks": []}
    with pytest.raises(ValueError, match="max_pages must be at least 1"):
        render_interpretation_compilation_variations(
            context,
            tmp_path / "pages",
            max_pages=0,
        )
    with pytest.raises(ValueError, match="At least one"):
        render_interpretation_compilation_variations(
            context,
            tmp_path / "empty",
            template_slugs=set(),
        )
    with pytest.raises(ValueError, match="Unknown interpretation template"):
        render_interpretation_compilation_variations(
            context,
            tmp_path / "unknown",
            template_slugs={"unknown"},
        )


def test_oversized_input_is_rejected_before_rendering(tmp_path: Path) -> None:
    output_dir = tmp_path / "oversized"

    with pytest.raises(ValueError, match="exceeds render budget"):
        render_interpretation_compilation_variations(
            {
                "title": "가" * 40_001,
                "blocks": [],
            },
            output_dir,
            max_pages=10,
        )

    assert not output_dir.exists()


def test_rejected_render_publishes_no_html_or_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = parse_generation_payload(_payload())
    context = build_interpretation_compilation_context(envelope, seed=702)
    output_dir = tmp_path / "rejected"
    output_dir.mkdir()
    (output_dir / "stale.pdf").write_bytes(b"stale")

    monkeypatch.setattr(
        interpretation_compilation_rendering,
        "_inspect_pdf",
        lambda _: (11, ""),
    )

    with pytest.raises(RuntimeError, match="validation failed"):
        render_interpretation_compilation_variations(
            context,
            output_dir,
            per_template=10,
            template_slugs={"interpretation_01_sequence"},
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
