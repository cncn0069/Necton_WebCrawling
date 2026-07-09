from datetime import date

import pytest

from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.db import DocumentStore
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_OPEN_GO_KR, SOURCE_PRISM


@pytest.fixture
def store(tmp_path):
    db = DocumentStore(tmp_path / "test.db")
    yield db
    db.close()


def _doc(**overrides):
    kwargs = dict(
        title="테스트 문서",
        ordering_agency="테스트기관",
        disclosure_status=DisclosureStatus.OPEN,
        body_text="본문",
        cso_classification=CsoClassification.O,
        source=SOURCE_OPEN_GO_KR,
        source_url="https://open.go.kr/doc/1",
        is_synthetic=False,
    )
    kwargs.update(overrides)
    return Document(**kwargs)


def test_upsert_stores_document(store):
    assert store.upsert(_doc()) is True
    assert store.count_documents() == 1


def test_upsert_dedup_same_source_url_skipped(store):
    assert store.upsert(_doc()) is True
    assert store.upsert(_doc()) is False  # 동일 source+URL — 중복
    assert store.count_documents() == 1


def test_upsert_different_url_not_deduped(store):
    store.upsert(_doc(source_url="https://open.go.kr/doc/1"))
    store.upsert(_doc(source_url="https://open.go.kr/doc/2"))
    assert store.count_documents() == 2


def test_synthetic_docs_without_url_never_deduped(store):
    for _ in range(3):
        store.upsert(
            _doc(
                cso_classification=CsoClassification.C,
                cso_sub_clause="1",
                source="synthetic-llm",
                source_url=None,
                is_synthetic=True,
            )
        )
    assert store.count_documents(cso_classification="C") == 3


def test_quarantine_stores_failed_record(store):
    store.quarantine({"title": "broken"}, "is_synthetic mismatch")
    assert store.count_quarantine() == 1


def test_pending_download_lifecycle(store):
    doc = _doc(source=SOURCE_PRISM, source_url="https://www.prism.go.kr/homepage/asmt/1")
    store.upsert(doc)
    store.mark_pending_download(doc)

    pending = store.list_pending_downloads(SOURCE_PRISM)
    assert len(pending) == 1
    assert pending[0]["source_url"] == doc.source_url

    dedup_key = pending[0]["dedup_key"]
    assert store.get_title(dedup_key) == doc.title

    store.update_files(
        dedup_key,
        f"{SOURCE_PRISM}/{DOC_TYPE_RESEARCH_REPORT}/1_본문.pdf",
        [f"{SOURCE_PRISM}/{DOC_TYPE_RESEARCH_REPORT}/1_부속.pdf"],
    )
    store.clear_pending_download(dedup_key)

    assert store.list_pending_downloads(SOURCE_PRISM) == []


def test_mark_pending_download_is_idempotent(store):
    doc = _doc(source=SOURCE_PRISM, source_url="https://www.prism.go.kr/homepage/asmt/1")
    store.upsert(doc)
    store.mark_pending_download(doc)
    store.mark_pending_download(doc)
    assert len(store.list_pending_downloads(SOURCE_PRISM)) == 1


def test_mark_pending_download_skips_synthetic_docs_without_url(store):
    doc = _doc(
        source="synthetic-llm",
        source_url=None,
        cso_classification=CsoClassification.C,
        cso_sub_clause="1",
        is_synthetic=True,
    )
    store.upsert(doc)
    store.mark_pending_download(doc)
    assert store.list_pending_downloads("synthetic-llm") == []
