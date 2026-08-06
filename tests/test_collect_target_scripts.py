import sys
from pathlib import Path

import pytest

import collect_moe
import collect_mohw
import collect_molit


@pytest.mark.parametrize("module", [collect_moe, collect_mohw, collect_molit])
def test_existing_document_is_skipped_before_detail_parse(monkeypatch, tmp_path: Path, module):
    checkpoints: list[int] = []

    class FakeAdapter:
        source_name = "test-source"

        def fetch_list(self, *, skip: int, max_items: int):
            assert skip == 0
            assert max_items == 1
            yield {"_detail_url": "https://example.test/detail/1", "_title": "기존 문서"}

        def parse_detail(self, _raw_item):
            raise AssertionError("이미 저장된 문서는 상세 파싱하면 안 됨")

    class FakeStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def has_document(self, source: str, source_url: str | None) -> bool:
            assert source == "test-source"
            assert source_url == "https://example.test/detail/1"
            return True

        def count_documents(self, **_filters) -> int:
            return 0

    adapter_name = next(
        name
        for name in ("MoeAdapter", "MohwAdapter", "MolitAdapter")
        if hasattr(module, name)
    )
    monkeypatch.setattr(module, adapter_name, FakeAdapter)
    monkeypatch.setattr(module, "DocumentStore", FakeStore)
    monkeypatch.setattr(
        module,
        "_save_checkpoint",
        lambda _path, processed: checkpoints.append(processed),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [module.__name__, "1", "--checkpoint", str(tmp_path / "checkpoint.json")],
    )

    module.main()

    assert checkpoints == [1]
