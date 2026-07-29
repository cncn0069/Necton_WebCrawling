from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image

from rd2.generators.synthetic_approval_stamps import (
    STAMP_PROFILES,
    STAMP_SHAPES,
    build_stamp_placement,
    generate_synthetic_approval_stamp,
)


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def test_same_seed_and_profile_produce_identical_stamp() -> None:
    first = generate_synthetic_approval_stamp(
        "해솔공공서비스원장인",
        seed=18273,
        profile="damaged",
    )
    second = generate_synthetic_approval_stamp(
        "해솔공공서비스원장인",
        seed=18273,
        profile="damaged",
    )

    assert first.png_bytes == second.png_bytes
    assert first.parameters == second.parameters
    assert first.data_uri == second.data_uri


def test_every_stamp_profile_changes_the_impression() -> None:
    stamps = [
        generate_synthetic_approval_stamp(
            "합성승인인",
            seed=7000 + index,
            profile=profile,
        )
        for index, profile in enumerate(STAMP_PROFILES)
    ]

    assert len({_digest(stamp.png_bytes) for stamp in stamps}) == len(
        STAMP_PROFILES
    )
    assert [stamp.parameters.profile for stamp in stamps] == list(
        STAMP_PROFILES
    )

    for stamp in stamps:
        image = Image.open(BytesIO(stamp.png_bytes))
        alpha = np.asarray(image.getchannel("A"))
        assert image.mode == "RGBA"
        assert image.size == (420, 420)
        assert alpha.max() > 0
        assert np.count_nonzero(alpha == 0) > alpha.size // 2


def test_implicit_profile_is_seeded_and_recorded() -> None:
    first = generate_synthetic_approval_stamp("합성확인인", seed=98765)
    second = generate_synthetic_approval_stamp("합성확인인", seed=98765)

    assert first.parameters.profile in STAMP_PROFILES
    assert first.parameters == second.parameters
    assert first.png_bytes == second.png_bytes


def test_round_square_and_oval_shapes_are_supported() -> None:
    stamps = [
        generate_synthetic_approval_stamp(
            "합성확인인",
            seed=12000 + index,
            profile="normal",
            shape=shape,
        )
        for index, shape in enumerate(STAMP_SHAPES)
    ]

    assert [stamp.parameters.shape for stamp in stamps] == list(STAMP_SHAPES)
    assert len({_digest(stamp.png_bytes) for stamp in stamps}) == len(
        STAMP_SHAPES
    )


def test_short_names_prefer_personal_shapes_and_long_names_avoid_square() -> None:
    short_shapes = {
        generate_synthetic_approval_stamp("김가온인", seed=seed).parameters.shape
        for seed in range(30)
    }
    long_shapes = {
        generate_synthetic_approval_stamp(
            "해솔공공서비스원장인",
            seed=seed,
        ).parameters.shape
        for seed in range(30)
    }

    assert short_shapes <= {"round", "square"}
    assert short_shapes == {"round", "square"}
    assert long_shapes <= {"round", "oval"}
    assert long_shapes == {"round", "oval"}


def test_stamp_placement_uses_approved_seeded_ratios() -> None:
    placements = [build_stamp_placement(seed) for seed in range(1000)]
    counts = {
        mode: sum(placement.mode == mode for placement in placements)
        for mode in ("standard", "boundary", "lower_overlap")
    }

    assert 500 <= counts["standard"] <= 600
    assert 200 <= counts["boundary"] <= 300
    assert 150 <= counts["lower_overlap"] <= 250
    assert all(
        placement.crosses_boundary
        for placement in placements
        if placement.mode != "standard"
    )
    assert all(
        placement == build_stamp_placement(placement.seed)
        for placement in placements
    )
