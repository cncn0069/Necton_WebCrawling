"""최소 프롬프트만으로 원문 하나를 판별 -> 변형한다.

큰 프롬프트 경로가 하던 일(판별 -> 계획 -> 생성 -> blind 검사 -> 재생성 게이트)을
**최소 프롬프트 두 개**로만 한다. 그 경로는 제거됐고 여기가 유일한 생성 경로다.

    원문 -> minimal_classifier -> 자료 하나 결정론적 선택 -> minimal 생성기
                                                              -> GeneratorResponse

큰 경로와 다른 점 넷.

1. 계획기가 없다. 판별기가 고른 ``primary_subclause``가 그대로 목표다.
2. seed를 조립하지 않는다. 심을 자료 하나를 ``select_minimal_ground``가 고르고
   생성기 프롬프트가 그 자료만 렌더링한다.
3. 생성기가 문서와 함께 **변형 이력**을 낸다(``GeneratorResponse``). 이력은
   ``document`` 밖에 있으므로 blind 채점자에게 넘어가지 않는다.
4. 검증기를 돌리지 않는다. 이 스크립트가 보는 것은 "변형이 되는가"까지다.

**문서 형식은 묻지 않는다.** 판별기는 세부조항만 찾고, 형식은 수집 라벨
(``doc_type``)에서 ``DOCUMENT_FORM_BY_TYPE``으로 유도한다. 배치 경로는 그 라벨을
DB 행에서 얻지만 여기 입력은 로컬 추출 JSON이라 라벨이 없다 — 그래서
``--doc-type``이 필수다.

기본값은 실호출을 하지 않는다. ``--execute`` 없이 돌리면 두 프롬프트를 렌더링해
보여 주는 것까지가 전부이고 과금되지 않는다. 호출 수는 문서당 2회다.

사용 예:

    # 프롬프트만 확인 (무료)
    python scripts/run_minimal_generation.py --source data/extracted/....json \\
        --doc-type official_document

    # 실제 호출 (과금)
    python scripts/run_minimal_generation.py --source ... --doc-type audit_result \\
        --execute
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# 콘솔 기본 코드페이지가 cp949라 프롬프트·도움말의 em dash 하나에 죽는다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# conda 기본값이 없는 파일을 가리켜 OpenAI 호출이 요청 전에 FileNotFoundError로
# 죽는다. certifi 번들로 덮어쓴다 — import 시점이어야 openai가 집어 간다.
if not Path(os.environ.get("SSL_CERT_FILE", "")).exists():
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    GROUND_IDS,
    SUBCLAUSE_GENERATION_RULES,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (  # noqa: E402
    GeneratorResponse,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
)
from rd2.source_generation.document_select import render_full_source  # noqa: E402
from rd2.source_generation.minimal_classifier import (  # noqa: E402
    MinimalSourceAssessment,
    render_minimal_classifier_system_prompt,
    render_minimal_classifier_user_prompt,
)
from rd2.source_generation.minimal_envelope import (  # noqa: E402
    build_generation_envelope,
    document_form_for_doc_type,
)
from rd2.source_generation.minimal_prompt import (  # noqa: E402
    render_minimal_generator_system_prompt,
    render_minimal_generator_user_prompt,
    select_minimal_ground,
)
from rd2.source_generation.gateway import default_openai_gateway  # noqa: E402


def _lines_from_spans(spans: list[dict]) -> list[str]:
    """span을 줄 단위로 묶는다.

    추출본의 span은 글꼴이 바뀔 때마다 끊기므로 한 줄이 여러 span으로 나뉜다.
    span 하나를 block 하나로 두면 `- 1 -` 같은 조각이 block이 되어
    ``available_slots``의 인용도 ``transformations``의 block_id도 쓸모가 없다.
    그래서 같은 y 대역(3pt)에 있는 span을 한 줄로 합친다.
    """

    placed: list[tuple[float, list[tuple[float, str]]]] = []
    for span in spans:
        text = span.get("text", "").strip()
        if not text:
            continue
        bbox = span.get("bbox") or [0, 0, 0, 0]
        x, y = float(bbox[0]), float(bbox[1])
        for top, parts in placed:
            if abs(top - y) < 3.0:
                parts.append((x, text))
                break
        else:
            placed.append((y, [(x, text)]))
    # PyMuPDF 좌표는 좌상단 원점이라 y가 아래로 갈수록 커진다. 오름차순이
    # 위에서 아래다 — 내림차순으로 두면 본문이 통째로 뒤집힌다.
    placed.sort(key=lambda item: item[0])
    lines = []
    for _, parts in placed:
        parts.sort(key=lambda item: item[0])
        line = " ".join(text for _, text in parts).strip()
        if line:
            lines.append(line)
    return lines


def load_snapshot(path: Path) -> SourceDocumentSnapshot:
    """추출 JSON(``pages[].spans[]`` 또는 ``pages[].blocks[]``)을 snapshot으로."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    pages = []
    for page in payload["pages"]:
        page_number = page.get("page_number") or page.get("page_no")
        if "spans" in page:
            texts = _lines_from_spans(page["spans"])
        else:
            texts = [
                block["text"].strip()
                for block in page.get("blocks", ())
                if block.get("text", "").strip()
            ]
        blocks = tuple(
            SourceTextBlock(block_id=f"p{page_number}:b{index}", text=text)
            for index, text in enumerate(texts)
        )
        if blocks:
            pages.append(SourcePage(page_number=page_number, blocks=blocks))
    if not pages:
        raise SystemExit(f"본문 텍스트가 없다(스캔본일 수 있다): {path}")
    return SourceDocumentSnapshot(
        source_document_id=payload.get("doc_id") or path.stem,
        source=payload.get("source") or "local_extract",
        manifest_key=str(path),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        pages=tuple(pages),
    )


def render_slot_table(assessment: MinimalSourceAssessment) -> str:
    return "\n".join(
        f"- [{slot.kind.value}] {slot.name} — 원문 인용: {slot.evidence_span.quote} ({slot.evidence_span.block_id})"
        for slot in assessment.available_slots
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--doc-type",
        required=True,
        choices=[value.value for value in SemanticDocumentType],
        help="수집 라벨. 문서 형식이 여기서 유도되고 envelope의 document_type이 된다",
    )
    parser.add_argument("--classifier-model", default="gpt-5")
    parser.add_argument("--generator-model", default="gpt-5")
    parser.add_argument("--max-output-tokens", type=int, default=16000)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--envelope-out",
        type=Path,
        help="PDF 렌더러가 읽는 envelope로 조립해 따로 쓴다.",
    )
    parser.add_argument(
        "--preview-subclause",
        help="--execute 없이 생성기 프롬프트까지 보려고 목표를 손으로 준다.",
    )
    parser.add_argument(
        "--preview-context",
        help="자료 선택 해시에 쓸 업무 맥락. --preview-subclause와 함께 쓴다.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 LLM을 호출한다. 문서당 2회 호출이며 그대로 과금된다.",
    )
    args = parser.parse_args()

    snapshot = load_snapshot(args.source)
    source_text = render_full_source(snapshot)

    document_form = document_form_for_doc_type(args.doc_type)
    if document_form is None:  # choices가 막지만, 대응표에 구멍이 나면 여기서 걸린다
        raise SystemExit(f"문서 형식을 유도할 수 없는 doc_type이다: {args.doc_type}")
    classifier_system = render_minimal_classifier_system_prompt(document_form)
    classifier_user = render_minimal_classifier_user_prompt(source_text)

    print(f"원문      : {args.source}")
    print(f"block 수  : {sum(len(page.blocks) for page in snapshot.pages)}")
    print(f"원문 길이 : {len(source_text):,}자")
    print(f"문서 형식 : {args.doc_type} -> {document_form.value} (판별기에 고정으로 준다)")
    print(f"판별기 프롬프트: {len(classifier_system):,}자")

    if not args.execute:
        print("\n--execute 없이 돌렸다. 실호출은 하지 않는다.")
        print("\n" + "=" * 70 + "\n[판별기 SYSTEM]\n" + "=" * 70)
        print(classifier_system)
        print("\n" + "=" * 70 + "\n[판별기 USER (앞 1,500자)]\n" + "=" * 70)
        print(classifier_user[:1500])
        if args.preview_subclause:
            # 판별기를 부르지 않았으므로 목표를 손으로 준다. 자료 선택은
            # 업무 맥락 해시라 그것도 함께 가정해야 실제와 같은 값이 나온다.
            subclause = SubclauseKey(args.preview_subclause)
            context = args.preview_context or snapshot.source_document_id
            ground_index = select_minimal_ground(subclause, context)
            print("\n" + "=" * 70)
            print(
                f"[생성기 SYSTEM — 가정: {subclause.value} / 업무맥락 {context!r} "
                f"-> 조건{GROUND_IDS[ground_index]}]"
            )
            print("=" * 70)
            print(
                render_minimal_generator_system_prompt(
                    subclause,
                    ground_index=ground_index,
                )
            )
        return 0

    gateway = default_openai_gateway()

    call = gateway.parse(
        model=args.classifier_model,
        system_prompt=classifier_system,
        user_prompt=classifier_user,
        response_model=MinimalSourceAssessment,
        max_output_tokens=args.max_output_tokens,
    )
    assessment: MinimalSourceAssessment = call.parsed
    print("\n[판별 결과]")
    print(
        json.dumps(
            assessment.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )

    subclause = assessment.primary_subclause
    ground_index = select_minimal_ground(subclause, assessment.business_context)
    patterns = SUBCLAUSE_GENERATION_RULES[subclause].document_patterns
    print(
        f"\n[심을 자료] 조건{GROUND_IDS[ground_index]} — "
        f"{patterns[ground_index].partition(':')[0]}"
    )

    generator_system = render_minimal_generator_system_prompt(
        subclause,
        ground_index=ground_index,
    )
    generator_user = render_minimal_generator_user_prompt(
        source_text,
        layout_analysis=assessment.layout_analysis,
        available_slots=render_slot_table(assessment),
    )
    print(f"생성기 프롬프트: {len(generator_system):,}자")

    generation = gateway.parse(
        model=args.generator_model,
        system_prompt=generator_system,
        user_prompt=generator_user,
        response_model=GeneratorResponse,
        max_output_tokens=args.max_output_tokens,
    )
    response: GeneratorResponse = generation.parsed

    print("\n[생성 결과]")
    print(
        json.dumps(
            response.model_dump(mode="json", exclude_computed_fields=True),
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n[본문]")
    print(response.document.body_text)

    # 글자 수만 보면 이 실패가 안 보인다. radio-001 실측에서 원문 126 block 중
    # 32 block만 남았는데 글자 수는 82%였다 — 살아남은 block에 민감정보를
    # 덧붙여 부풀린 결과다. 빠뜨림은 block ID로 세야 드러난다.
    #
    # ``synthetic_mask``와 달리 이 경로의 보존은 코드가 아니라 프롬프트 지시로
    # 서 있다. 지시가 지켜졌는지는 재 봐야 알기 때문에 매 실행에 찍는다.
    source_ids = [block.block_id for page in snapshot.pages for block in page.blocks]
    generated_ids = {block.block_id for block in response.document.blocks}
    missing = [block_id for block_id in source_ids if block_id not in generated_ids]
    print(
        f"\n[원문 보존] block {len(source_ids) - len(missing)}/{len(source_ids)}"
        f" ({(len(source_ids) - len(missing)) / len(source_ids):.0%}), "
        f"본문 {len(response.document.body_text):,}자 / 원문 {len(source_text):,}자"
    )
    if missing:
        print(f"  빠진 block {len(missing)}개: {', '.join(missing[:20])}")
        if len(missing) > 20:
            print(f"  … 외 {len(missing) - 20}개")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "source": str(args.source),
                    "assessment": assessment.model_dump(mode="json"),
                    "ground_id": GROUND_IDS[ground_index],
                    "generation": response.model_dump(
                        mode="json", exclude_computed_fields=True
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n기록: {args.out}")

    if args.envelope_out:
        envelope = build_generation_envelope(
            assessment=assessment.model_dump(mode="json"),
            response=response,
            snapshot=snapshot,
            doc_type=args.doc_type,
            document_form=document_form,
            classifier_prompt={
                "system": classifier_system,
                "user": classifier_user,
            },
            generator_prompt={
                "system": generator_system,
                "user": generator_user,
            },
            ground_id=GROUND_IDS[ground_index],
        )
        args.envelope_out.parent.mkdir(parents=True, exist_ok=True)
        args.envelope_out.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"envelope: {args.envelope_out} "
            f"(document_type {args.doc_type} / document_form {document_form.value})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
