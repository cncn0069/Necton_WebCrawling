"""candidate_profile.py 회귀 테스트.

설계 문서 §5: "문서별 기관 source는 후보마다 LIKE 쿼리를 실행하지 않는다 ...
DB query 수는 후보 수가 아니라 고유 (source, doc_type) 조합 수 이하이어야 한다."
§5: "DB 연결·timeout·query 오류는 unresolved로 변환하지 않고 planner 전체를
실패시킨다."
"""

import json

import pytest

from rd2.generators.candidate_profile import (
    ProfileRow,
    preload_agency_index,
    profile_candidates,
    write_candidate_profiles_atomic,
)


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._result: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._conn.execute_calls.append(params)
        source, doc_type = params
        self._result = [
            {"body_file_path": row["body_file_path"], "ordering_agency": row["ordering_agency"]}
            for row in self._conn.documents
            if row["source"] == source and row["doc_type"] == doc_type
        ]

    def fetchall(self):
        return self._result


class _FakeConn:
    def __init__(self, documents):
        self.documents = documents
        self.execute_calls: list[tuple] = []

    def cursor(self):
        return _FakeCursor(self)


def _candidate(source, doc_type, doc_id, text=""):
    return {"source": source, "doc_type": doc_type, "doc_id": doc_id, "text": text}


class TestPreloadAgencyIndex:
    def test_batches_by_distinct_source_doc_type(self):
        documents = [
            {
                "source": "alio", "doc_type": "bid_notice",
                "body_file_path": "alio/bid_notice/bucket1/doc-1_file.pdf",
                "ordering_agency": "서울특별시",
            },
            {
                "source": "alio", "doc_type": "bid_notice",
                "body_file_path": "alio/bid_notice/bucket2/doc-2_file.pdf",
                "ordering_agency": "부산광역시",
            },
            {
                "source": "korea_kr", "doc_type": "report",
                "body_file_path": "korea_kr/report/bucketA/doc-9_file.pdf",
                "ordering_agency": "환경부",
            },
        ]
        conn = _FakeConn(documents)
        candidates = [
            _candidate("alio", "bid_notice", "doc-1"),
            _candidate("alio", "bid_notice", "doc-2"),
            _candidate("alio", "bid_notice", "doc-1"),  # 중복 — 그룹은 여전히 1개
            _candidate("korea_kr", "report", "doc-9"),
        ]
        index = preload_agency_index(conn, candidates)
        assert len(conn.execute_calls) == 2  # distinct (source, doc_type) 조합 수
        assert index[("alio", "bid_notice", "doc-1")] == "서울특별시"
        assert index[("alio", "bid_notice", "doc-2")] == "부산광역시"
        assert index[("korea_kr", "report", "doc-9")] == "환경부"

    def test_skips_fixed_agency_sources_since_they_never_need_db(self):
        conn = _FakeConn([])
        candidates = [_candidate("moel", "official_document", "1")]
        index = preload_agency_index(conn, candidates)
        assert index == {}
        assert conn.execute_calls == []

    def test_query_count_bounded_by_distinct_groups_not_candidate_count(self):
        documents = [
            {
                "source": "alio", "doc_type": "bid_notice",
                "body_file_path": f"alio/bid_notice/b/doc-{i}_f.pdf",
                "ordering_agency": "기관",
            }
            for i in range(50)
        ]
        conn = _FakeConn(documents)
        candidates = [_candidate("alio", "bid_notice", str(i)) for i in range(50)]
        preload_agency_index(conn, candidates)
        assert len(conn.execute_calls) == 1


class TestProfileCandidates:
    def test_unresolved_candidate_kept_with_reason(self):
        conn = _FakeConn([])
        candidates_by_clause = {"5": [_candidate("alio", "bid_notice", "1", "입찰 공고 관련 내용")]}
        candidates_by_clause["5"][0]["candidate_id"] = "c1"
        candidates_by_clause["5"][0]["extraction_id"] = "e1"

        rows = profile_candidates(candidates_by_clause, conn=conn)
        assert len(rows) == 1
        row = rows[0]
        assert row.profile_status == "unresolved"
        assert row.unresolved_reason == "not_found_in_documents_table"
        assert row.agency_category == ""

    def test_fixed_agency_source_resolves_without_db(self):
        conn = _FakeConn([])
        candidate = _candidate("moel", "official_document", "1", "고용 관련 문서")
        candidate["candidate_id"] = "c1"
        candidate["extraction_id"] = "e1"

        rows = profile_candidates({"5": [candidate]}, conn=conn)
        assert rows[0].profile_status == "resolved"
        assert rows[0].agency_category != ""
        assert conn.execute_calls == []

    def test_db_error_propagates_and_does_not_become_unresolved(self):
        class _ExplodingConn:
            def cursor(self):
                raise RuntimeError("connection lost")

        candidate = _candidate("alio", "bid_notice", "1", "입찰")
        candidate["candidate_id"] = "c1"
        candidate["extraction_id"] = "e1"

        with pytest.raises(RuntimeError):
            profile_candidates({"5": [candidate]}, conn=_ExplodingConn())


class TestWriteCandidateProfilesAtomic:
    def test_publishes_profiles_and_manifest(self, tmp_path):
        rows = [
            ProfileRow(
                candidate_id="c1", extraction_id="e1", source="alio", doc_type="bid_notice",
                doc_id="1", clause_no="5", subclause_key="bid_contract",
                agency_category="central_ministry", profile_status="resolved", unresolved_reason="",
            ),
        ]
        manifest = write_candidate_profiles_atomic(
            tmp_path, rows, candidate_manifest={"run_id": "run-1", "rule_version": "rule-v1"}
        )
        assert (tmp_path / "candidate_profiles.jsonl").exists()
        assert (tmp_path / "_profile_manifest.json").exists()
        assert manifest["row_count"] == 1
        assert manifest["candidate_manifest_run_id"] == "run-1"
        assert not list(tmp_path.glob(".*.tmp"))

    def test_deterministic_row_order(self, tmp_path):
        rows = [
            ProfileRow(
                "c2", "e2", "alio", "report", "2", "5", "audit_inspection",
                "central_ministry", "resolved", "",
            ),
            ProfileRow(
                "c1", "e1", "alio", "bid_notice", "1", "5", "bid_contract",
                "central_ministry", "resolved", "",
            ),
        ]
        write_candidate_profiles_atomic(
            tmp_path, rows, candidate_manifest={"run_id": "run-1", "rule_version": "rule-v1"}
        )
        lines = (tmp_path / "candidate_profiles.jsonl").read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in lines]
        # 정렬키 (clause_no, subclause_key, doc_type, candidate_id): "audit_inspection" < "bid_contract"
        assert [p["candidate_id"] for p in parsed] == ["c2", "c1"]
