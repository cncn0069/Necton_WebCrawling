"""공문(official_letter) 형식의 합성 문서를 생성해 PDF 렌더러 입력으로 만든다.

렌더러(`scripts/render_generated_documents.py`)는 `result.generated_document`가
들어 있는 payload를 받는다. 이 스크립트는 그 payload를 만든다.

**왜 별도 스크립트인가.** 원본문서 기반 배치(`run_source_generation_batch.py`)는
공개 원문을 재료로 삼아 P1이 형식을 스스로 정한다. 반면 여기서는 **형식을
고정해** 공문만 뽑아야 하므로 source-free 경로를 직접 쓴다. 수집 코퍼스의
`official_document`는 메타데이터 전용이라 본문이 없어(open_go_kr) 원문 기반으로는
공문을 만들 재료 자체가 없다.

생성물은 `DocumentForm.OFFICIAL_LETTER`의 판정 정의를 그대로 지시로 받는다 —
수신처가 특정된 개별 사안 처리 문서이고, 제목-본문-붙임의 표준 시행문 구성을
갖춰야 한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openai import OpenAI  # noqa: E402

from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    DOCUMENT_FORM_DEFINITIONS,
    DocumentForm,
)
from rd2.source_generation.contracts import GeneratedDocumentIR  # noqa: E402
from rd2.source_generation.pipeline import (  # noqa: E402
    OpenAIResponsesGateway,
    RetryingGateway,
)

#: 공문이 실제로 오가는 상황들. 형식은 같고 사안만 다르게 해서 서식 변주를 본다.
SCENARIOS: tuple[tuple[str, str], ...] = (
    ("협조 요청", "타 부서에 자료 제출을 요청하는 공문"),
    ("자료 제출", "상급기관 요구에 따라 실적 자료를 제출하는 공문"),
    ("회의 개최 알림", "관계기관에 회의 일정과 참석을 알리는 공문"),
    ("의견 조회", "제도 개선안에 대한 관계기관 의견을 구하는 공문"),
    ("점검 실시 통보", "현장 점검 일정과 준비 사항을 통보하는 공문"),
    ("교육 이수 안내", "소속 직원 대상 의무교육 이수를 안내하는 공문"),
    ("시설 이용 협조", "행사 기간 중 시설 이용 협조를 요청하는 공문"),
    ("업무 인계 통보", "담당자 변경에 따른 업무 인계를 통보하는 공문"),
    ("예산 집행 협의", "사업비 집행 방식을 관계 부서와 협의하는 공문"),
    ("자료 정정 요청", "제출된 자료의 오류 정정을 요청하는 공문"),
)

SYSTEM_PROMPT = """\
당신은 학습용 합성 대한민국 공공문서 생성기다. 실존하는 개인정보나 기밀을
사용하지 않고, 요청된 형식에 맞는 GeneratedDocumentIR만 반환한다.

paragraph, bullet_list, key_value, table, attachment_reference block만 쓴다.
문서가 무엇을 다룬다고 소개하지 말고 그 내용을 직접 작성한다. 구체적인
날짜·수량·금액·부서명을 실제 값으로 채운다.
"""


def _user_prompt(topic: str, description: str, reference: date) -> str:
    definition = DOCUMENT_FORM_DEFINITIONS[DocumentForm.OFFICIAL_LETTER]
    return json.dumps(
        {
            "document_form": DocumentForm.OFFICIAL_LETTER.value,
            "form_label": definition.label,
            "form_definition": definition.definition,
            "form_includes": list(definition.includes),
            "form_excludes": list(definition.excludes),
            "topic": topic,
            "situation": description,
            "reference_date": reference.isoformat(),
            "required_structure": [
                "문서번호·수신·시행일자를 담은 key_value block으로 시작한다",
                "제목에 해당하는 사안을 본문 첫 paragraph에서 바로 다룬다",
                "조치 사항이나 요청 사항을 bullet_list 또는 table로 정리한다",
                "붙임이 있으면 attachment_reference block으로 적는다",
            ],
            "classification": "O",
            "note": "정보공개법상 비공개 사유가 없는 일반 공문이다",
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    gateway = RetryingGateway(
        OpenAIResponsesGateway(OpenAI(api_key=os.environ["OPENAI_API_KEY"])),
        max_attempts=args.max_attempts,
    )
    reference = date.today()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with args.out.open("w", encoding="utf-8") as handle:
        for index, (topic, description) in enumerate(SCENARIOS[: args.count], 1):
            print(f"[{index}/{min(args.count, len(SCENARIOS))}] {topic}")
            try:
                call = gateway.parse(
                    model=args.model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=_user_prompt(topic, description, reference),
                    response_model=GeneratedDocumentIR,
                    max_output_tokens=8_000,
                )
            except Exception as exc:  # noqa: BLE001 — 실패도 기록하고 계속한다
                print(f"    실패: {type(exc).__name__}: {exc}")
                continue

            document = call.parsed
            payload = {
                "result": {
                    "contract_version": document.contract_version,
                    "generated_document": document.model_dump(mode="json"),
                },
                "receipt": {
                    "request_id": f"official-letter-{index:02d}",
                    "model_id": args.model,
                    "response_id": call.response_id,
                },
                "provenance": {
                    "document_form": DocumentForm.OFFICIAL_LETTER.value,
                    "topic": topic,
                    "reference_date": reference.isoformat(),
                    "generation_route": "fully_synthetic",
                },
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            written += 1
            print(f"    {document.title} / block {len(document.blocks)}개")

    print(f"\n{written}건 -> {args.out}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
