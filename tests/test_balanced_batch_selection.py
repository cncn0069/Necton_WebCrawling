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


def test_directory_batch_renders_one_balanced_assignment_per_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    for index in range(12):
        payload = {
            "result": {
                "source_classification": {"document_type": "guide"},
            },
            "receipt": {"request_id": f"request-{index:02d}"},
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
        document_output_dir.mkdir(parents=True)
        template_slug = next(iter(kwargs["template_slugs"]))
        variation_offset = int(kwargs["variation_offset"])
        return [
            {
                "status": "ok",
                "template_slug": template_slug,
                "variation_slug": f"{variation_offset + 1:02d}_test",
            }
        ]

    monkeypatch.setattr(render_cli, "render_generation_payload", fake_render)

    manifest = render_cli.render_input_directory(
        input_dir,
        output_dir,
        selection_seed=2026,
        variation_count=3,
    )

    assert manifest["document_count"] == 12
    assert manifest["success_count"] == 12
    assert manifest["rejected_count"] == 0
    selections = [entry["selection"] for entry in manifest["documents"]]
    template_counts = Counter(
        selection["template_slug"] for selection in selections
    )
    assert set(template_counts.values()) == {3}
    per_template_variations: dict[str, set[int]] = defaultdict(set)
    for selection in selections:
        per_template_variations[selection["template_slug"]].add(
            selection["variation_index"]
        )
    assert all(
        variations == {1, 2, 3}
        for variations in per_template_variations.values()
    )
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
