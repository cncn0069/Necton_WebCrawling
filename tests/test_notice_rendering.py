from copy import deepcopy
from pathlib import Path

import fitz
import pytest

from rd2.generators.generated_document_pipeline import (
    build_notice_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.notice_rendering import (
    _missing_texts,
    build_notice_variation_specs,
    render_notice_variations,
)


_NOTICE_TYPES = {
    "bid_notice": "입찰공고",
    "bid_renotice": "입찰재공고",
    "pre_spec_notice": "사전규격공개",
    "public_offering": "공모",
    "notice": "공고",
}


def _notice_payload(document_type: str = "bid_notice") -> dict:
    return {
        "result": {
            "contract_version": "2.0.0",
            "source_classification": {
                "document_type": document_type,
                "classification": "S",
            },
            "generation_route": "fully_synthetic",
            "generated_document": {
                "contract_version": "2.0.0",
                "title": "공공시설 사용·수익허가 대상자 선정 공고",
                "agency_name": None,
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "n1",
                        "text": (
                            "공공시설의 안정적인 운영을 위하여 사용·수익허가 "
                            "대상자를 다음과 같이 선정합니다."
                        ),
                    },
                    {
                        "kind": "key_value",
                        "block_id": "n2",
                        "entries": [
                            {"key": "대상 시설", "value": "편의시설 1개소"},
                            {"key": "허가 기간", "value": "계약일부터 3년"},
                            {"key": "선정 방식", "value": "공개 경쟁"},
                            {"key": "접수 방법", "value": "방문 접수"},
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "n3",
                        "columns": ["구획", "면적", "예정금액", "용도"],
                        "rows": [
                            ["A동 1층", "48㎡", "연 12,500,000원", "편의시설"],
                            ["B동 별관", "32㎡", "연 8,300,000원", "지원시설"],
                        ],
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "n4",
                        "items": [
                            "신청서와 사업계획서를 함께 제출해야 합니다.",
                            "제출된 서류는 선정 절차 종료 후 반환하지 않습니다.",
                            "세부 조건은 붙임 자료를 확인하시기 바랍니다.",
                        ],
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "n5",
                        "attachment_id": "notice-appendix-001",
                        "label": "사용·수익허가 조건 1부",
                        "description": "신청 서식 및 세부 기준",
                    },
                ],
            },
        },
        "receipt": {
            "model_id": "gpt-4o",
            "response_id": "notice-response",
            "request_id": "notice-request",
        },
        "failure": None,
    }


def _normalized_pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "".join(
            "".join(page.get_text().split())
            for page in document
        )


def test_notice_context_preserves_order_and_does_not_invent_identity() -> None:
    envelope = parse_generation_payload(_notice_payload())
    context = build_notice_context(envelope, seed=101)

    assert context["document_type_label"] == "입찰공고"
    assert context["agency_name"] == ""
    assert [block["block_id"] for block in context["blocks"]] == [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
    ]
    assert context["blocks"][2]["column_count"] == 4
    assert context["signers"] == []
    assert context["administrative_events"] == []


def test_notice_variations_are_deterministic_and_structurally_distinct() -> None:
    first = build_notice_variation_specs(
        "notice_02_structured",
        count=3,
        base_seed=91,
    )
    second = build_notice_variation_specs(
        "notice_02_structured",
        count=3,
        base_seed=91,
    )

    assert first == second
    assert [(spec.key_value_columns, spec.list_columns) for spec in first] == [
        (2, 1),
        (3, 2),
        (1, 1),
    ]
    assert [spec.density for spec in first] == [
        "balanced",
        "compact",
        "airy",
    ]


def test_notice_renderer_rejects_invalid_selection_arguments(
    tmp_path: Path,
) -> None:
    context = build_notice_context(
        parse_generation_payload(_notice_payload()),
        seed=101,
    )

    with pytest.raises(ValueError, match="Unknown notice template"):
        build_notice_variation_specs("unknown-template")
    with pytest.raises(ValueError, match="count must be between 1 and 10"):
        build_notice_variation_specs(
            "notice_01_classic_gazette",
            count=0,
        )
    with pytest.raises(ValueError, match="per_template must be between 1 and 10"):
        render_notice_variations(context, tmp_path, per_template=11)
    with pytest.raises(ValueError, match="max_pages must be at least 1"):
        render_notice_variations(context, tmp_path, max_pages=0)
    with pytest.raises(ValueError, match="At least one notice template"):
        render_notice_variations(context, tmp_path, template_slugs=set())


@pytest.mark.parametrize(
    ("document_type", "expected_label"),
    _NOTICE_TYPES.items(),
)
def test_notice_taxonomy_types_route_to_notice_renderer(
    tmp_path: Path,
    document_type: str,
    expected_label: str,
) -> None:
    output_dir = tmp_path / document_type
    manifest = render_generation_payload(
        _notice_payload(document_type),
        output_dir,
        per_template=1,
        base_seed=20260730,
        template_slugs={"notice_01_classic_gazette"},
    )

    assert manifest[0]["renderer_family"] == "notice"
    assert manifest[0]["input"]["document_type"] == document_type
    assert manifest[0]["status"] == "ok"
    assert manifest[0]["identity"] == {
        "identity_source": "none",
        "agency_name": None,
    }
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")
    assert f">{expected_label}<" in html
    assert "가상" not in html


def test_notice_defaults_to_three_layouts_and_preserves_all_source_text(
    tmp_path: Path,
) -> None:
    payload = _notice_payload()
    envelope = parse_generation_payload(payload)
    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260730,
    )

    assert {
        entry["template_slug"] for entry in manifest
    } == {
        "notice_01_classic_gazette",
        "notice_02_structured",
        "notice_03_record_rail",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["source_text_present"] is True for entry in manifest)
    assert all(entry["actual_pages"] <= 10 for entry in manifest)

    source_atoms = source_text_atoms(envelope.result.generated_document)
    for entry in manifest:
        rendered_text = _normalized_pdf_text(Path(str(entry["pdf"])))
        assert all(
            "".join(atom.split()) in rendered_text
            for atom in source_atoms
        )
        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        for invented_text in (
            "입찰공고번호",
            "담당 부서",
            "공고일",
            "공고 기관장",
        ):
            assert invented_text not in html


def test_notice_preserves_input_agency_and_escapes_model_html(
    tmp_path: Path,
) -> None:
    payload = _notice_payload("notice")
    document = payload["result"]["generated_document"]
    document["agency_name"] = "입력기관명보존원"
    document["title"] = "가" * 40
    document["blocks"][0]["text"] = (
        '태그 <img src="file:///private/etc/passwd"> & 본문'
    )

    context = build_notice_context(
        parse_generation_payload(payload),
        seed=101,
    )
    assert context["title_class"] == "title-long"

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260730,
        template_slugs={"notice_02_structured"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert "입력기관명보존원" in html
    assert 'class="notice-title title-long"' in html
    assert '&lt;img src=&#34;file:///private/etc/passwd&#34;&gt;' in html
    assert '<img src="file:///private/etc/passwd">' not in html
    assert "&amp; 본문" in html
    assert manifest[0]["identity"] == {
        "identity_source": "input",
        "agency_name": "입력기관명보존원",
    }


def test_notice_renders_only_supplied_approval_and_administrative_event(
    tmp_path: Path,
) -> None:
    payload = _notice_payload("public_offering")
    document = payload["result"]["generated_document"]
    document["document_metadata"] = {
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
                        "seed": 4217,
                        "profile": "dry_ink",
                        "shape": "round",
                    },
                }
            ]
        },
        "administrative_events": [
            {
                "type": "review_deadline",
                "date": "2026-08-15",
                "text": "제출 자료 검토 결과는 지정된 절차에 따라 안내합니다.",
            }
        ],
    }

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260730,
        template_slugs={"notice_03_record_rail"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert "김가온" in html
    assert "2026-07-30" in html
    assert "2026-08-15" in html
    assert "제출 자료 검토 결과는 지정된 절차에 따라 안내합니다." in html
    assert "data:image/png;base64," in html


def test_notice_renderer_rejects_missing_source_text(
    tmp_path: Path,
) -> None:
    context = build_notice_context(
        parse_generation_payload(_notice_payload()),
        seed=101,
    )

    with pytest.raises(RuntimeError, match="missing source text"):
        render_notice_variations(
            context,
            tmp_path,
            per_template=1,
            base_seed=20260730,
            template_slugs={"notice_01_classic_gazette"},
            required_source_texts=("PDF에 존재하지 않는 원문",),
        )

    assert '"status": "rejected"' in (
        tmp_path / "manifest.json"
    ).read_text(encoding="utf-8")


def test_notice_renderer_requires_duplicate_source_occurrences(
    tmp_path: Path,
) -> None:
    envelope = parse_generation_payload(_notice_payload())
    context = build_notice_context(envelope, seed=101)
    paragraph_text = envelope.result.generated_document.blocks[0].text

    with pytest.raises(RuntimeError, match="missing source text"):
        render_notice_variations(
            context,
            tmp_path,
            per_template=1,
            base_seed=20260730,
            template_slugs={"notice_01_classic_gazette"},
            required_source_texts=(paragraph_text, paragraph_text),
        )


def test_notice_source_validation_does_not_reuse_overlapping_text() -> None:
    assert _missing_texts(("서울", "서울시"), "서울시") == ["서울"]
    assert _missing_texts(("서울", "서울시"), "서울서울시") == []


@pytest.mark.parametrize(
    ("length_name", "repeat_count"),
    (("short", 1), ("medium", 35), ("long", 150)),
)
def test_notice_layout_variations_handle_input_lengths(
    tmp_path: Path,
    length_name: str,
    repeat_count: int,
) -> None:
    payload = deepcopy(_notice_payload())
    document = payload["result"]["generated_document"]
    document["blocks"][0]["text"] = (
        "공고 입력 길이 검증을 위한 본문이며 주어진 내용을 그대로 배치합니다. "
        * repeat_count
    ).strip()
    if length_name == "long":
        document["blocks"][1]["entries"][0]["value"] = (
            "긴 항목 값도 잘리지 않고 한 열로 전환되어 이어져야 합니다. " * 10
        ).strip()
        document["blocks"][3]["items"][0] = (
            "긴 목록 항목 역시 다단 배치를 해제하고 원문 전체를 보존해야 합니다. "
            * 10
        ).strip()

    manifest = render_generation_payload(
        payload,
        tmp_path / length_name,
        per_template=3,
        base_seed=20260730,
        template_slugs={"notice_03_record_rail"},
    )

    assert len(manifest) == 3
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["source_text_present"] is True for entry in manifest)
    assert all(entry["actual_pages"] <= 10 for entry in manifest)
    if length_name == "long":
        assert all(
            entry["parameters"]["key_value_columns"] == 1
            and entry["parameters"]["list_columns"] == 1
            for entry in manifest
        )


def test_notice_rejects_oversized_input_before_pdf_render(
    tmp_path: Path,
) -> None:
    payload = _notice_payload()
    payload["result"]["generated_document"]["blocks"][0]["text"] = (
        "과대입력" * 10_001
    )

    with pytest.raises(ValueError, match="exceeds render budget"):
        render_generation_payload(
            payload,
            tmp_path,
            per_template=1,
            template_slugs={"notice_01_classic_gazette"},
        )

    assert not list(tmp_path.rglob("*.pdf"))


def test_notice_rejects_oversized_input_metadata_before_pdf_render(
    tmp_path: Path,
) -> None:
    payload = _notice_payload()
    payload["result"]["generation_target"] = {
        "untrusted_metadata": "메타데이터" * 7_000,
    }

    with pytest.raises(ValueError, match="input metadata exceeds"):
        render_generation_payload(
            payload,
            tmp_path,
            per_template=1,
            template_slugs={"notice_01_classic_gazette"},
        )

    assert not list(tmp_path.rglob("*.pdf"))
