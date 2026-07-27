"""rd2.audit.duplicates 회귀 테스트 — 설계 문서 §7.2 + §12.4.

Success Criteria: "raw exact duplicate fixture의 100%를 탐지한다."
"""

from rd2.audit.duplicates import NORMALIZED_EXACT, RAW_EXACT, ExactDuplicateAccumulator
from rd2.audit.row_contract import AuditRow


def _row(row_id, title, body, *, seed_candidate_id="", seed_extraction_id="", ordering_agency="고용노동부"):
    return AuditRow(
        source_row_number=1, row_id=row_id, status="ok", body_text=body, title=title,
        classification="C", clause_no="1", subclause_key="legal_secret", doc_type="report",
        document_status="", ordering_agency=ordering_agency, agency_category="central_ministry",
        template_id="T1-1", seed_candidate_id=seed_candidate_id, seed_extraction_id=seed_extraction_id,
        seed_source_path="", coverage_cell_key="C|1|legal_secret|report|central_ministry|-",
        coverage_slot="0", coverage_plan_run_id="sha256:abc",
    )


class TestExactDuplicateAccumulator:
    def test_detects_100_percent_of_raw_exact_duplicates(self):
        acc = ExactDuplicateAccumulator()
        # 3건이 완전히 동일한 title/body -> 하나의 raw_exact 그룹, size 3.
        for i in range(3):
            acc.add_row(_row(f"row-{i}", "같은 제목", "완전히 같은 본문 내용"))
        groups, pairs = acc.finalize(audit_run_id="audit-1")
        raw_groups = [g for g in groups if g["metric"] == RAW_EXACT]
        assert len(raw_groups) == 1
        assert raw_groups[0]["group_size"] == 3
        assert raw_groups[0]["representative_row_id"] == "row-0"

    def test_normalized_only_duplicate_detected(self):
        """기관명·날짜만 다르고 나머지 내용이 같으면 normalized에서만 잡혀야 한다."""
        acc = ExactDuplicateAccumulator()
        acc.add_row(_row("row-a", "제목", "생산일자 2024-01-01 발표", ordering_agency="고용노동부"))
        acc.add_row(_row("row-b", "제목", "생산일자 2024-05-05 발표", ordering_agency="고용노동부"))
        groups, pairs = acc.finalize(audit_run_id="audit-1")
        raw_groups = [g for g in groups if g["metric"] == RAW_EXACT]
        normalized_groups = [g for g in groups if g["metric"] == NORMALIZED_EXACT]
        assert raw_groups == []
        assert len(normalized_groups) == 1
        assert normalized_groups[0]["group_size"] == 2

    def test_distinct_content_not_grouped(self):
        acc = ExactDuplicateAccumulator()
        acc.add_row(_row("row-a", "제목A", "완전히 다른 내용 A"))
        acc.add_row(_row("row-b", "제목B", "완전히 다른 내용 B"))
        groups, pairs = acc.finalize(audit_run_id="audit-1")
        assert groups == []
        assert pairs == []

    def test_only_one_representative_pair_emitted_per_group(self):
        """설계 문서 §14.8: "완전 조합으로 펼치지 않는다" — 그룹 크기가 커도 pair는 1개."""
        acc = ExactDuplicateAccumulator()
        for i in range(5):
            acc.add_row(_row(f"row-{i}", "제목", "동일 본문"))
        groups, pairs = acc.finalize(audit_run_id="audit-1")
        raw_pairs = [p for p in pairs if p["metric"] == RAW_EXACT]
        assert len(raw_pairs) == 1
        raw_group = next(g for g in groups if g["metric"] == RAW_EXACT)
        assert raw_group["group_size"] == 5
        assert raw_group["emitted_pair_count"] == 1
        assert raw_group["pair_rows_truncated"] is True

    def test_pair_row_ids_in_canonical_order(self):
        acc = ExactDuplicateAccumulator()
        acc.add_row(_row("row-z", "제목", "동일 본문"))
        acc.add_row(_row("row-a", "제목", "동일 본문"))
        _groups, pairs = acc.finalize(audit_run_id="audit-1")
        raw_pair = next(p for p in pairs if p["metric"] == RAW_EXACT)
        assert raw_pair["row_id_a"] < raw_pair["row_id_b"]

    def test_shared_candidate_and_extraction_flags(self):
        acc = ExactDuplicateAccumulator()
        acc.add_row(_row("row-a", "제목", "동일 본문", seed_candidate_id="cand-1", seed_extraction_id="ext-1"))
        acc.add_row(_row("row-b", "제목", "동일 본문", seed_candidate_id="cand-1", seed_extraction_id="ext-2"))
        _groups, pairs = acc.finalize(audit_run_id="audit-1")
        raw_pair = next(p for p in pairs if p["metric"] == RAW_EXACT)
        assert raw_pair["shared_candidate"] is True
        assert raw_pair["shared_extraction"] is False

    def test_pair_fields_use_global_scope_and_no_rank(self):
        acc = ExactDuplicateAccumulator()
        acc.add_row(_row("row-a", "제목", "동일 본문"))
        acc.add_row(_row("row-b", "제목", "동일 본문"))
        _groups, pairs = acc.finalize(audit_run_id="audit-1")
        raw_pair = next(p for p in pairs if p["metric"] == RAW_EXACT)
        assert raw_pair["comparison_scope_key"] == "GLOBAL"
        assert raw_pair["metric_value"] == 1.0
        assert raw_pair["reciprocal"] is False
        assert raw_pair["rank_a_to_b"] == ""
        assert raw_pair["rank_b_to_a"] == ""
