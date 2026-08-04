"""C트랙 문서 한 건을 **3단계(분석-작성-기록)** 계약으로 생성한다.

``run_c_track_generation.py``와 같은 템플릿·같은 사건 프레임을 쓰되 두 곳이
다르다.

1. 출력 계약이 ``GeneratedDocumentIR`` 하나가 아니라 ``CTrackCoTResponse``다 —
   분석(``security_analysis``)이 문서보다 앞 필드에, 발췌·저장 사유가 뒤 필드에
   온다. 필드 순서가 곧 사고 순서이므로 산문으로 "먼저 분석하라"고 쓰지 않는다.
2. 프롬프트 골격이 다르다. 저쪽은 지시를 종류별 절로 쌓지만 이쪽은 절을
   **단계**로 세운다 — ``#역할``·``#과제``·``[단계 1~3]``이 system에,
   ``[조건]``이 user에 온다(``render_cot_fixed_prefix``·
   ``render_cot_case_section``).

**대체가 아니라 대조군이다.** 저쪽 스크립트를 지우지 않는 이유는 같은 템플릿·
같은 ``--case-index``로 두 경로를 돌려 무엇이 달라지는지 보기 위해서다.
``minimal_prompt``가 현행 프롬프트를 두고 별도 모듈로 선 것과 같은 배치다.

기본값은 실호출을 하지 않는다. ``--execute`` 없이 돌리면 프롬프트를 렌더링해
보여 주는 것까지가 전부이고 과금되지 않는다.

사용 예:

    # 프롬프트만 확인 (무료)
    python scripts/generate/run_c_cot_generation.py

    # 단일 출력 경로와 같은 건을 나란히 보기
    python scripts/generate/run_c_track_generation.py --case-index 3
    python scripts/generate/run_c_cot_generation.py --case-index 3

    # 실제 호출 (과금, 1회)
    python scripts/generate/run_c_cot_generation.py --execute --out tmp/c-cot-001.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from itertools import islice
from pathlib import Path

# 콘솔 기본 코드페이지가 cp949라 프롬프트의 em dash 하나에 죽는다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# conda 기본값이 없는 파일을 가리켜 OpenAI 호출이 요청 전에 FileNotFoundError로
# 죽는다. certifi 번들로 덮어쓴다 — import 시점이어야 openai가 집어 간다.
if not Path(os.environ.get("SSL_CERT_FILE", "")).exists():
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from rd2.source_generation.c_track_templates import (  # noqa: E402
    C_TRACK_COT_PROMPT_VERSION,
    C_TRACK_TEMPLATE_VERSION,
    C_TRACK_TEMPLATES,
    expand_cases,
    render_cot_case_section,
    render_cot_fixed_prefix,
)
from rd2.source_generation.contracts import CTrackCoTResponse  # noqa: E402
from rd2.source_generation.gateway import default_openai_gateway  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        default=None,
        help=(
            "`{세부조항}/{기관}` 형식. 생략하면 등록된 첫 템플릿을 쓴다. "
            f"등록된 것: {', '.join(f'{k.value}/{a}' for k, a in C_TRACK_TEMPLATES)}"
        ),
    )
    parser.add_argument(
        "--case-index",
        type=int,
        default=0,
        help="전개 순서에서 몇 번째 사건 프레임을 쓸지. 같은 seed면 항상 같다.",
    )
    parser.add_argument("--seed", type=int, default=0, help="expand_cases 전개 시드")
    parser.add_argument("--model", default="gpt-5")
    # 단일 출력 경로보다 높다. 분석·발췌·저장 사유가 같은 응답에 들어가므로
    # 16,000에서는 본문이 밀려 LengthFinishReasonError로 죽을 수 있다.
    parser.add_argument("--max-output-tokens", type=int, default=24000)
    parser.add_argument("--out", type=Path, help="생성 결과를 JSON으로 저장")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 LLM을 호출한다. 문서당 1회 호출이며 그대로 과금된다.",
    )
    args = parser.parse_args()

    if args.template:
        subclause_value, _, agency = args.template.partition("/")
        matches = [
            key
            for key in C_TRACK_TEMPLATES
            if key[0].value == subclause_value and key[1] == agency
        ]
        if not matches:
            raise SystemExit(f"등록되지 않은 템플릿이다: {args.template}")
        template = C_TRACK_TEMPLATES[matches[0]]
    else:
        template = next(iter(C_TRACK_TEMPLATES.values()))

    frames = expand_cases(template, args.case_index + 1, seed=args.seed)
    frame = next(islice(frames, args.case_index, None))

    system_prompt = render_cot_fixed_prefix(template)
    user_prompt = render_cot_case_section(template, frame)

    print(f"템플릿    : {template.subclause_key.value} / {template.agency}")
    print(f"버전      : {C_TRACK_TEMPLATE_VERSION} + {C_TRACK_COT_PROMPT_VERSION}")
    print(f"조항      : 제9조제1항 제{template.clause_no.value}호")
    print(f"조합 수   : {template.case_count:,} (이번 건 case_index={frame.case_index})")
    print(f"부서      : {frame.department}")
    print(f"사안      : {frame.subject} / {frame.stage} / {frame.security_grade}")
    print(f"문서      : {frame.subject_case.document_name} ({frame.document_form.value})")
    print(f"프롬프트  : 고정 {len(system_prompt):,}자 + 이 건 {len(user_prompt):,}자")

    print("\n" + "=" * 70 + "\n[고정 접두사 — SYSTEM]\n" + "=" * 70)
    print(system_prompt)
    print("\n" + "=" * 70 + "\n[이 건의 조건 — USER]\n" + "=" * 70)
    print(user_prompt)

    if not args.execute:
        print("\n--execute 없이 돌렸다. 실호출은 하지 않는다.")
        return 0

    gateway = default_openai_gateway()
    call = gateway.parse(
        model=args.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_model=CTrackCoTResponse,
        max_output_tokens=args.max_output_tokens,
    )
    response: CTrackCoTResponse = call.parsed
    document = response.document

    print("\n" + "=" * 70 + "\n[1단계 — 보안 취약점 분석]\n" + "=" * 70)
    print(f"취약점    : {response.security_analysis.vulnerability}")
    print(f"지장 범위 : {response.security_analysis.impact}")
    print("\n" + "=" * 70 + f"\n[2단계 — 문서] {document.title}\n" + "=" * 70)
    print(document.body_text)

    print("\n" + "=" * 70 + "\n[3단계 — 보안 로그]\n" + "=" * 70)
    for snippet in response.confidential_snippets:
        print(f"- [{snippet.block_id}] {snippet.quote}")
    print(f"\n저장 사유 : {response.reasoning_for_storage}")

    print(f"\nblock 수  : {len(document.blocks)}")
    print(f"본문 길이 : {len(document.body_text):,}자")
    print(f"발췌 수   : {len(response.confidential_snippets)}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "template": f"{template.subclause_key.value}/{template.agency}",
                    "template_version": C_TRACK_TEMPLATE_VERSION,
                    "prompt_version": C_TRACK_COT_PROMPT_VERSION,
                    # 조항은 템플릿이 잠근 값이라 모델에게 묻지 않는다. 로그에
                    # 필요한 자리이므로 여기서 붙인다.
                    "legal_basis": f"9-1-{template.clause_no.value}",
                    "subclause_key": template.subclause_key.value,
                    "case_index": frame.case_index,
                    "seed": args.seed,
                    "model": args.model,
                    "response_id": call.response_id,
                    "response": response.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n저장      : {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
