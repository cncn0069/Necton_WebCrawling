from collections import Counter
import json

from rd2.generators.official_document_variations import (
    EXPECTED_PAGE_COUNTS,
    IDENTITY_PROFILES,
    PROFILES,
    apply_identity_context,
    build_identity_spec,
    build_variation_specs,
    render_identity_css,
    render_variation_css,
)
from rd2.generators.synthetic_official_agencies import (
    SYNTHETIC_AGENCIES,
    SYNTHETIC_AGENCY_POOL_SIZE,
)


def test_builds_three_deterministic_variations_for_every_template() -> None:
    all_specs = []

    for template_slug in EXPECTED_PAGE_COUNTS:
        first = build_variation_specs(template_slug)
        second = build_variation_specs(template_slug)

        assert first == second
        assert [spec.profile for spec in first] == list(PROFILES)
        assert len({spec.seed for spec in first}) == 3
        all_specs.extend(first)

    assert len(all_specs) == 30
    assert len({spec.seed for spec in all_specs}) == 30


def test_variation_palette_stays_low_saturation_for_print() -> None:
    for template_slug in EXPECTED_PAGE_COUNTS:
        for spec in build_variation_specs(template_slug):
            for color in (spec.accent, spec.light_accent):
                channels = [
                    int(color[index : index + 2], 16)
                    for index in (1, 3, 5)
                ]
                assert max(channels) - min(channels) <= 12


def test_every_variation_produces_scoped_css() -> None:
    for template_slug in EXPECTED_PAGE_COUNTS:
        for spec in build_variation_specs(template_slug):
            css = render_variation_css(spec)

            assert spec.css_class in css
            assert spec.heading_font in css
            assert spec.accent in css


def test_identity_profiles_are_balanced_within_every_layout() -> None:
    all_identities = []
    symbol_shapes_by_layout = {}

    for variation_index in range(1, 4):
        identities = [
            build_identity_spec(
                template_slug,
                variation_index=variation_index,
            )
            for template_slug in EXPECTED_PAGE_COUNTS
        ]

        assert Counter(identity.profile for identity in identities) == {
            "none": 4,
            "symbol": 4,
            "wordmark": 2,
        }
        symbol_shapes = Counter(
            identity.shape
            for identity in identities
            if identity.profile == "symbol"
        )
        assert set(symbol_shapes) == {"orbit", "block", "dual"}
        symbol_shapes_by_layout[variation_index] = symbol_shapes
        all_identities.extend(identities)

    assert set(identity.profile for identity in all_identities) == set(
        IDENTITY_PROFILES
    )
    assert Counter(identity.profile for identity in all_identities) == {
        "none": 12,
        "symbol": 12,
        "wordmark": 6,
    }
    assert Counter(
        identity.shape
        for identity in all_identities
        if identity.profile == "symbol"
    ) == {
        "orbit": 4,
        "block": 4,
        "dual": 4,
    }
    assert all(
        sorted(shapes.values()) == [1, 1, 2]
        for shapes in symbol_shapes_by_layout.values()
    )


def test_synthetic_agency_pool_has_300_unique_diverse_public_bodies() -> None:
    categories = Counter(
        agency.organization_category for agency in SYNTHETIC_AGENCIES
    )

    assert len(SYNTHETIC_AGENCIES) == SYNTHETIC_AGENCY_POOL_SIZE == 300
    assert len({agency.agency_name for agency in SYNTHETIC_AGENCIES}) == 300
    assert len({agency.romanized_name for agency in SYNTHETIC_AGENCIES}) == 300
    assert categories == {
        "local_government": 25,
        "police": 25,
        "fire_and_rescue": 25,
        "education": 25,
        "public_corporation": 25,
        "regional_development": 25,
        "research": 25,
        "health_and_welfare": 25,
        "field_service": 25,
        "culture_and_youth": 25,
        "public_committee": 25,
        "generic_public": 25,
    }
    organization_types = {
        agency.organization_type for agency in SYNTHETIC_AGENCIES
    }
    assert {"시", "군", "구"} <= organization_types
    assert {"경찰청", "경찰서", "해양경찰서"} <= organization_types
    assert {"소방본부", "소방서", "119안전센터"} <= organization_types
    assert {"교육지원청", "시설관리공단", "정책연구원"} <= (
        organization_types
    )


def test_document_identity_is_stable_across_templates_and_layouts() -> None:
    identities = [
        build_identity_spec(
            template_slug,
            variation_index=variation_index,
            base_seed=template_number * 100,
            identity_seed=87421,
        )
        for template_number, template_slug in enumerate(
            EXPECTED_PAGE_COUNTS,
            start=1,
        )
        for variation_index in range(1, 4)
    ]

    assert len({identity.agency_name for identity in identities}) == 1
    assert len({identity.agency_pool_index for identity in identities}) == 1
    assert len({identity.organization_category for identity in identities}) == 1
    assert len({identity.profile for identity in identities}) > 1
    assert len({identity.seed for identity in identities}) == len(identities)


def test_identity_seeds_can_reach_every_agency_without_template_bias() -> None:
    identities = [
        build_identity_spec(
            "01_classic_municipal",
            variation_index=1,
            identity_seed=seed,
        )
        for seed in range(SYNTHETIC_AGENCY_POOL_SIZE)
    ]

    assert {identity.agency_pool_index for identity in identities} == set(
        range(SYNTHETIC_AGENCY_POOL_SIZE)
    )
    assert len({identity.agency_name for identity in identities}) == 300


def test_identity_category_limits_seeded_selection_to_matching_pool() -> None:
    identities = [
        build_identity_spec(
            "01_classic_municipal",
            variation_index=1,
            identity_seed=seed,
            organization_category="generic_public",
        )
        for seed in range(25)
    ]

    assert len({identity.agency_name for identity in identities}) == 25
    assert {
        identity.organization_category for identity in identities
    } == {"generic_public"}
    assert all(
        identity.display_agency_name == identity.agency_name
        for identity in identities
        if len(identity.agency_name) > 6
    )


def test_identity_replaces_legacy_agency_tokens_and_scopes_css() -> None:
    base_context = {
        "emblem": "한빛",
        "agency_name": "한 빛 시",
        "issuer_title": "한 빛 시 장",
        "address": "한빛시 중앙대로 88",
        "details": [{"value": "한빛시민 누구나"}],
        "sections": [{"text": "한빛문화광장"}],
        "copy_recipients": "한빛시설관리공단",
        "website": "www.hanbit.go.kr",
        "email": "safety@hanbit.go.kr",
    }

    for template_slug in EXPECTED_PAGE_COUNTS:
        for variation_index in range(1, 4):
            identity = build_identity_spec(
                template_slug,
                variation_index=variation_index,
            )
            context = apply_identity_context(base_context, identity)
            serialized = json.dumps(context, ensure_ascii=False)
            css = render_identity_css(
                identity,
                accent="#123456",
                light_accent="#ddeeff",
            )

            assert "한빛" not in serialized
            assert identity.agency_name in serialized.replace(" ", "")
            assert f"identity-{identity.profile}" in css
            assert "http://" not in css
            assert "https://" not in css
