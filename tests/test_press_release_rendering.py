import json
from pathlib import Path

import fitz
import pytest
from pydantic import ValidationError

import rd2.generators.press_release_rendering as press_renderer
from rd2.generators.generated_document_pipeline import (
    build_press_release_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.press_release_rendering import (
    PRESS_RELEASE_URL_FETCHER,
    build_press_release_variation_specs,
    render_press_release_variations,
)
from scripts.render_generated_documents import render_input_file


def _press_payload() -> dict:
    return {
        "result": {
            "contract_version": "2.0.0",
            "source_classification": {
                "document_type": "press_release",
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generated_document": {
                "contract_version": "2.0.0",
                "title": "지역 문화공간 안전관리 지원 확대",
                "agency_name": None,
                "document_metadata": {
                    "administrative_events": [
                        {
                            "type": "release_schedule",
                            "date": "2026-08-03",
                            "text": "지정된 보도시점 이후 사용할 수 있습니다.",
                        }
                    ]
                },
                "blocks": [
                    {
                        "kind": "key_value",
                        "block_id": "p1",
                        "entries": [
                            {
                                "key": "보도시점",
                                "value": "2026. 8. 3.(월) 09:00",
                            },
                            {
                                "key": "배포",
                                "value": "2026. 8. 2.(일)",
                            },
                        ],
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "p2",
                        "text": (
                            "현장 점검과 개선 지원을 연계해 "
                            "이용자 안전을 강화합니다."
                        ),
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "p3",
                        "items": [
                            "지역 문화공간 120개소를 대상으로 안전 점검 실시",
                            "시설별 개선계획과 현장 자문을 연계해 후속 조치 지원",
                            "점검 결과를 향후 지원사업 운영 기준에 반영",
                        ],
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "p4",
                        "text": (
                            "첫번째본문은 지역 문화공간의 안전한 운영을 위한 "
                            "현장 점검 계획을 설명합니다."
                        ),
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "p5",
                        "text": (
                            "두번째본문은 시설 여건을 고려한 개선 권고와 "
                            "후속 지원 방식을 설명합니다."
                        ),
                    },
                    {
                        "kind": "table",
                        "block_id": "p6",
                        "columns": [
                            "구분",
                            "대상",
                            "기간",
                            "권역",
                            "점검",
                            "자문",
                            "후속",
                            "비고",
                        ],
                        "rows": [
                            [
                                "안전관리",
                                "문화공간",
                                "8월",
                                "전국",
                                "현장",
                                "전문가",
                                "개선",
                                "계속",
                            ]
                        ],
                    },
                    {
                        "kind": "key_value",
                        "block_id": "p7",
                        "entries": [
                            {"key": "담당 부서", "value": "지역문화지원실"},
                            {
                                "key": "담당자",
                                "value": "주무관 이도담 (02-1234-5679)",
                            },
                        ],
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "p8",
                        "attachment_id": "press-attachment-001",
                        "label": "지역 문화공간 안전관리 세부계획 1부",
                        "description": "끝.",
                    },
                ],
                "body_text": (
                    "보도시점: 2026. 8. 3.(월) 09:00\n"
                    "배포: 2026. 8. 2.(일)\n\n"
                    "현장 점검과 개선 지원을 연계해 "
                    "이용자 안전을 강화합니다.\n\n"
                    "- 지역 문화공간 120개소를 대상으로 안전 점검 실시\n"
                    "- 시설별 개선계획과 현장 자문을 연계해 후속 조치 지원\n"
                    "- 점검 결과를 향후 지원사업 운영 기준에 반영\n\n"
                    "첫번째본문은 지역 문화공간의 안전한 운영을 위한 "
                    "현장 점검 계획을 설명합니다.\n\n"
                    "두번째본문은 시설 여건을 고려한 개선 권고와 "
                    "후속 지원 방식을 설명합니다.\n\n"
                    "구분\t대상\t기간\t권역\t점검\t자문\t후속\t비고\n"
                    "안전관리\t문화공간\t8월\t전국\t현장\t전문가\t개선\t계속\n\n"
                    "담당 부서: 지역문화지원실\n"
                    "담당자: 주무관 이도담 (02-1234-5679)\n\n"
                    "[첨부] 지역 문화공간 안전관리 세부계획 1부 "
                    "(press-attachment-001): 끝."
                ),
            },
        },
        "receipt": {
            "model_id": "gpt-4o",
            "response_id": "press-response",
            "request_id": "press-request",
        },
        "failure": None,
    }


def _normalized_pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "".join(
            "".join(page.get_text().split())
            for page in document
        )


def test_press_context_preserves_order_and_uses_only_input_identity() -> None:
    envelope = parse_generation_payload(_press_payload())
    context = build_press_release_context(envelope, seed=100)

    assert context["agency_name"] == ""
    assert context["header_meta"]["block_id"] == "p1"
    assert context["header_meta"]["entries"][0]["key"] == "보도시점"
    assert context["render_items"][0]["block_id"] == "p2"
    assert context["render_items"][0]["is_lead"] is True
    assert context["render_items"][1]["block_id"] == "p3"
    assert context["render_items"][1]["is_summary"] is True
    assert context["render_items"][2]["kind"] == "paragraph_group"
    assert [
        block["block_id"] for block in context["render_items"][2]["blocks"]
    ] == ["p4", "p5"]
    assert context["render_items"][3]["block_id"] == "p6"
    assert context["render_items"][3]["is_wide"] is True
    assert context["render_items"][4]["block_id"] == "p7"
    assert context["render_items"][4]["is_footer_details"] is True
    assert context["render_items"][5]["block_id"] == "p8"


def test_press_variations_are_deterministic_and_structural() -> None:
    first = build_press_release_variation_specs(
        "press_03_joint_modular",
        count=3,
        base_seed=77,
    )
    second = build_press_release_variation_specs(
        "press_03_joint_modular",
        count=3,
        base_seed=77,
    )

    assert first == second
    assert [
        (
            spec.meta_columns,
            spec.summary_columns,
            spec.body_columns,
            spec.contact_columns,
        )
        for spec in first
    ] == [
        (2, 1, 2, 2),
        (2, 2, 2, 1),
        (1, 1, 1, 2),
    ]
    assert len({spec.horizontal_margin_mm for spec in first}) == 3


def test_press_renderer_rejects_invalid_arguments(tmp_path: Path) -> None:
    context = build_press_release_context(
        parse_generation_payload(_press_payload()),
        seed=100,
    )

    with pytest.raises(ValueError, match="Unknown press release template"):
        build_press_release_variation_specs("unknown")
    with pytest.raises(ValueError, match="count must be at least 1"):
        build_press_release_variation_specs(
            "press_01_government_standard",
            count=0,
        )
    with pytest.raises(ValueError, match="count must be at most 10"):
        build_press_release_variation_specs(
            "press_01_government_standard",
            count=11,
        )
    with pytest.raises(ValueError, match="per_template must be at least 1"):
        render_press_release_variations(
            context,
            tmp_path,
            per_template=0,
        )
    with pytest.raises(ValueError, match="per_template must be at most 10"):
        render_press_release_variations(
            context,
            tmp_path,
            per_template=11,
        )
    with pytest.raises(ValueError, match="At least one press release"):
        render_press_release_variations(
            context,
            tmp_path,
            template_slugs=set(),
        )


def test_document_type_is_closed_and_press_release_routes_to_three_templates(
    tmp_path: Path,
) -> None:
    payload = _press_payload()
    payload["result"]["source_classification"]["document_type"] = (
        "press_release "
    )
    with pytest.raises(ValidationError):
        parse_generation_payload(payload)

    payload = _press_payload()
    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260730,
    )

    assert len(manifest) == 3
    assert {
        entry["template_slug"] for entry in manifest
    } == {
        "press_01_government_standard",
        "press_02_briefing_focus",
        "press_03_joint_modular",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["renderer_family"] == "press_release" for entry in manifest)
    assert all(
        entry["input"]["document_type"] == "press_release"
        for entry in manifest
    )
    assert all(entry["identity"]["identity_source"] == "none" for entry in manifest)
    assert all(entry["actual_pages"] <= 10 for entry in manifest)

    expected_order = (
        "보도시점",
        "지역 문화공간 안전관리 지원 확대",
        "현장 점검과 개선 지원",
        "지역 문화공간 120개소",
        "첫번째본문",
        "두번째본문",
        "구분",
        "담당 부서",
        "지역 문화공간 안전관리 세부계획",
    )
    document = parse_generation_payload(payload).result.generated_document
    for entry in manifest:
        pdf_path = Path(str(entry["pdf"]))
        rendered_text = _normalized_pdf_text(pdf_path)
        positions = [
            rendered_text.index("".join(token.split()))
            for token in expected_order
        ]
        assert positions == sorted(positions)
        assert all(
            "".join(atom.split()) in rendered_text
            for atom in source_text_atoms(document)
        )
        assert "2026-08-03" in rendered_text
        with fitz.open(pdf_path) as pdf:
            assert any(page.rect.width > page.rect.height for page in pdf)

        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        for invented_text in (
            "대한민국정부",
            "문화체육관광부",
            "보도담당관",
            "공공정책지원원",
        ):
            assert invented_text not in html


def test_missing_source_classification_keeps_legacy_official_routing(
    tmp_path: Path,
) -> None:
    payload = _press_payload()
    payload["result"].pop("source_classification")

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        template_slugs={"01_classic_municipal"},
    )

    assert len(manifest) == 1
    assert manifest[0]["renderer_family"] == "official_document"
    assert manifest[0]["input"]["document_type"] is None


def test_press_renderer_accepts_document_without_paragraph_or_list(
    tmp_path: Path,
) -> None:
    payload = _press_payload()
    document = payload["result"]["generated_document"]
    document["title"] = "장문보도자료제목" * 10
    document["blocks"] = [
        {
            "kind": "table",
            "block_id": "minimal-table",
            "columns": ["구분", "내용"],
            "rows": [["일정", "2026년 8월"]],
        }
    ]
    document.pop("body_text")

    envelope = parse_generation_payload(payload)
    context = build_press_release_context(envelope, seed=100)
    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        template_slugs={"press_01_government_standard"},
    )

    assert context["title_class"] == "title-extra-long"
    assert context["header_meta"] is None
    assert all(
        not item.get("is_lead") and not item.get("is_summary")
        for item in context["render_items"]
    )
    assert manifest[0]["status"] == "ok"


def test_press_renderer_preserves_explicit_agency_and_omits_absent_regions(
    tmp_path: Path,
) -> None:
    payload = _press_payload()
    document = payload["result"]["generated_document"]
    document["agency_name"] = "입력기관명보존원"
    document.pop("document_metadata")
    document["blocks"] = document["blocks"][1:6]
    document.pop("body_text")

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        template_slugs={"press_02_briefing_focus"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert "입력기관명보존원" in html
    assert "release-meta" not in html
    assert "is-footer-details" not in html
    assert "administrative-events" not in html
    assert "press-approval" not in html
    assert manifest[0]["identity"] == {
        "identity_source": "input",
        "agency_name": "입력기관명보존원",
    }


def test_press_renderer_uses_only_explicit_approval_metadata(
    tmp_path: Path,
) -> None:
    payload = _press_payload()
    metadata = payload["result"]["generated_document"]["document_metadata"]
    metadata["approval_line"] = {
        "slots": [
            {
                "role": "배포책임",
                "name": "김보도",
                "status": "approved",
                "approved_at": "2026-08-02",
                "stamp": {
                    "mode": "synthetic",
                    "stamp_text": "김보도인",
                    "seed": 8602,
                    "profile": "dry_ink",
                    "shape": "square",
                },
            }
        ]
    }

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        template_slugs={"press_03_joint_modular"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")
    rendered_text = _normalized_pdf_text(Path(str(manifest[0]["pdf"])))

    assert "press-approval" in html
    assert "배포책임" in rendered_text
    assert "김보도" in rendered_text
    assert "2026-08-02" in rendered_text
    assert "data:image/png;base64," in html
    assert manifest[0]["approval"][0]["stamp"]["parameters"]["seed"] == 8602


def test_press_template_escapes_model_html(tmp_path: Path) -> None:
    payload = _press_payload()
    document = payload["result"]["generated_document"]
    document["blocks"][1]["text"] = (
        '태그 <img src="https://example.invalid/a.png"> & 본문'
    )
    document.pop("body_text")

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        template_slugs={"press_01_government_standard"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert '&lt;img src=&#34;https://example.invalid/a.png&#34;&gt;' in html
    assert '<img src="https://example.invalid/a.png">' not in html
    assert "&amp; 본문" in html


def test_press_renderer_makes_three_variations_per_template(
    tmp_path: Path,
) -> None:
    manifest = render_generation_payload(
        _press_payload(),
        tmp_path,
        per_template=3,
        base_seed=20260730,
        template_slugs={"press_03_joint_modular"},
    )

    assert len(manifest) == 3
    assert [entry["variation_slug"] for entry in manifest] == [
        "01_balanced",
        "02_compact",
        "03_airy",
    ]
    assert [entry["parameters"]["body_columns"] for entry in manifest] == [
        2,
        2,
        1,
    ]
    assert all(entry["status"] == "ok" for entry in manifest)

    briefing_manifest = render_generation_payload(
        _press_payload(),
        tmp_path / "briefing",
        per_template=3,
        base_seed=20260730,
        template_slugs={"press_02_briefing_focus"},
    )
    assert len(briefing_manifest) == 3
    assert all(entry["status"] == "ok" for entry in briefing_manifest)


def test_press_renderer_rejects_page_overflow_and_oversized_input(
    tmp_path: Path,
) -> None:
    envelope = parse_generation_payload(_press_payload())
    context = build_press_release_context(envelope, seed=100)

    with pytest.raises(RuntimeError, match="maximum 1 pages"):
        render_press_release_variations(
            context,
            tmp_path / "overflow",
            per_template=1,
            template_slugs={"press_01_government_standard"},
            required_source_texts=source_text_atoms(
                envelope.result.generated_document
            ),
            max_pages=1,
        )
    assert not list((tmp_path / "overflow").rglob("*.pdf"))
    assert not list((tmp_path / "overflow").rglob("*.html"))
    overflow_manifest = json.loads(
        (tmp_path / "overflow" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert overflow_manifest[0]["status"] == "rejected"
    assert overflow_manifest[0]["artifact_published"] is False
    assert overflow_manifest[0]["pdf"] is None

    payload = _press_payload()
    document = payload["result"]["generated_document"]
    document["blocks"][1]["text"] = "과대입력" * 10_001
    document.pop("body_text")
    with pytest.raises(ValueError, match="exceeds render budget"):
        render_generation_payload(
            payload,
            tmp_path / "oversized",
            per_template=1,
            template_slugs={"press_01_government_standard"},
        )
    assert not list((tmp_path / "oversized").rglob("*.pdf"))


def test_press_renderer_rejects_each_budget_dimension_before_pdf(
    tmp_path: Path,
) -> None:
    def fresh_context() -> dict:
        return build_press_release_context(
            parse_generation_payload(_press_payload()),
            seed=100,
        )

    cases = []

    title_context = fresh_context()
    title_context["title"] = "제" * 301
    cases.append((title_context, "title exceeds 300 characters"))

    block_context = fresh_context()
    block_context["render_items"] = [
        {"kind": "paragraph", "block_id": str(index), "text": "본문"}
        for index in range(161)
    ]
    cases.append((block_context, "supports at most 160 blocks"))

    list_context = fresh_context()
    list_context["render_items"] = [
        {
            "kind": "bullet_list",
            "block_id": "too-many-items",
            "items": ["항목"] * 601,
        }
    ]
    cases.append((list_context, "601 list items, maximum 600"))

    row_context = fresh_context()
    row_context["render_items"] = [
        {
            "kind": "table",
            "block_id": "too-many-rows",
            "columns": ["열"],
            "rows": [["값"]] * 501,
        }
    ]
    cases.append((row_context, "501 table rows, maximum 500"))

    cell_context = fresh_context()
    columns = [f"열{index}" for index in range(1251)]
    cell_context["render_items"] = [
        {
            "kind": "table",
            "block_id": "too-many-cells",
            "columns": columns,
            "rows": [["값"] * len(columns)],
        }
    ]
    cases.append((cell_context, "2502 table cells, maximum 2500"))

    block_id_context = fresh_context()
    block_id_context["render_items"][0]["block_id"] = "b" * 40_001
    cases.append((block_id_context, "characters, maximum 40000"))

    signer_context = fresh_context()
    signer_context["signers"] = [
        {
            "role": "결" * 40_001,
            "name": "",
            "date": "",
            "status": "approved",
            "stamp_alt": "",
        }
    ]
    cases.append((signer_context, "characters, maximum 40000"))

    for index, (context, message) in enumerate(cases):
        case_output = tmp_path / str(index)
        with pytest.raises(ValueError, match=message):
            render_press_release_variations(
                context,
                case_output,
                per_template=1,
                template_slugs={"press_01_government_standard"},
            )
        assert not list(case_output.rglob("*.pdf"))
    assert not list(tmp_path.rglob("*.pdf"))


def test_press_renderer_replaces_output_as_one_generation(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "same-output"
    context = build_press_release_context(
        parse_generation_payload(_press_payload()),
        seed=100,
    )

    first = render_press_release_variations(
        context,
        output_dir,
        per_template=3,
        template_slugs={"press_01_government_standard"},
    )
    assert len(first) == 3
    assert len(list(output_dir.rglob("*.pdf"))) == 3
    assert len(list(output_dir.rglob("*.html"))) == 3

    second = render_press_release_variations(
        context,
        output_dir,
        per_template=1,
        template_slugs={"press_02_briefing_focus"},
    )
    assert len(second) == 1
    assert {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
    } == {
        "manifest.json",
        "press_02_briefing_focus/01_balanced.html",
        "press_02_briefing_focus/01_balanced.pdf",
    }

    with pytest.raises(RuntimeError, match="sha256="):
        render_press_release_variations(
            context,
            output_dir,
            per_template=1,
            template_slugs={"press_01_government_standard"},
            required_source_texts=("출력에 없는 검증 문장",),
        )
    assert {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
    } == {"manifest.json"}
    failed_manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert failed_manifest[0]["status"] == "rejected"
    assert failed_manifest[0]["artifact_published"] is False


def test_restricted_fetcher_rejects_network_and_outside_files() -> None:
    with pytest.raises(ValueError):
        PRESS_RELEASE_URL_FETCHER.fetch("https://example.invalid/asset.css")
    with pytest.raises(ValueError, match="outside the renderer asset roots"):
        PRESS_RELEASE_URL_FETCHER.fetch(Path("/etc/passwd").as_uri())


def test_press_renderer_rejects_excessive_page_cost_before_pdf(
    tmp_path: Path,
) -> None:
    context = build_press_release_context(
        parse_generation_payload(_press_payload()),
        seed=100,
    )
    context["render_items"] = [
        {
            "kind": "paragraph",
            "block_id": "large-but-under-character-limit",
            "text": "가" * 36_000,
        }
    ]

    with pytest.raises(ValueError, match="pre-render page-cost estimate"):
        render_press_release_variations(
            context,
            tmp_path,
            per_template=1,
            template_slugs={"press_01_government_standard"},
        )
    assert not list(tmp_path.rglob("*.pdf"))


def test_press_renderer_records_hashed_missing_source_failure(
    tmp_path: Path,
) -> None:
    context = build_press_release_context(
        parse_generation_payload(_press_payload()),
        seed=100,
    )
    missing_text = "PDF에 존재하면 안 되는 검증 전용 문장"

    with pytest.raises(RuntimeError, match="sha256=") as exc_info:
        render_press_release_variations(
            context,
            tmp_path,
            per_template=1,
            template_slugs={"press_01_government_standard"},
            required_source_texts=(missing_text,),
        )

    assert missing_text not in str(exc_info.value)
    manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest[0]["status"] == "rejected"
    assert manifest[0]["artifact_published"] is False
    assert manifest[0]["source_text_present"] is False
    assert manifest[0]["pdf"] is None
    assert not list(tmp_path.rglob("*.pdf"))


def test_rejected_manifest_removes_source_identity_and_metadata(
    tmp_path: Path,
) -> None:
    payload = _press_payload()
    payload["result"]["generated_document"]["agency_name"] = (
        "SENSITIVE_AGENCY"
    )
    payload["result"]["generation_target"] = {
        "classification": "S",
        "private_note": "SENSITIVE_TARGET",
    }
    payload["receipt"]["request_id"] = "SENSITIVE_REQUEST"
    context = build_press_release_context(
        parse_generation_payload(payload),
        seed=100,
    )
    context["approval_manifest"] = [
        {"name": "SENSITIVE_SIGNER", "stamp_text": "SENSITIVE_STAMP"}
    ]

    with pytest.raises(RuntimeError):
        render_press_release_variations(
            context,
            tmp_path,
            template_slugs={"press_01_government_standard"},
            required_source_texts=("렌더 결과에 없는 검증값",),
            input_metadata={
                "contract_version": "2.0.0",
                "document_type": "press_release",
                "renderer_family": "press_release",
                "generation_route": "fully_synthetic",
                "generation_target": {"private_note": "SENSITIVE_TARGET"},
                "request_id": "SENSITIVE_REQUEST",
                "content_sha256": "0" * 64,
            },
        )

    manifest_text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert "SENSITIVE" not in manifest_text
    manifest = json.loads(manifest_text)
    assert "identity" not in manifest[0]
    assert "approval" not in manifest[0]
    assert "generation_target" not in manifest[0]["input"]
    assert "request_id" not in manifest[0]["input"]


def test_source_validation_requires_duplicate_occurrences(
    tmp_path: Path,
) -> None:
    context = build_press_release_context(
        parse_generation_payload(_press_payload()),
        seed=100,
    )
    repeated = "중복 원문 검증을 위한 고유 문장"
    context["render_items"] = [
        {
            "kind": "paragraph",
            "block_id": "only-once",
            "text": repeated,
            "is_lead": False,
        }
    ]

    with pytest.raises(RuntimeError, match="count=1"):
        render_press_release_variations(
            context,
            tmp_path,
            template_slugs={"press_01_government_standard"},
            required_source_texts=(repeated, repeated),
        )
    assert not list(tmp_path.rglob("*.pdf"))


def test_batch_cli_function_routes_press_release(tmp_path: Path) -> None:
    input_path = tmp_path / "press-input.json"
    input_path.write_text(
        json.dumps(_press_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    batch = render_input_file(
        input_path,
        tmp_path / "output",
        per_template=1,
        base_seed=20260730,
        template_slugs={"press_02_briefing_focus"},
    )

    assert batch[0]["status"] == "ok"
    assert batch[0]["render_count"] == 1
    manifest = json.loads(
        (
            Path(str(batch[0]["output_dir"])) / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest[0]["template_slug"] == "press_02_briefing_focus"
    assert manifest[0]["input"]["document_type"] == "press_release"


def test_batch_continues_after_invalid_document_and_rejects_wrong_family(
    tmp_path: Path,
) -> None:
    invalid = _press_payload()
    invalid["receipt"]["request_id"] = "invalid-press"
    invalid["result"]["source_classification"]["document_type"] = "unknown"
    valid = _press_payload()
    valid["receipt"]["request_id"] = "valid-press"
    input_path = tmp_path / "mixed.json"
    input_path.write_text(
        json.dumps([invalid, valid], ensure_ascii=False),
        encoding="utf-8",
    )

    batch = render_input_file(
        input_path,
        tmp_path / "mixed-output",
        per_template=1,
        template_slugs={"press_01_government_standard"},
    )

    assert [entry["status"] for entry in batch] == ["rejected", "ok"]
    assert Path(str(batch[1]["output_dir"])).name == "valid-press"

    wrong_family_path = tmp_path / "wrong-family.json"
    wrong_family_path.write_text(
        json.dumps(valid, ensure_ascii=False),
        encoding="utf-8",
    )
    wrong_family = render_input_file(
        wrong_family_path,
        tmp_path / "wrong-family-output",
        per_template=1,
        template_slugs={"01_classic_municipal"},
    )
    assert wrong_family[0]["status"] == "rejected"
