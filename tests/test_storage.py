from datetime import date

import pytest

from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.db import DocumentStore
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_OPEN_GO_KR, SOURCE_PRISM


@pytest.fixture
def store():
    db = DocumentStore(database="rd2_test")
    # 매 테스트가 빈 테이블에서 시작하도록 정리 — 로컬 MariaDB rd2_test는
    # 세션 내내 살아있는 DB라 tmp_path 방식(테스트마다 새 파일)과 달리
    # 명시적으로 비워줘야 한다. rd2_test는 실 작업 DB(rd2_dump)와 완전히
    # 분리된 전용 테스트 DB — 절대 실 데이터가 있는 DB를 가리키면 안 된다
    # (2026-07-13: 예전에 둘이 같은 DB를 공유해 TRUNCATE가 실 데이터를
    # 지워버린 사고가 있었음).
    for table in ("documents", "quarantine", "pending_downloads"):
        with db._conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {table}")
    db._conn.commit()
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


def test_upsert_includes_ref_id_without_touching_a_database():
    class FakeCursor:
        def __init__(self):
            self.executed = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            self.executed = (sql, params)

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()
            self.committed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("unexpected rollback")

    connection = FakeConnection()
    fake_store = DocumentStore.__new__(DocumentStore)
    fake_store._conn = connection

    assert fake_store.upsert(_doc(ref_id=42)) is True

    sql, params = connection.cursor_instance.executed
    columns = sql.partition("(")[2].partition(")")[0].split(", ")
    assert params[columns.index("ref_id")] == 42
    assert connection.committed is True


def test_upsert_dedup_same_source_url_skipped(store):
    assert store.upsert(_doc()) is True
    assert store.upsert(_doc()) is False  # 동일 source+URL — 중복
    assert store.count_documents() == 1


def test_has_document_uses_source_and_source_url(store):
    assert store.has_document(SOURCE_OPEN_GO_KR, "https://open.go.kr/doc/1") is False
    store.upsert(_doc())
    assert store.has_document(SOURCE_OPEN_GO_KR, "https://open.go.kr/doc/1") is True
    assert store.has_document(SOURCE_PRISM, "https://open.go.kr/doc/1") is False
    assert store.has_document(SOURCE_OPEN_GO_KR, None) is False


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


def _generated_yn(store, dedup_key_like: str) -> bytes:
    with store._conn.cursor() as cur:
        cur.execute(
            "SELECT generated_yn FROM documents WHERE dedup_key LIKE %s",
            (dedup_key_like,),
        )
        return cur.fetchone()[0]


def test_upsert_stores_generated_yn_0_for_collected_docs(store):
    store.upsert(_doc())
    assert _generated_yn(store, f"{SOURCE_OPEN_GO_KR}::%") == b"0"


def test_upsert_stores_generated_yn_1_for_generated_docs(store):
    """생성 문서는 '1' — is_synthetic에서 파생되므로 수집기/생성기가 따로
    설정할 필요가 없다. BINARY(1) 컬럼이라 ASCII 한 글자로 들어간다."""
    store.upsert(
        _doc(
            cso_classification=CsoClassification.S,
            cso_sub_clause="5",
            source="gen_alio",
            source_url="synthetic://source-generation/alio-1/5-none",
            is_synthetic=True,
        )
    )
    assert _generated_yn(store, "gen_alio::%") == b"1"


def test_count_documents_filters_by_generated_yn(store):
    store.upsert(_doc())
    store.upsert(_doc(source="gen_alio", source_url=None, is_synthetic=True))
    assert store.count_documents() == 2
    assert store.count_documents(generated_yn="0") == 1
    assert store.count_documents(generated_yn="1") == 1


def test_upsert_stores_generation_provenance(store):
    """input_prompt/content/generated_text/ref_id는 준 그대로 들어간다."""
    store.upsert(
        _doc(
            cso_classification=CsoClassification.S,
            cso_sub_clause="5",
            source="gen_alio",
            source_url="synthetic://source-generation/alio-7/5-none",
            is_synthetic=True,
            input_prompt="생성기 프롬프트",
            content="원문 앞 40쪽",
            generated_text="생성된 본문",
            ref_id=7,
        )
    )
    with store._conn.cursor() as cur:
        cur.execute(
            "SELECT input_prompt, content, generated_text, ref_id FROM documents "
            "WHERE dedup_key LIKE %s",
            ("gen_alio::%",),
        )
        assert cur.fetchone() == ("생성기 프롬프트", "원문 앞 40쪽", "생성된 본문", 7)


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
