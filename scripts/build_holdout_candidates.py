"""프로덕션 DB의 공개(O) 문서에서 held-out 평가 후보와 snapshot을 만든다.

**왜 O 문서만인가.** 실측 결과 실제 C/S 라벨이 붙은 문서(PRISM S 1,544 + C 122)는
전부 본문이 없다. 비공개 문서라 접근제어를 존중해 내용을 받지 않기 때문이고 이는
의도된 동작이다. 따라서 "진짜 민감 문서를 민감이라 맞히는가"(재현율)는 이 코퍼스로
잴 수 없다. 반대 방향인 **오탐** — 실제로 공개된 문서를 C/S로 잘못 찍는 비율 — 은
공개 문서만으로 측정 가능하고, 높으면 채점자가 표면 단서(감사·입찰·개인정보 같은
낱말)만 보고 민감 판정을 남발한다는 뜻이라 그 자체로 신호다.

**정답 근거.** 이 문서들은 기관이 실제로 대외 공개한 것이다. 정보공개법 제9조는
비공개 대상 정보를 규정하므로, 이미 공개된 문서의 정답은 O다. 본문에 감사·입찰
관련 서술이 있어도 그렇다 — 그 서술이 있다고 공개된 문서가 비공개가 되지는 않는다.

**표본 편향(기록).** 본문 길이가 ``--min-chars``~``--max-chars`` 범위인 문서만
쓴다. 잘라내면 난이도가 달라져 비교가 흐려지므로 자르는 대신 제외한다. 아주 짧은
문서와 아주 긴 문서는 이 표본에 없다.

사용 예:

    python scripts/build_holdout_candidates.py --out-dir data/holdout --per-stratum 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymysql  # noqa: E402

from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    SemanticDocumentType,
)

BLOCKS_PER_PAGE = 12


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ["MARIADB_HOST"],
        port=int(os.environ["MARIADB_PORT"]),
        user=os.environ["MARIADB_USER"],
        password=os.environ["MARIADB_PASSWORD"],
        database=os.environ["MARIADB_DATABASE"],
        charset="utf8mb4",
    )


def _blocks_from_body(body: str) -> list[str]:
    """본문을 문단 단위 block으로 자른다. 내용은 바꾸지 않는다."""

    parts = [chunk.strip() for chunk in body.split("\n\n")]
    blocks = [chunk for chunk in parts if chunk]
    if len(blocks) <= 1:
        blocks = [line.strip() for line in body.splitlines() if line.strip()]
    return blocks


def _build_snapshot(document_id: str, source: str, body: str) -> dict | None:
    blocks = _blocks_from_body(body)
    if not blocks:
        return None
    pages = []
    for page_index in range(0, len(blocks), BLOCKS_PER_PAGE):
        page_number = page_index // BLOCKS_PER_PAGE + 1
        pages.append(
            {
                "page_number": page_number,
                "blocks": [
                    {
                        "block_id": f"p{page_number}:b{offset}",
                        "text": text,
                    }
                    for offset, text in enumerate(
                        blocks[page_index : page_index + BLOCKS_PER_PAGE]
                    )
                ],
            }
        )
    return {
        "source_document_id": document_id,
        "source": source,
        "manifest_key": "holdout-open-overflagging",
        "source_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "pages": pages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--per-stratum", type=int, default=8)
    parser.add_argument("--min-chars", type=int, default=800)
    parser.add_argument("--max-chars", type=int, default=12_000)
    args = parser.parse_args()

    known_types = {item.value for item in SemanticDocumentType}

    connection = _connect()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT id, source, doc_type, title, body_text
        FROM documents
        WHERE cso_classification = 'O'
          AND body_text IS NOT NULL
          AND CHAR_LENGTH(body_text) BETWEEN %s AND %s
        ORDER BY id
        """,
        (args.min_chars, args.max_chars),
    )

    per_stratum: dict[tuple[str, str], int] = {}
    candidates: list[dict] = []
    snapshots: dict[str, dict] = {}
    titles: dict[str, str] = {}
    skipped_unknown_type = 0

    for row_id, source, doc_type, title, body in cursor.fetchall():
        if doc_type not in known_types:
            skipped_unknown_type += 1
            continue
        stratum = (source, doc_type)
        if per_stratum.get(stratum, 0) >= args.per_stratum:
            continue

        document_id = f"{source}-{row_id}"
        snapshot = _build_snapshot(document_id, source, body)
        if snapshot is None:
            continue

        per_stratum[stratum] = per_stratum.get(stratum, 0) + 1
        candidates.append(
            {
                "case_id": f"{source}-{doc_type}-{row_id}",
                "source_document_id": document_id,
                "source_sha256": snapshot["source_sha256"],
                "document_type": doc_type,
                "classification": "O",
                "clause_no": None,
                "subclause_key": None,
            }
        )
        snapshots[document_id] = snapshot
        titles[document_id] = (title or document_id)[:200]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("candidates.json", candidates),
        ("snapshots.json", snapshots),
        ("titles.json", titles),
    ):
        (args.out_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"후보 {len(candidates)}건 ({len(per_stratum)}개 stratum)")
    for stratum, count in sorted(per_stratum.items()):
        print(f"  {stratum[0]:<12} {stratum[1]:<22} {count}")
    if skipped_unknown_type:
        print(f"taxonomy에 없는 doc_type으로 제외: {skipped_unknown_type}건")
    print(f"저장: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
