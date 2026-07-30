"""``run_source_generation_batch.py``의 batch_records.jsonl을 사람이 읽는 HTML로 만든다.

배치 산출물은 문서당 40개 가까운 필드의 JSONL이라 눈으로 훑을 수 없다. 이 리포트는
**한 문서가 판별 -> 계획 -> 생성 -> 독립 검사를 지나며 무엇이 바뀌는지**를 한 줄로
펼쳐 보여준다. 프롬프트를 고칠 때마다 같은 형태로 다시 뽑아 이전 버전과 나란히
비교하는 것이 목적이므로, 레이아웃은 v29 리포트와 같게 유지한다.

배치 기록에 없는 것은 그리지 않는다 — ``run_source_generation_batch.py``는 원문
전문이나 프롬프트 원문을 남기지 않으므로 원문 칸은 발췌(``source_excerpt``)다.
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path

#: 생성기 프롬프트가 금지한 "내용의 존재만 말하는" 문형. 실측에서 위반이 잦아
#: 리포트에 표시한다 — 세지 않으면 프롬프트를 고칠 근거가 남지 않는다.
BANNED_PHRASES: tuple[str, ...] = (
    "본 문서는",
    "본 보고서는",
    "에 관한 보고서",
    "을 다룬다",
    "을 포함한다",
    "한 상황이다",
)

STYLE = """
:root {
  --bg:#f6f7f9; --fg:#14181f; --muted:#5b6472; --line:#dfe3e8; --card:#fff;
  --accent:#2b6cb0; --warn:#b7791f; --bad:#c53030; --ok:#2f855a;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#12151a; --fg:#e6e9ee; --muted:#9aa4b2; --line:#2a3038;
           --card:#1a1f26; --accent:#7cb3e8; --warn:#e0b050; --bad:#f08a86; --ok:#7bcf9e; }
}
* { box-sizing:border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
  font:14px/1.65 -apple-system,"Segoe UI","Malgun Gothic",sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:var(--muted); margin:0 0 20px; font-size:13px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:18px; margin-bottom:22px; overflow:hidden; }
.card h2 { font-size:15px; margin:0 0 14px; font-weight:600; word-break:break-all; }
.n { display:inline-block; background:var(--accent); color:#fff; border-radius:5px;
  padding:1px 7px; margin-right:8px; font-size:12px; }
.fail { border-left:4px solid var(--bad); }
.failmsg { margin:0 0 8px; font-size:12px; color:var(--bad); }
.flow { display:flex; align-items:stretch; gap:8px; flex-wrap:wrap;
  padding:12px; background:var(--bg); border-radius:8px; margin-bottom:16px; }
.step { flex:1 1 130px; min-width:130px; }
.steplabel { font-size:11px; color:var(--muted); margin-bottom:5px; }
.arrow { align-self:center; color:var(--muted); font-size:18px; }
.pill { display:inline-block; border:1px solid var(--line); border-radius:20px;
  padding:2px 10px; font-size:12px; font-weight:600; background:var(--card); }
.pill.label-O { border-color:var(--ok); color:var(--ok); }
.pill.label-S { border-color:var(--bad); color:var(--bad); }
.pill.target { border-color:var(--accent); color:var(--accent); }
.pill.dead { border-color:var(--line); color:var(--muted); font-weight:400; }
.meta { font-size:11px; color:var(--muted); margin-top:4px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(290px,1fr)); gap:16px; }
.col h3 { font-size:13px; margin:0 0 8px; padding-bottom:5px;
  border-bottom:1px solid var(--line); }
.col h4 { font-size:12px; margin:14px 0 5px; color:var(--muted); }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; vertical-align:top; color:var(--muted); font-weight:500;
  width:88px; padding:4px 8px 4px 0; white-space:nowrap; }
td { vertical-align:top; padding:4px 0; word-break:break-word; }
tr + tr th, tr + tr td { border-top:1px solid var(--line); }
.summary { overflow-x:auto; }
.summary table { font-size:12px; min-width:760px; }
.summary th { width:auto; white-space:nowrap; padding:6px 10px 6px 0;
  border-bottom:1px solid var(--line); }
.summary td { padding:6px 10px 6px 0; white-space:nowrap; }
.summary td.wrap { white-space:normal; }
pre { margin:0; padding:10px; background:var(--bg); border:1px solid var(--line);
  border-radius:6px; font:11.5px/1.6 ui-monospace,Consolas,monospace;
  white-space:pre-wrap; word-break:break-word; max-height:420px; overflow:auto; }
pre.gen { border-left:3px solid var(--accent); }
ul { margin:0; padding-left:16px; font-size:12px; }
ul.spans li { margin-bottom:5px; word-break:break-word; }
code { font:11.5px ui-monospace,Consolas,monospace; background:var(--bg);
  border:1px solid var(--line); border-radius:3px; padding:0 4px; }
.chip { display:inline-block; background:var(--bg); border:1px solid var(--line);
  border-radius:11px; padding:1px 8px; font-size:11px; margin:1px 2px 1px 0; }
.chip.ok { border-color:var(--ok); color:var(--ok); }
.chip.bad { border-color:var(--bad); color:var(--bad); }
.none { color:var(--muted); font-style:italic; font-size:12px; }
.warn { color:var(--warn); font-size:11px; }
.ok { color:var(--ok); }
.bad { color:var(--bad); }
"""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _chips(values, *, empty: str = "없음") -> str:
    items = list(values or ())
    if not items:
        return f'<span class="none">{esc(empty)}</span>'
    return "".join(f'<span class="chip">{esc(item)}</span>' for item in items)


def _rows(pairs) -> str:
    body = "".join(
        f"<tr><th>{esc(key)}</th><td>{value}</td></tr>"
        for key, value in pairs
        if value is not None
    )
    return f"<table>{body}</table>"


def _target_text(target: dict | None) -> str:
    if not target:
        return "-"
    parts = [target.get("classification", "?")]
    if target.get("clause_no"):
        parts.append(f"제{target['clause_no']}호")
    if target.get("subclause_key"):
        parts.append(target["subclause_key"])
    return " / ".join(str(part) for part in parts)


def _header_keys(body: str) -> list[str]:
    """생성문 첫 단락에서 표제부 항목 이름을 뽑는다.

    ``generated_body``는 IR을 평문으로 이어 붙인 것이라 첫 ``key_value`` block이
    ``키: 값`` 줄로 나온다.
    """

    if not body:
        return []
    head = body.split("\n\n")[0]
    return [line.split(":", 1)[0].strip() for line in head.splitlines() if ":" in line]


def _banned_hits(body: str) -> list[str]:
    return [phrase for phrase in BANNED_PHRASES if phrase in (body or "")]


def _flow(record: dict) -> str:
    body = record.get("generated_body") or ""
    classification = record.get("source_classification")
    validation_form = record.get("validation_document_form")

    requested = _target_text(record.get("requested_target"))
    final = record.get("generation_final_target")
    final_text = _target_text(final)
    swapped = "" if requested == final_text else f"req={esc(requested)} → 대체됨<br>"

    steps = [
        (
            "1. 원문",
            f'<span class="pill">{esc(record.get("source_document_form") or "-")}</span>',
            f'수집 라벨 {esc(record.get("collected_doc_type"))}<br>'
            f'block {esc(record.get("source_block_count"))}개',
        ),
        (
            "2. 분별기 판정",
            f'<span class="pill label-{esc(classification)}">{esc(classification)}</span>',
            esc(record.get("source_evidence_level") or "조항 없음"),
        ),
        (
            "3. 심은 목표",
            (
                f'<span class="pill target">{esc((final or {}).get("subclause_key") or "-")}</span>'
                if final
                # 분류 단계에서 죽으면 계획은 시도조차 안 됐다 — 단계를 단정하지 않는다.
                else '<span class="pill dead">미도달</span>'
            ),
            (swapped + esc((record.get("generation_route") or "-"))) if final else "-",
        ),
        (
            "4. 생성물",
            (
                f'<span class="pill">{len(body)}자</span>'
                if body
                else '<span class="pill dead">없음</span>'
            ),
            (
                ("표제부 OK" if record.get("form_has_header") else "표제부 미달")
                if body
                else "-"
            ),
        ),
        (
            "5. 독립 검사",
            (
                f'<span class="pill">{esc(validation_form)}</span>'
                if validation_form
                else '<span class="pill dead">미도달</span>'
            ),
            (
                f'{esc(record.get("validation_classification"))} / '
                f'{esc(record.get("validation_subclause") or "조항 없음")}'
                if validation_form
                else "-"
            ),
        ),
    ]

    cells = []
    for label, pill, meta in steps:
        cells.append(
            f'<div class="step"><div class="steplabel">{esc(label)}</div>'
            f'{pill}<div class="meta">{meta}</div></div>'
        )
    return '<div class="flow">' + '<div class="arrow">→</div>'.join(cells) + "</div>"


def _source_column(record: dict) -> str:
    return (
        "<div class=\"col\"><h3>1. 원문 (발췌)</h3>"
        + _rows(
            (
                ("제목", esc(record.get("source_title"))),
                ("출처", f'<code>{esc(record.get("source"))}</code>'),
                ("수집 라벨", f'<code>{esc(record.get("collected_doc_type"))}</code>'),
            )
        )
        + f'<h4>source_excerpt</h4><pre>{esc(record.get("source_excerpt"))}</pre></div>'
    )


def _assessment_column(record: dict) -> str:
    quotes = record.get("source_evidence_quotes") or ()
    spans = (
        "<ul class=\"spans\">"
        + "".join(f"<li>{esc(quote)}</li>" for quote in quotes)
        + "</ul>"
        if quotes
        else '<span class="none">없음</span>'
    )
    return (
        "<div class=\"col\"><h3>2. 분별기가 낸 판정</h3>"
        + _rows(
            (
                ("문서형식", f'<code>{esc(record.get("source_document_form"))}</code>'),
                ("S/O", esc(record.get("source_classification"))),
                ("조항", esc(record.get("source_clause") or "없음")),
                ("세부조항", esc(record.get("source_subclause") or "없음")),
                ("근거 수준", f'<code>{esc(record.get("source_evidence_level"))}</code>'),
                ("판정 사유코드", f'<code>{esc(record.get("source_reason_code"))}</code>'),
                ("업무 맥락", esc(record.get("source_business_context"))),
                ("등장 역할", _chips(record.get("source_subject_roles"))),
                (
                    "가장 가까운 세부조항",
                    (
                        f'<code>{esc(record.get("primary_subclause"))}</code>'
                        if record.get("primary_subclause")
                        else '<span class="none">없음</span>'
                    ),
                ),
                ("그 판단 근거", esc(record.get("primary_rationale"))),
                ("다음 후보", _chips(record.get("compatible_subclauses"))),
            )
        )
        + f'<h4>rationale</h4><div style="font-size:12px">{esc(record.get("source_rationale"))}</div>'
        + f"<h4>evidence_spans (원문에서 짚은 근거)</h4>{spans}</div>"
    )


def _plan_column(record: dict) -> str:
    requested = record.get("requested_target") or {}
    final = record.get("generation_final_target")
    note = (
        '<span class="warn">원문과 맞지 않아 계획기가 대체함</span>'
        if final and _target_text(requested) != _target_text(final)
        else None
    )
    return (
        "<div class=\"col\"><h3>3. 생성 지시</h3>"
        + _rows(
            (
                ("요청 목표", f"<code>{esc(_target_text(requested))}</code>"),
                (
                    "잠긴 목표",
                    f"<code>{esc(_target_text(final))}</code>" if final else '<span class="none">계획 단계에서 종료</span>',
                ),
                ("route", f'<code>{esc(record.get("generation_route") or "-")}</code>'),
                ("대체 여부", note),
                (
                    "시도 횟수",
                    esc(record.get("generation_attempt_index"))
                    if record.get("generation_attempt_index")
                    else None,
                ),
                (
                    "repair codes",
                    _chips(record.get("generation_repair_codes"), empty="없음")
                    if record.get("generation_attempt_index")
                    else None,
                ),
            )
        )
        + "</div>"
    )


def _generated_column(record: dict) -> str:
    body = record.get("generated_body")
    if not body:
        return (
            "<div class=\"col\"><h3>4. 생성된 문서</h3>"
            '<p class="none">생성 이전 단계에서 종료돼 본문이 없다.</p></div>'
        )

    keys = _header_keys(body)
    header_chip = (
        f'<span class="chip ok">표제부 충족</span>'
        if record.get("form_has_header")
        else f'<span class="chip bad">표제부 미달</span>'
    )
    missing = record.get("form_missing") or ()
    banned = _banned_hits(body)
    return (
        "<div class=\"col\"><h3>4. 생성된 문서</h3>"
        + _rows(
            (
                ("제목", esc(record.get("generated_title"))),
                ("block 구성", _chips(record.get("generated_blocks"))),
                ("표제부 항목", _chips(keys, empty="key_value 없음")),
                (
                    "형식 검사",
                    header_chip
                    + (
                        f'<div class="meta">{esc(", ".join(missing))}</div>'
                        if missing
                        else ""
                    ),
                ),
                (
                    "금지 문형",
                    (
                        "".join(f'<span class="chip bad">{esc(p)}</span>' for p in banned)
                        if banned
                        else '<span class="chip ok">없음</span>'
                    ),
                ),
            )
        )
        + f'<h4>generated_body</h4><pre class="gen">{esc(body)}</pre></div>'
    )


def _validation_column(record: dict) -> str:
    if not record.get("validation_document_form"):
        return (
            "<div class=\"col\"><h3>5. validator 독립 판정</h3>"
            '<p class="none">생성물이 독립 검사에 도달하지 못했다.</p></div>'
        )

    comparison = record.get("comparison") or {}
    quotes = record.get("validation_evidence_quotes") or ()
    matches = "".join(
        f'<span class="chip {"ok" if value else "bad"}">{esc(key)}</span>'
        for key, value in comparison.items()
        if key != "requires_review"
    )
    spans = (
        "<ul class=\"spans\">" + "".join(f"<li>{esc(q)}</li>" for q in quotes) + "</ul>"
        if quotes
        else '<span class="none">없음</span>'
    )
    return (
        "<div class=\"col\"><h3>5. validator 독립 판정</h3>"
        + _rows(
            (
                ("문서형식", f'<code>{esc(record.get("validation_document_form"))}</code>'),
                ("S/O", esc(record.get("validation_classification"))),
                ("조항", esc(record.get("validation_clause") or "없음")),
                ("세부조항", esc(record.get("validation_subclause") or "없음")),
                (
                    "행정상태 반영",
                    f'<code>{esc(record.get("validation_effective_classification"))}</code>',
                ),
                ("판별기와 비교", matches),
            )
        )
        + f'<h4>rationale</h4><div style="font-size:12px">{esc(record.get("validation_rationale"))}</div>'
        + f"<h4>evidence_spans</h4>{spans}</div>"
    )


def _summary_card(records: list[dict]) -> str:
    head = (
        "<tr><th>#</th><th>문서</th><th>수집 라벨</th><th>판별 형식</th>"
        "<th>생성 표제부</th><th>형식검사</th><th>validator 형식</th>"
        "<th>결과</th></tr>"
    )
    body = []
    for index, record in enumerate(records, 1):
        keys = _header_keys(record.get("generated_body") or "")
        source_form = record.get("source_document_form")
        validation_form = record.get("validation_document_form")
        if validation_form is None:
            form_cell = '<span class="none">미도달</span>'
        elif validation_form == source_form:
            form_cell = f'<span class="ok">{esc(validation_form)}</span>'
        else:
            form_cell = f'<span class="bad">{esc(validation_form)}</span>'

        if record.get("succeeded"):
            result = '<span class="ok">완료</span>'
        else:
            result = (
                f'<span class="bad">{esc(record.get("failure_stage"))}</span>'
                f'<div class="meta">{esc(record.get("failure_code"))}</div>'
            )
        body.append(
            f"<tr><td>{index:02d}</td>"
            f'<td><code>{esc(record.get("source_document_id"))}</code></td>'
            f'<td>{esc(record.get("collected_doc_type"))}</td>'
            f'<td>{esc(source_form)}</td>'
            f'<td class="wrap">{" / ".join(esc(k) for k in keys) or "<span class=\'none\'>없음</span>"}</td>'
            f'<td>{"OK" if record.get("form_has_header") else ("-" if not record.get("generated_body") else "미달")}</td>'
            f"<td>{form_cell}</td><td>{result}</td></tr>"
        )

    generated = [r for r in records if r.get("generated_body")]
    header_ok = sum(1 for r in generated if r.get("form_has_header"))
    kept = [
        r
        for r in records
        if r.get("validation_document_form")
        and r["validation_document_form"] == r.get("source_document_form")
    ]
    banned = [r for r in generated if _banned_hits(r.get("generated_body") or "")]
    stages = Counter(
        f'{r.get("failure_stage")}/{r.get("failure_code")}'
        for r in records
        if not r.get("succeeded")
    )

    stats = _rows(
        (
            ("표제부 충족", f"{header_ok}/{len(generated)} (생성된 문서 기준)"),
            (
                "원문 형식 유지",
                f"{len(kept)}/{len(records) - len([r for r in records if not r.get('validation_document_form')])}"
                " (validator가 같은 형식으로 판정)",
            ),
            ("금지 문형 발생", f"{len(banned)}/{len(generated)}"),
            (
                "실패 단계",
                _chips(f"{key} × {value}" for key, value in stages.most_common())
                if stages
                else '<span class="chip ok">없음</span>',
            ),
        )
    )

    return (
        f'<section class="card"><h2><span class="n">요약</span>{len(records)}건 한눈에</h2>'
        f'{stats}<div class="summary"><table>{head}{"".join(body)}</table></div></section>'
    )


def _document_card(index: int, record: dict) -> str:
    failed = not record.get("succeeded")
    classes = "card fail" if failed else "card"
    failmsg = (
        f'<p class="failmsg"><b>{esc(record.get("failure_stage"))}</b> 단계에서 종료 · '
        f'<code>{esc(record.get("failure_code"))}</code><br>'
        f'{esc(record.get("failure_message"))}</p>'
        if failed
        else ""
    )
    return (
        f'<section class="{classes}">'
        f'<h2><span class="n">{index:02d}</span>{esc(record.get("source_title"))}'
        f' <code>{esc(record.get("source_document_id"))}</code></h2>'
        f"{failmsg}{_flow(record)}"
        '<div class="grid">'
        f"{_source_column(record)}{_assessment_column(record)}{_plan_column(record)}"
        f"{_generated_column(record)}{_validation_column(record)}"
        "</div></section>"
    )


def render(records: list[dict], *, bundle_version: str, models: str) -> str:
    done = sum(1 for r in records if r.get("succeeded"))
    cards = "".join(
        _document_card(index, record) for index, record in enumerate(records, 1)
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>원문 판별 → 생성 전 과정 ({len(records)}건)</title>
<style>{STYLE}</style></head><body>
<h1>원문 판별 → 조항 목표 → 생성까지 전 과정 (실제 원문 {len(records)}건)</h1>
<p class="sub">생성 성공 <b>{done}/{len(records)}</b> ·
prompt bundle <code>{esc(bundle_version)}</code> ·
classifier/generator <code>{esc(models)}</code>
<br>이 버전에서 바뀐 것: 생성기가 <b>원문의 문서형식을 그대로 사용</b>하고,
표제부 항목도 <b>형식별로</b> 다르게 쓴다 — 회의록이면 회차·개최일시, 점검보고서면
점검일시·점검대상. 이전에는 모든 문서에 공문 서식(문서번호·수신·시행일자)을 요구했다.
<br>문서형식 정의와 형식별 표제부 표는 생성기 프롬프트에서 <b>"문서형식을 그대로
사용한다"는 지시 바로 뒤</b>에 온다. 같은 표를 <code>check_document_form</code>이
채점에 쓰므로 지시와 지표가 어긋나지 않는다.
<br>요청 목표는 <b>문서와 무관하게 라운드로빈으로 배정</b>했고, 원문과 맞지 않으면
계획기가 <b>판별기가 추천한 1순위로 대체</b>한다.</p>
{_summary_card(records)}
{cards}
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bundle-version", default="")
    parser.add_argument("--models", default="gpt-4o")
    args = parser.parse_args()

    records = [
        json.loads(line)
        for line in args.records.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    bundle_version = args.bundle_version
    if not bundle_version:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from rd2.source_generation.prompts import PROMPT_BUNDLE_VERSION

        bundle_version = PROMPT_BUNDLE_VERSION

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        render(records, bundle_version=bundle_version, models=args.models),
        encoding="utf-8",
    )
    print(f"{len(records)}건 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
