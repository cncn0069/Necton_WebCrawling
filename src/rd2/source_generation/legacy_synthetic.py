"""기존 조항 생성기를 source-free ``GeneratedDocumentIR``로 연결하는 어댑터."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from openai import OpenAI

from rd2.administrative_status import (
    ADMIN_STATUS_TEXT_POLICIES,
    AdminStatus,
)
from rd2.generators.content_points import LEAF_CONTENT_POINTS
from rd2.generators.clause_data import CLAUSES
from rd2.generators.generate import generate_clause_document
from rd2.generators.template_matrix import TEMPLATE_TARGETS
from rd2.schema.models import Document
from rd2.source_generation.contracts import (
    BulletListBlock,
    GeneratedDocumentIR,
    GenerationTarget,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
    TargetClassification,
)


@dataclass(frozen=True)
class FullySyntheticContext:
    """원문과 독립적으로 합성 문서에 허용되는 실행 컨텍스트."""

    scenario_id: str
    ordering_agency: str
    production_date: str
    scenario_index: int | None = None

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must not be blank")
        if not self.ordering_agency.strip():
            raise ValueError("ordering_agency must not be blank")
        try:
            date.fromisoformat(self.production_date)
        except ValueError as exc:
            raise ValueError("production_date must be an ISO date") from exc
        if self.scenario_index is not None and self.scenario_index < 0:
            raise ValueError("scenario_index must be non-negative")


class FullySyntheticDocumentGenerator(Protocol):
    def generate(
        self,
        *,
        target: GenerationTarget,
        context: FullySyntheticContext,
    ) -> GeneratedDocumentIR: ...


@dataclass(frozen=True)
class SensitiveSyntheticProfile:
    clause_no: str
    subclause_key: str
    subclause_label: str
    document_type: str
    template_id: str
    scenario_indexes: tuple[int, ...]
    core_content: tuple[str, ...]
    secondary_content: tuple[str, ...]


_SCENARIO_INDEXES: dict[tuple[str, str], tuple[int, ...]] = {
    ("5", "audit_inspection"): (0, 4, 5),
    ("5", "bid_contract"): (1,),
    ("5", "personnel_management"): (2, 6, 8),
    ("5", "decision_review"): (7, 9),
    ("5", "technology_development"): (3,),
    ("6", "petitioner_pii"): (0, 4, 5),
    ("6", "personnel_pii"): (1, 6, 7),
    ("6", "welfare_pii"): (2,),
    ("6", "subject_pii"): (3,),
    ("7", "technology_patent"): (0, 8),
    ("7", "ma_terms"): (1,),
    ("7", "security_diagnosis"): (2,),
    ("7", "unit_cost"): (3,),
    ("7", "business_strategy"): (4, 5, 6, 7),
    ("8", "real_estate_speculation"): (0, 1, 2, 3, 4, 5),
    ("8", "cornering"): (6, 7, 8),
}


def sensitive_profiles() -> dict[tuple[str, str], SensitiveSyntheticProfile]:
    """민감 5~8호의 모든 세부조항을 기존 생성 자산에 매핑한다."""

    profiles: dict[tuple[str, str], SensitiveSyntheticProfile] = {}
    for key, scenario_indexes in _SCENARIO_INDEXES.items():
        clause_no, subclause_key = key
        scenario_count = len(CLAUSES[clause_no].scenario_prompts)
        if not scenario_indexes or max(scenario_indexes) >= scenario_count:
            raise ValueError(
                f"invalid legacy scenario indexes for sensitive subclause {key!r}"
            )
        targets = [
            target
            for target in TEMPLATE_TARGETS
            if target.clause_no == clause_no
            and target.subclause_key == subclause_key
        ]
        if not targets:
            raise ValueError(f"no template target for sensitive subclause {key!r}")
        target = targets[0]
        leaf_key = (clause_no, subclause_key, target.doc_type)
        points = LEAF_CONTENT_POINTS.get(leaf_key)
        if points is None:
            raise ValueError(f"no content points for sensitive template {leaf_key!r}")
        profiles[key] = SensitiveSyntheticProfile(
            clause_no=clause_no,
            subclause_key=subclause_key,
            subclause_label=target.subclause_label,
            document_type=target.doc_type,
            template_id=target.template_id,
            scenario_indexes=scenario_indexes,
            core_content=points.core,
            secondary_content=points.secondary,
        )
    return profiles


SENSITIVE_SYNTHETIC_PROFILES = sensitive_profiles()


def build_sensitive_generation_guidance(
    *,
    profile: SensitiveSyntheticProfile,
    scenario_id: str,
    administrative_statuses: tuple[AdminStatus, ...] = (),
) -> str:
    core = ", ".join(profile.core_content)
    secondary = ", ".join(profile.secondary_content) or "없음"
    guidance = [
            "이 호출은 공개 원문을 사용하지 않는 완전 합성 생성이다.",
            f"시나리오 ID: {scenario_id}",
            (
                f"고정 목표: S / 정보공개법 제{profile.clause_no}호 / "
                f"{profile.subclause_key}({profile.subclause_label})"
            ),
            (
                f"문서유형: {profile.document_type}; 템플릿 연결 키: "
                f"{profile.template_id}"
            ),
            f"반드시 본문에 구체적으로 드러낼 핵심 민감 포인트: {core}",
            f"문서 자연성을 위해 선택적으로 사용할 부수 포인트: {secondary}",
            (
                "핵심 민감 포인트에는 평가기준·점수·가격·식별정보·내부결정 등 "
                "목표에 맞는 구체적인 가상 값을 포함하라."
            ),
            (
                f"본문 말미에 정보공개법 제9조 제1항 제{profile.clause_no}호의 "
                f"{profile.subclause_label} 사유로 공개 시 어떤 업무상 침해가 "
                "발생하는지 설명하는 자연스러운 '공개 여부 검토' 문단을 포함하라."
            ),
            (
                "다른 세부조항을 주된 비공개 사유로 만들지 말고, 공개 문서처럼 "
                "일반론만 서술하지 마라."
            ),
            (
                "대외비 표시나 비밀유지협약만으로 타 법률상 비밀인 제1호처럼 "
                "보이게 쓰지 마라."
            ),
            "공개 원문, 원문 인용, 원문 기관·인명·과제명은 제공되지 않았으며 추정하지 마라.",
    ]
    for status in administrative_statuses:
        policy = ADMIN_STATUS_TEXT_POLICIES[status]
        guidance.append(
            f"행정상태 '{status.value}'를 별도 메타데이터나 고정 상태명으로 "
            f"붙이지 말고, 다음 상황이 본문 문맥에서 자연스럽게 드러나게 하라: "
            f"{policy.detail}."
        )
    return "\n".join(guidance)


_KEY_VALUE_RE = re.compile(r"^\s*([^:\n]{1,80})\s*:\s*(\S.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|(?:\d+)[.)])\s+(\S.*)$")


def legacy_document_to_ir(document: Document) -> GeneratedDocumentIR:
    """기존 title/body_text 결과를 결정론적인 블록 IR로 변환한다."""

    body_text = (document.body_text or "").strip()
    if not body_text:
        raise ValueError("legacy synthetic generator returned an empty body")

    blocks: list[Any] = [
        KeyValueBlock(
            block_id="g1",
            entries=(
                KeyValueEntry(key="기관명", value=document.ordering_agency),
                KeyValueEntry(
                    key="생산일자",
                    value=(
                        document.production_date.isoformat()
                        if document.production_date is not None
                        else "미상"
                    ),
                ),
            ),
        )
    ]
    groups = re.split(r"\n\s*\n", body_text)
    for group in groups:
        lines = [line.strip() for line in group.splitlines() if line.strip()]
        if not lines:
            continue
        block_id = f"g{len(blocks) + 1}"

        bullet_matches = [_BULLET_RE.match(line) for line in lines]
        if all(match is not None for match in bullet_matches):
            blocks.append(
                BulletListBlock(
                    block_id=block_id,
                    items=tuple(match.group(1) for match in bullet_matches if match),
                )
            )
            continue

        key_value_matches = [_KEY_VALUE_RE.match(line) for line in lines]
        if all(match is not None for match in key_value_matches):
            entries = tuple(
                KeyValueEntry(key=match.group(1), value=match.group(2))
                for match in key_value_matches
                if match
            )
            if len({entry.key for entry in entries}) == len(entries):
                blocks.append(KeyValueBlock(block_id=block_id, entries=entries))
                continue

        blocks.append(
            ParagraphBlock(block_id=block_id, text="\n".join(lines))
        )

    return GeneratedDocumentIR(title=document.title, blocks=tuple(blocks))


class LegacySensitiveSyntheticGenerator:
    """기존 Chat Completions 생성기를 S 5~8호 전용 source-free 호출로 감싼다."""

    def __init__(
        self,
        *,
        client: OpenAI | None = None,
        model: str = "gpt-4o-mini",
        document_factory: Callable[..., Document] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._document_factory = document_factory or generate_clause_document

    def generate(
        self,
        *,
        target: GenerationTarget,
        context: FullySyntheticContext,
    ) -> GeneratedDocumentIR:
        if target.classification != TargetClassification.S:
            raise ValueError("legacy sensitive adapter only supports S targets")
        if target.clause_no is None or target.subclause_key is None:
            raise ValueError(
                "legacy sensitive adapter requires a legal clause target"
            )

        key = (target.clause_no.value, target.subclause_key.value)
        profile = SENSITIVE_SYNTHETIC_PROFILES.get(key)
        if profile is None:
            raise ValueError(
                "legacy sensitive adapter only supports clause 5-8 subclauses"
            )
        scenario_index = (
            context.scenario_index
            if context.scenario_index is not None
            else profile.scenario_indexes[0]
        )
        if scenario_index not in profile.scenario_indexes:
            raise ValueError(
                f"scenario_index {scenario_index} is not allowed for {key!r}"
            )

        document = self._document_factory(
            profile.clause_no,
            ordering_agency=context.ordering_agency,
            production_date=context.production_date,
            client=self._client,
            model=self._model,
            scenario_index=scenario_index,
            generation_guidance=build_sensitive_generation_guidance(
                profile=profile,
                scenario_id=context.scenario_id,
                administrative_statuses=target.administrative_statuses,
            ),
        )
        return legacy_document_to_ir(document)
