"""C트랙(제1~4호) 템플릿으로 문서 한 건을 생성한다.

``c_track_templates``는 지금까지 테스트에서만 불렸다 — 프롬프트를 만드는 데까지는
와 있는데 실제로 무엇이 나오는지는 아무도 본 적이 없다. 이 스크립트가 그 한 건을
돌린다.

5~8호 경로(``run_minimal_generation``)와 다른 점은 **원문이 없다**는 것이다.
1~4호는 rd2 DB에 해당 기관 문서가 0건이라 seed를 뽑을 원문 자체가 없고, 엔트로피는
전부 ``expand_cases``가 전개하는 사건 프레임에서 나온다. 그래서 판별기도, 변형
이력도 없다 — 호출은 문서당 **1회**이고 출력 계약은 ``GeneratedDocumentIR`` 하나다.

프롬프트 분할은 ``render_fixed_prefix``의 설계를 그대로 따른다. 고정 접두사를
system(``instructions``)에, 이 건의 조건만 user에 둔다 — 같은 템플릿으로 배치를
돌리면 앞부분이 통째로 프롬프트 캐시에 걸린다.

기본값은 실호출을 하지 않는다. ``--execute`` 없이 돌리면 프롬프트를 렌더링해
보여 주는 것까지가 전부이고 과금되지 않는다.

사용 예:

    # 프롬프트만 확인 (무료)
    python scripts/run_c_track_generation.py

    # 실제 호출 (과금, 1회)
    python scripts/run_c_track_generation.py --execute --out tmp/c-track-001.json
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
    C_TRACK_TEMPLATE_VERSION,
    C_TRACK_TEMPLATES,
    expand_cases,
    render_case_section,
    render_fixed_prefix,
)
from rd2.source_generation.contracts import GeneratedDocumentIR  # noqa: E402
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
    parser.add_argument("--max-output-tokens", type=int, default=16000)
    parser.add_argument("--out", type=Path, help="생성 결과 IR을 JSON으로 저장")
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

    system_prompt = render_fixed_prefix(template)
    user_prompt = render_case_section(template, frame)

    print(f"템플릿    : {template.subclause_key.value} / {template.agency}")
    print(f"버전      : {C_TRACK_TEMPLATE_VERSION}")
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
        response_model=GeneratedDocumentIR,
        max_output_tokens=args.max_output_tokens,
    )
    document: GeneratedDocumentIR = call.parsed

    print("\n" + "=" * 70 + f"\n[생성 결과] {document.title}\n" + "=" * 70)
    print(document.body_text)
    print(f"\nblock 수  : {len(document.blocks)}")
    print(f"본문 길이 : {len(document.body_text):,}자")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "template": f"{template.subclause_key.value}/{template.agency}",
                    "template_version": C_TRACK_TEMPLATE_VERSION,
                    "case_index": frame.case_index,
                    "seed": args.seed,
                    "model": args.model,
                    "response_id": call.response_id,
                    "document": document.model_dump(mode="json"),
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
