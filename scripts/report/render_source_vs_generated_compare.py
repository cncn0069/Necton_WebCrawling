"""출처별 배치 결과의 원문 ↔ 생성물 좌우 비교 뷰어(단일 HTML)를 만든다.

사용:
    python scripts/render_source_vs_generated_compare.py output/allsources_gated_20260803
"""

from __future__ import annotations

import argparse
import html
import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

STATUS_LABEL = {
    "accepted_s": "S 학습 승인",
    "hard_case_review": "검수 필요",
    "excluded_after_retry": "재생성 후 제외",
    "pipeline_failed": "파이프라인 실패",
}


def _split_blocks(text: str) -> list[str]:
    return (text or "").split("\n\n")


def _char_diff(left: str, right: str) -> tuple[str, str]:
    """블록 안에서 바뀐 글자 구간만 표시로 감싼 (좌, 우) HTML을 돌려준다."""
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    lhs: list[str] = []
    rhs: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        lseg = html.escape(left[i1:i2])
        rseg = html.escape(right[j1:j2])
        if tag == "equal":
            lhs.append(lseg)
            rhs.append(rseg)
            continue
        if lseg:
            lhs.append(f"<del>{lseg}</del>")
        if rseg:
            rhs.append(f"<ins>{rseg}</ins>")
    return "".join(lhs), "".join(rhs)


def _align(source_blocks: list[str], generated_blocks: list[str]) -> list[dict[str, Any]]:
    matcher = SequenceMatcher(None, source_blocks, generated_blocks, autojunk=False)
    rows: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                text = html.escape(source_blocks[i1 + offset])
                rows.append({"op": "equal", "l": text, "r": text})
            continue
        if tag == "replace":
            span = max(i2 - i1, j2 - j1)
            for offset in range(span):
                left = source_blocks[i1 + offset] if i1 + offset < i2 else ""
                right = generated_blocks[j1 + offset] if j1 + offset < j2 else ""
                if left and right:
                    lhtml, rhtml = _char_diff(left, right)
                    rows.append({"op": "replace", "l": lhtml, "r": rhtml})
                elif left:
                    rows.append({"op": "delete", "l": f"<del>{html.escape(left)}</del>", "r": ""})
                else:
                    rows.append({"op": "insert", "l": "", "r": f"<ins>{html.escape(right)}</ins>"})
            continue
        if tag == "delete":
            for offset in range(i1, i2):
                rows.append({"op": "delete", "l": f"<del>{html.escape(source_blocks[offset])}</del>", "r": ""})
            continue
        for offset in range(j1, j2):
            rows.append({"op": "insert", "l": "", "r": f"<ins>{html.escape(generated_blocks[offset])}</ins>"})
    return rows


def _generated_blocks(record: dict[str, Any]) -> list[str]:
    """생성 문서의 블록 텍스트. 상위 generated_blocks는 kind 목록이라 쓰지 않는다."""
    candidates: list[Any] = []
    artifact = record.get("generation_artifact") or {}
    candidates.append((artifact.get("generated_document") or {}).get("blocks"))
    for attempt in reversed(record.get("attempts") or []):
        candidates.append(((attempt.get("generated_document") or {}) or {}).get("blocks"))
    for blocks in candidates:
        if blocks and isinstance(blocks[0], dict):
            return [b.get("text") or "" for b in blocks]
    return _split_blocks(record.get("generated_body") or "") if record.get("generated_body") else []


def _failure_reason(record: dict[str, Any]) -> str:
    for attempt in reversed(record.get("attempts") or []):
        failure = attempt.get("failure")
        if failure:
            if isinstance(failure, dict):
                return str(failure.get("message") or failure.get("code") or failure)
            return str(failure)
    return ""


def _build_document(record: dict[str, Any]) -> dict[str, Any]:
    source_blocks = _split_blocks(record.get("source_text") or "")
    generated_blocks = _generated_blocks(record)
    if generated_blocks:
        rows = _align(source_blocks, generated_blocks)
        changed = sum(1 for row in rows if row["op"] != "equal")
    else:
        rows = [{"op": "equal", "l": html.escape(b), "r": ""} for b in source_blocks]
        changed = 0
    target = record.get("generation_final_target") or {}
    consistency = record.get("consistency_assessment") or {}
    source_assessment = record.get("source_assessment") or {}
    source_classification = source_assessment.get("source_classification") or {}
    return {
        "file": record.get("source_file") or record.get("source_document_id") or "",
        "title": record.get("source_title") or "",
        "status": record.get("approval_status") or "",
        "route": record.get("generation_route") or "",
        "mode": target.get("generation_mode") or "",
        "source_class": source_classification.get("classification") or "",
        "source_form": source_classification.get("document_form") or "",
        "target_class": target.get("classification") or "",
        "clause": target.get("clause_no"),
        "subclause": target.get("subclause_key") or "",
        "verdict": consistency.get("sensitivity_verdict") or "",
        "rationale": consistency.get("rationale") or "",
        "attempts": record.get("attempt_count") or 0,
        "failure": _failure_reason(record),
        "blocks": len(rows),
        "changed": changed,
        "has_generated": bool(generated_blocks),
        "rows": rows,
    }


def build(batch_dir: Path) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for source_dir in sorted(batch_dir.iterdir(), key=lambda p: p.name.lower()):
        records_path = source_dir / "batch_records.jsonl"
        if not records_path.is_file():
            continue
        summary_path = source_dir / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        documents = [
            _build_document(json.loads(line))
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        sources.append(
            {
                "name": source_dir.name,
                "source_dir": summary.get("source_dir") or "",
                "counts": summary.get("approval_counts") or {},
                "generator_model": summary.get("generator_model") or "",
                "prompt_bundle": summary.get("prompt_bundle") or "",
                "documents": documents,
            }
        )
    return {"batch": batch_dir.name, "sources": sources}


PAGE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>원문 ↔ 생성물 비교 · __BATCH__</title>
<style>
  :root {
    --bg:#f6f7f9; --panel:#fff; --line:#e3e6ea; --ink:#16181d; --muted:#6b7280;
    --del:#fdecec; --del-ink:#a61b1b; --ins:#e7f5ec; --ins-ink:#0f6b35; --accent:#1f4fd8;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.6 "Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;
         background:var(--bg); color:var(--ink); }
  header { padding:14px 20px; background:var(--panel); border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:center; flex-wrap:wrap; position:sticky; top:0; z-index:5; }
  header h1 { font-size:16px; margin:0; }
  header .meta { color:var(--muted); font-size:12px; }
  .controls { margin-left:auto; display:flex; gap:14px; align-items:center; font-size:13px; }
  .layout { display:flex; align-items:flex-start; }
  aside { width:290px; flex:none; height:calc(100vh - 52px); overflow:auto; position:sticky; top:52px;
          background:var(--panel); border-right:1px solid var(--line); padding:10px 0; }
  aside .src { padding:8px 14px 4px; font-weight:700; font-size:13px; display:flex; justify-content:space-between;
               cursor:pointer; border-top:1px solid var(--line); }
  aside .src:first-child { border-top:none; }
  aside .src small { font-weight:400; color:var(--muted); }
  aside ul { list-style:none; margin:0 0 8px; padding:0; }
  aside li { padding:5px 14px 5px 20px; font-size:12px; color:#374151; cursor:pointer;
             display:flex; gap:6px; align-items:baseline; }
  aside li:hover { background:#eef2ff; }
  aside li.active { background:#e0e7ff; color:#1e2a78; font-weight:600; }
  aside li span.nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  main { flex:1; min-width:0; padding:18px 22px 60px; }
  .dochead { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin-bottom:14px; }
  .dochead h2 { margin:0 0 8px; font-size:15px; word-break:break-all; }
  .tags { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
  .tag { font-size:11px; padding:2px 8px; border-radius:99px; background:#eef1f5; color:#374151; }
  .tag.ok { background:#e7f5ec; color:var(--ins-ink); }
  .tag.warn { background:#fff4e0; color:#8a5300; }
  .tag.bad { background:var(--del); color:var(--del-ink); }
  .rationale { font-size:12.5px; color:var(--muted); margin:0; }
  table.diff { width:100%; border-collapse:collapse; background:var(--panel);
               border:1px solid var(--line); border-radius:8px; overflow:hidden; table-layout:fixed; }
  table.diff th { font-size:12px; text-align:left; padding:8px 12px; background:#f0f2f5;
                  border-bottom:1px solid var(--line); position:sticky; top:52px; }
  table.diff td { vertical-align:top; padding:7px 12px; border-bottom:1px solid #f0f1f3;
                  white-space:pre-wrap; word-break:break-word; font-size:13px; }
  td.n { width:46px; color:#9aa1ac; font-size:11px; text-align:right; white-space:nowrap; }
  tr.replace td, tr.insert td, tr.delete td { background:#fcfcfd; }
  tr.equal td { color:#4b5563; }
  del { background:var(--del); color:var(--del-ink); text-decoration:none; border-radius:2px; }
  ins { background:var(--ins); color:var(--ins-ink); text-decoration:none; border-radius:2px; }
  .empty { color:#c4c8cf; }
  .note { color:var(--muted); font-size:13px; padding:10px 0; }
</style></head><body>
<header>
  <h1>원문 ↔ 생성물 비교</h1>
  <span class="meta" id="meta"></span>
  <div class="controls">
    <label><input type="checkbox" id="onlyChanged"> 변경된 블록만</label>
    <label><input type="checkbox" id="failOnly"> 실패/제외만</label>
  </div>
</header>
<div class="layout">
  <aside id="nav"></aside>
  <main id="view"></main>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const STATUS = __STATUS__;
const BAD = new Set(['pipeline_failed','excluded_after_retry']);
let cur = [0, 0];

document.getElementById('meta').textContent =
  DATA.batch + ' · 출처 ' + DATA.sources.length + '개 · 문서 ' +
  DATA.sources.reduce((a, s) => a + s.documents.length, 0) + '건';

function statusClass(s) {
  if (s === 'accepted_s') return 'ok';
  if (s === 'hard_case_review') return 'warn';
  return 'bad';
}

function renderNav() {
  const failOnly = document.getElementById('failOnly').checked;
  const nav = document.getElementById('nav');
  nav.innerHTML = '';
  DATA.sources.forEach((src, si) => {
    const head = document.createElement('div');
    head.className = 'src';
    const okCount = src.documents.filter(d => d.status === 'accepted_s').length;
    head.innerHTML = '<span>' + src.name + '</span><small>' + okCount + '/' + src.documents.length + ' 승인</small>';
    nav.appendChild(head);
    const ul = document.createElement('ul');
    src.documents.forEach((doc, di) => {
      if (failOnly && !BAD.has(doc.status)) return;
      const li = document.createElement('li');
      li.className = (si === cur[0] && di === cur[1]) ? 'active' : '';
      const pct = doc.has_generated && doc.blocks
        ? Math.round(doc.changed / doc.blocks * 100) + '%' : '—';
      li.innerHTML = '<span class="tag ' + statusClass(doc.status) + '">' +
        (STATUS[doc.status] || doc.status).slice(0, 2) + '</span>' +
        '<span class="nm" title="' + doc.file.replace(/"/g, '&quot;') + '">' + (di + 1) + '. ' + doc.file + '</span>' +
        '<small style="margin-left:auto;color:#9aa1ac">' + pct + '</small>';
      li.onclick = () => { cur = [si, di]; renderNav(); renderDoc(); };
      ul.appendChild(li);
    });
    nav.appendChild(ul);
  });
}

function renderDoc() {
  const src = DATA.sources[cur[0]];
  const doc = src.documents[cur[1]];
  const onlyChanged = document.getElementById('onlyChanged').checked;
  const tags = [
    ['tag ' + statusClass(doc.status), STATUS[doc.status] || doc.status],
    ['tag', '원문 ' + (doc.source_class || '?') + ' / ' + (doc.source_form || '-')],
    ['tag', '생성 ' + (doc.target_class || '?') +
       (doc.clause ? ' 제' + doc.clause + '호' : '') + (doc.subclause ? ' · ' + doc.subclause : '')],
    ['tag', 'route ' + (doc.route || '-')],
    ['tag', doc.mode || '-'],
    ['tag', '시도 ' + doc.attempts + '회'],
    ['tag' + (doc.has_generated ? '' : ' bad'),
     doc.has_generated ? '변경 블록 ' + doc.changed + '/' + doc.blocks : '생성물 없음'],
  ];
  if (doc.failure) tags.push(['tag bad', doc.failure.slice(0, 120)]);

  const rows = doc.rows
    .map((r, i) => [r, i])
    .filter(([r]) => !onlyChanged || r.op !== 'equal');

  const body = rows.map(([r, i]) =>
    '<tr class="' + r.op + '"><td class="n">' + (i + 1) + '</td><td>' +
    (r.l || '<span class="empty">—</span>') + '</td><td>' +
    (r.r || '<span class="empty">—</span>') + '</td></tr>').join('');

  document.getElementById('view').innerHTML =
    '<div class="dochead"><h2>' + src.name + ' · ' + doc.file + '</h2>' +
    '<div class="tags">' + tags.map(([c, t]) =>
      '<span class="' + c + '">' + String(t).replace(/</g, '&lt;') + '</span>').join('') + '</div>' +
    (doc.rationale ? '<p class="rationale">' + doc.rationale.replace(/</g, '&lt;') + '</p>' : '') +
    '</div>' +
    (rows.length
      ? '<table class="diff"><colgroup><col style="width:46px"><col><col></colgroup>' +
        '<thead><tr><th>#</th><th>원문</th><th>생성물</th></tr></thead><tbody>' + body + '</tbody></table>'
      : '<p class="note">표시할 블록이 없습니다.</p>');
  window.scrollTo(0, 0);
}

document.getElementById('onlyChanged').onchange = renderDoc;
document.getElementById('failOnly').onchange = renderNav;
renderNav();
renderDoc();
</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path, help="출처별 하위 디렉터리를 담은 배치 출력 경로")
    parser.add_argument("--out", type=Path, default=None, help="출력 HTML 경로")
    args = parser.parse_args()

    data = build(args.batch_dir)
    out = args.out or (args.batch_dir / "compare.html")
    page = (
        PAGE.replace("__BATCH__", html.escape(data["batch"]))
        .replace("__STATUS__", json.dumps(STATUS_LABEL, ensure_ascii=False))
        .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    )
    out.write_text(page, encoding="utf-8")
    total = sum(len(s["documents"]) for s in data["sources"])
    print(f"{out} ({out.stat().st_size / 1024:.0f} KB) · 출처 {len(data['sources'])} · 문서 {total}")


if __name__ == "__main__":
    main()
