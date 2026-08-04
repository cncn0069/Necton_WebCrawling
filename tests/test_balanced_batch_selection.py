from collections import Counter, defaultdict
import json
from pathlib import Path

import pytest

from rd2.generators.administrative_rule_rendering import (
    build_administrative_rule_variation_specs,
)
from rd2.generators.balanced_batch_selection import (
    BalancedTemplateSelector,
    template_slugs_for_document_type,
)
from rd2.generators.guide_rendering import build_guide_variation_specs
from rd2.generators.interpretation_compilation_rendering import (
    build_interpretation_variation_specs,
)
from rd2.generators.meeting_minutes_rendering import (
    build_meeting_minutes_variation_specs,
)
from rd2.generators.notice_rendering import build_notice_variation_specs
from rd2.generators.official_document_variations import build_variation_specs
from rd2.generators.paged_output import RenderedSourceTextError
from rd2.generators.press_release_rendering import (
    build_press_release_variation_specs,
)
from rd2.generators.research_report_rendering import (
    build_research_variation_specs,
)
from rd2.generators.status_report_rendering import (
    build_status_report_variation_specs,
)
from scripts import render_generated_documents as render_cli


@pytest.mark.parametrize(
    ("document_type", "expected_prefix", "expected_count"),
    (
        ("research_report", "research_", 3),
        ("press_release", "press_", 3),
        ("directive", "rule_", 4),
        ("regulation", "rule_", 4),
        ("notification", "rule_", 4),
        ("interpretation_compilation", "interpretation_", 4),
        ("guide", "guide_", 4),
        ("status_report", "status_", 4),
        ("meeting_minutes", "meeting_", 4),
        ("bid_notice", "notice_", 3),
        ("notice", "notice_", 3),
        ("policy_material", "01_", 10),
    ),
)
def test_document_type_uses_matching_template_family(
    document_type: str,
    expected_prefix: str,
    expected_count: int,
) -> None:
    slugs = template_slugs_for_document_type(document_type)

    assert len(slugs) == expected_count
    assert slugs[0].startswith(expected_prefix)


@pytest.mark.parametrize(
    ("builder", "template_slug"),
    (
        (build_variation_specs, "01_classic_municipal"),
        (build_research_variation_specs, "research_01_classic_flow"),
        (
            build_press_release_variation_specs,
            "press_01_government_standard",
        ),
        (build_administrative_rule_variation_specs, "rule_01_promulgation"),
        (build_interpretation_variation_specs, "interpretation_01_sequence"),
        (build_guide_variation_specs, "guide_01_classic"),
        (build_status_report_variation_specs, "status_01_brief"),
        (build_meeting_minutes_variation_specs, "meeting_01_registry"),
        (build_notice_variation_specs, "notice_01_classic_gazette"),
    ),
)
def test_renderer_can_start_from_selected_structural_variation(
    builder: object,
    template_slug: str,
) -> None:
    specs = builder(template_slug, count=1, start_offset=2)

    assert len(specs) == 1
    assert specs[0].index == 3


def test_twenty_thousand_assignments_are_balanced_and_reproducible() -> None:
    selector = BalancedTemplateSelector(seed=7719, variation_count=3)
    assignments = [
        selector.select("policy_material", item_key=f"item-{index}")
        for index in range(20_000)
    ]

    template_counts = Counter(
        assignment.template_slug for assignment in assignments
    )
    assert max(template_counts.values()) - min(template_counts.values()) <= 1

    variation_counts: dict[str, Counter[int]] = defaultdict(Counter)
    for assignment in assignments:
        variation_counts[assignment.template_slug][
            assignment.variation_index
        ] += 1
    for counts in variation_counts.values():
        assert set(counts) == {1, 2, 3}
        assert max(counts.values()) - min(counts.values()) <= 1

    second_selector = BalancedTemplateSelector(seed=7719, variation_count=3)
    repeated = [
        second_selector.select("policy_material", item_key=f"item-{index}")
        for index in range(100)
    ]
    assert assignments[:100] == repeated


def test_document_types_are_balanced_independently() -> None:
    selector = BalancedTemplateSelector(seed=88, variation_count=3)

    directive = [
        selector.select("directive", item_key=f"directive-{index}")
        for index in range(13)
    ]
    regulation = [
        selector.select("regulation", item_key=f"regulation-{index}")
        for index in range(9)
    ]

    directive_counts = Counter(item.template_slug for item in directive)
    regulation_counts = Counter(item.template_slug for item in regulation)
    assert max(directive_counts.values()) - min(directive_counts.values()) <= 1
    assert max(regulation_counts.values()) - min(regulation_counts.values()) <= 1


def test_verbatim_selection_does_not_shift_template_balance() -> None:
    selector = BalancedTemplateSelector(seed=88, variation_count=3)

    verbatim = selector.select(
        "guide",
        item_key="verbatim",
        renderer_family="verbatim",
    )
    templated = [
        selector.select("guide", item_key=f"guide-{index}")
        for index in range(4)
    ]

    assert verbatim.template_slug == "00_verbatim"
    assert {item.template_slug for item in templated} == set(
        template_slugs_for_document_type("guide")
    )


def test_directory_batch_renders_one_balanced_assignment_per_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    for index in range(13):
        generation_route = (
            "mask_restoration" if index == 0 else "fully_synthetic"
        )
        payload = {
            "output_filename": f"문서-{index:02d}.pdf",
            "source_assessment": {
                "source_classification": {"document_type": "guide"},
            },
            "generation_plan": {
                "generation_route": generation_route,
                "final_target": {},
            },
            "generation_artifact": {
                "contract_version": "2.2.0",
                "generated_document": {
                    "contract_version": "2.2.0",
                    "title": f"문서 {index}",
                    "blocks": [],
                },
                "provenance": {},
            },
            "generation_receipt": {
                "request_id": f"request-{index:02d}",
            },
        }
        (input_dir / f"{index:02d}.txt").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def fake_render(
        payload: dict,
        document_output_dir: Path,
        **kwargs: object,
    ) -> list[dict[str, object]]:
        assert payload["result"]["source_classification"] == {
            "document_type": "guide"
        }
        template_slug = next(iter(kwargs["template_slugs"]))
        variation_offset = int(kwargs["variation_offset"])
        template_dir = document_output_dir / template_slug
        template_dir.mkdir(parents=True)
        pdf_path = template_dir / "generated.pdf"
        html_path = template_dir / "generated.html"
        pdf_path.write_bytes(b"%PDF")
        html_path.write_text("<html></html>", encoding="utf-8")
        return [
            {
                "status": "ok",
                "template_slug": template_slug,
                "variation_slug": f"{variation_offset + 1:02d}_test",
                "pdf": str(pdf_path),
                "html": str(html_path),
            }
        ]

    monkeypatch.setattr(render_cli, "render_generation_payload", fake_render)

    manifest = render_cli.render_input_directory(
        input_dir,
        output_dir,
        selection_seed=2026,
        variation_count=3,
    )

    assert manifest["document_count"] == 13
    assert manifest["success_count"] == 13
    assert manifest["rejected_count"] == 0
    selections = [entry["selection"] for entry in manifest["documents"]]
    assert selections[0]["renderer_family"] == "verbatim"
    assert selections[0]["template_slug"] == "00_verbatim"
    template_counts = Counter(
        selection["template_slug"]
        for selection in selections
        if selection["renderer_family"] == "guide"
    )
    assert set(template_counts.values()) == {3}
    per_template_variations: dict[str, set[int]] = defaultdict(set)
    for selection in selections[1:]:
        per_template_variations[selection["template_slug"]].add(
            selection["variation_index"]
        )
    assert all(
        variations == {1, 2, 3}
        for variations in per_template_variations.values()
    )
    assert (output_dir / "문서-00" / "00_verbatim" / "문서-00.pdf").exists()
    saved = json.loads(
        (output_dir / "batch_manifest.json").read_text(encoding="utf-8")
    )
    assert saved["selection_seed"] == 2026


def test_directory_batch_rejects_output_inside_input(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    with pytest.raises(ValueError, match="outside input_dir"):
        render_cli.render_input_directory(
            input_dir,
            input_dir / "output",
            selection_seed=1,
        )


def test_directory_batch_retries_source_miss_with_same_template_compact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    payload = {
        "output_filename": "재시도.pdf",
        "source_assessment": {
            "source_classification": {"document_type": "guide"},
        },
        "generation_plan": {
            "generation_route": "fully_synthetic",
            "final_target": {},
        },
        "generation_artifact": {
            "contract_version": "2.2.0",
            "generated_document": {
                "contract_version": "2.2.0",
                "title": "재시도 문서",
                "blocks": [],
            },
        },
    }
    (input_dir / "payload.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    calls: list[tuple[str, int]] = []

    def fake_render(
        _payload: dict,
        document_output_dir: Path,
        **kwargs: object,
    ) -> list[dict[str, object]]:
        template_slug = next(iter(kwargs["template_slugs"]))
        variation_offset = int(kwargs["variation_offset"])
        calls.append((template_slug, variation_offset))
        if len(calls) == 1:
            raise RenderedSourceTextError("missing source")
        template_dir = document_output_dir / template_slug
        template_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = template_dir / "generated.pdf"
        html_path = template_dir / "generated.html"
        pdf_path.write_bytes(b"%PDF")
        html_path.write_text("<html></html>", encoding="utf-8")
        return [
            {
                "status": "ok",
                "template_slug": template_slug,
                "variation_slug": "02_compact",
                "pdf": str(pdf_path),
                "html": str(html_path),
            }
        ]

    monkeypatch.setattr(render_cli, "render_generation_payload", fake_render)

    manifest = render_cli.render_input_directory(
        input_dir,
        output_dir,
        selection_seed=2026,
        variation_count=1,
    )

    assert manifest["success_count"] == 1
    assert manifest["rejected_count"] == 0
    document = manifest["documents"][0]
    assert calls == [
        (document["selection"]["template_slug"], 0),
        (document["selection"]["template_slug"], 1),
    ]
    assert [attempt["status"] for attempt in document["render_attempts"]] == [
        "source_text_missing",
        "ok",
    ]
    assert document["accepted_selection"]["reason"] == (
        "same_template_compact"
    )


def test_source_retry_candidates_include_an_alternate_compact_template() -> None:
    assignment = BalancedTemplateSelector(seed=2026, variation_count=1).select(
        "guide",
        item_key="one-guide",
    )

    candidates = render_cli._source_text_retry_candidates(assignment, "guide")

    assert len(candidates) == 3
    assert candidates[0]["template_slug"] == assignment.template_slug
    assert candidates[1] == {
        "template_slug": assignment.template_slug,
        "variation_index": 2,
        "variation_offset": 1,
        "reason": "same_template_compact",
    }
    assert candidates[2]["template_slug"] != assignment.template_slug
    assert candidates[2]["variation_index"] == 2
    assert candidates[2]["reason"] == "alternate_template_compact"


def test_directory_batch_does_not_retry_non_source_render_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "payload.json").write_text(
        json.dumps(
            {
                "result": {
                    "source_classification": {"document_type": "guide"},
                    "generation_route": "fully_synthetic",
                    "generated_document": {"title": "잘못된 입력"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = 0

    def fake_render(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        raise ValueError("invalid render input")

    monkeypatch.setattr(render_cli, "render_generation_payload", fake_render)

    manifest = render_cli.render_input_directory(
        input_dir,
        output_dir,
        selection_seed=2026,
    )

    assert calls == 1
    assert manifest["success_count"] == 0
    assert manifest["rejected_count"] == 1
    assert manifest["documents"][0]["render_attempts"][0]["status"] == (
        "rejected"
    )
    assert manifest["documents"][0]["document_type"] == "guide"
    assert manifest["documents"][0]["document_type_resolution"] == {
        "document_type": "guide",
        "source": "explicit",
        "document_form": None,
        "reason": "result.source_classification.document_type",
    }
