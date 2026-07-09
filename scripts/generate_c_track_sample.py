"""C트랙 제1호 합성 샘플 1건 생성 검증 (The Assignment 2번째 실행, 텍스트만).

Genalog 이미지 증강은 후속 단계 — 이 스크립트는 텍스트 생성 + 스키마 검증 +
저장까지만 확인한다.
"""

from __future__ import annotations

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.generators.generate import generate_clause_document  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402


def main() -> None:
    doc = generate_clause_document("1", scenario_index=0)

    with DocumentStore() as store:
        stored = store.upsert(doc)
        print(f"[{'stored' if stored else 'dup-skip'}] {doc.title!r}")
        print(f"    cso_classification={doc.cso_classification.value!r} sub_clause={doc.cso_sub_clause!r}")
        print(f"    is_synthetic={doc.is_synthetic!r} source={doc.source!r}")
        print(f"    disclosure_status={doc.disclosure_status.value!r}")
        print()
        print("=== 생성된 본문 (텍스트만, Genalog 미적용) ===")
        print(doc.body_text)
        print()
        print(f"Total C-track docs in DB: {store.count_documents(cso_classification='C')}")


if __name__ == "__main__":
    main()
