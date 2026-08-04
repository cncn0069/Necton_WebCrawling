"""대규모 배치에서 템플릿과 구조 변주를 균등하게 선택한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import random

from rd2.generators.administrative_rule_rendering import (
    ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS,
)
from rd2.generators.generated_document_pipeline import (
    renderer_family_for_document_type,
)
from rd2.generators.guide_rendering import GUIDE_TEMPLATE_VARIANTS
from rd2.generators.interpretation_compilation_rendering import (
    INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS,
)
from rd2.generators.meeting_minutes_rendering import (
    MEETING_MINUTES_TEMPLATE_VARIANTS,
)
from rd2.generators.notice_rendering import NOTICE_TEMPLATE_VARIANTS
from rd2.generators.official_document_rendering import (
    OFFICIAL_TEMPLATE_VARIANTS,
)
from rd2.generators.press_release_rendering import (
    PRESS_RELEASE_TEMPLATE_VARIANTS,
)
from rd2.generators.research_report_rendering import (
    RESEARCH_REPORT_TEMPLATE_VARIANTS,
)
from rd2.generators.status_report_rendering import (
    STATUS_REPORT_TEMPLATE_VARIANTS,
)

_TEMPLATE_SLUGS_BY_FAMILY = {
    "official_document": tuple(
        variant["slug"] for variant in OFFICIAL_TEMPLATE_VARIANTS
    ),
    "research_report": tuple(
        variant["slug"] for variant in RESEARCH_REPORT_TEMPLATE_VARIANTS
    ),
    "press_release": tuple(
        variant["slug"] for variant in PRESS_RELEASE_TEMPLATE_VARIANTS
    ),
    "administrative_rule": tuple(
        variant["slug"] for variant in ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS
    ),
    "interpretation_compilation": tuple(
        variant["slug"]
        for variant in INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS
    ),
    "guide": tuple(variant["slug"] for variant in GUIDE_TEMPLATE_VARIANTS),
    "status_report": tuple(
        variant["slug"] for variant in STATUS_REPORT_TEMPLATE_VARIANTS
    ),
    "meeting_minutes": tuple(
        variant["slug"] for variant in MEETING_MINUTES_TEMPLATE_VARIANTS
    ),
    "notice": tuple(variant["slug"] for variant in NOTICE_TEMPLATE_VARIANTS),
    "verbatim": ("00_verbatim",),
}

ALL_TEMPLATE_SLUGS = tuple(
    slug
    for family, family_slugs in _TEMPLATE_SLUGS_BY_FAMILY.items()
    if family != "verbatim"
    for slug in family_slugs
)


def template_slugs_for_document_type(
    document_type: str | None,
) -> tuple[str, ...]:
    """문서 분류값에 사용할 수 있는 템플릿 slug를 반환한다."""

    renderer_family = renderer_family_for_document_type(document_type)
    return _TEMPLATE_SLUGS_BY_FAMILY[renderer_family]


def _stable_seed(base_seed: int, *parts: object) -> int:
    material = "\0".join((str(base_seed), *(str(part) for part in parts)))
    digest = sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass(frozen=True)
class BalancedTemplateAssignment:
    document_type: str | None
    renderer_family: str
    template_slug: str
    variation_index: int
    selection_seed: int
    render_seed: int
    synthetic_scan: bool
    synthetic_scan_seed: int
    document_type_index: int

    @property
    def variation_offset(self) -> int:
        return self.variation_index - 1

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class BalancedTemplateSelector:
    """분류값별 템플릿과 변주 사용량 차이를 최대 1로 유지한다."""

    def __init__(self, *, seed: int, variation_count: int = 3) -> None:
        if variation_count < 1:
            raise ValueError("variation_count must be at least 1")
        if variation_count > 3:
            raise ValueError(
                "variation_count must be at most 3 structural variations"
            )
        self.seed = seed
        self.variation_count = variation_count
        self._document_type_positions: dict[tuple[str, str], int] = {}
        self._template_positions: dict[tuple[str, str, str], int] = {}

    def select(
        self,
        document_type: str | None,
        *,
        item_key: str,
        renderer_family: str | None = None,
    ) -> BalancedTemplateAssignment:
        type_key = document_type or "__unclassified__"
        resolved_family = (
            renderer_family
            if renderer_family is not None
            else renderer_family_for_document_type(document_type)
        )
        if resolved_family not in _TEMPLATE_SLUGS_BY_FAMILY:
            raise ValueError(f"Unknown renderer family: {resolved_family}")
        template_slugs = _TEMPLATE_SLUGS_BY_FAMILY[resolved_family]

        document_type_key = (type_key, resolved_family)
        type_position = self._document_type_positions.get(document_type_key, 0)
        template_epoch, template_offset = divmod(
            type_position,
            len(template_slugs),
        )
        template_cycle = list(template_slugs)
        random.Random(
            _stable_seed(self.seed, type_key, "template", template_epoch)
        ).shuffle(template_cycle)
        template_slug = template_cycle[template_offset]
        self._document_type_positions[document_type_key] = type_position + 1

        template_key = (type_key, resolved_family, template_slug)
        variation_position = self._template_positions.get(template_key, 0)
        variation_epoch, variation_offset = divmod(
            variation_position,
            self.variation_count,
        )
        variation_cycle = list(range(1, self.variation_count + 1))
        random.Random(
            _stable_seed(
                self.seed,
                type_key,
                template_slug,
                "variation",
                variation_epoch,
            )
        ).shuffle(variation_cycle)
        variation_index = variation_cycle[variation_offset]
        self._template_positions[template_key] = variation_position + 1

        scan_epoch, scan_offset = divmod(type_position, 2)
        scan_cycle = [False, True]
        random.Random(
            _stable_seed(
                self.seed,
                type_key,
                resolved_family,
                "synthetic_scan",
                scan_epoch,
            )
        ).shuffle(scan_cycle)
        synthetic_scan = scan_cycle[scan_offset]

        return BalancedTemplateAssignment(
            document_type=document_type,
            renderer_family=resolved_family,
            template_slug=template_slug,
            variation_index=variation_index,
            selection_seed=self.seed,
            render_seed=_stable_seed(
                self.seed,
                type_key,
                item_key,
                type_position,
            ),
            synthetic_scan=synthetic_scan,
            synthetic_scan_seed=_stable_seed(
                self.seed,
                type_key,
                item_key,
                type_position,
                "synthetic_scan_effects",
            ),
            document_type_index=type_position,
        )
