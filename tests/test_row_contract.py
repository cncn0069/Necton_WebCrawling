"""rd2.audit.row_contract 회귀 테스트 — 설계 문서 §6(입력 계약) + §12.5(row 무결성)."""

import pytest

from rd2.audit.row_contract import (
    REQUIRED_COLUMNS,
    AuditContractError,
    parse_row,
    validate_header,
)

_PLAN_CELL_KEYS = frozenset({"C|1|legal_secret|report|central_ministry|-"})


def _raw(**overrides):
    base = {name: "" for name in REQUIRED_COLUMNS}
    base.update(
        row_id="row-1",
        status="ok",
        body_text="본문 내용",
        title="제목",
        cso_classification="C",
        clause_no="1",
        cso_subclause_key="legal_secret",
        doc_type="report",
        document_status="",
        ordering_agency="고용노동부",
        agency_category="central_ministry",
        template_id="T1-1",
        seed_candidate_id="cand-1",
        seed_extraction_id="ext-1",
        seed_source_path="src/path",
        coverage_cell_key="C|1|legal_secret|report|central_ministry|-",
        coverage_slot="0",
        coverage_plan_run_id="sha256:abc",
    )
    base.update(overrides)
    return base


class TestValidateHeader:
    def test_all_required_columns_present_ok(self):
        validate_header(REQUIRED_COLUMNS)

    def test_missing_column_raises(self):
        fieldnames = [c for c in REQUIRED_COLUMNS if c != "agency_category"]
        with pytest.raises(AuditContractError, match="agency_category"):
            validate_header(fieldnames)

    def test_none_fieldnames_raises(self):
        with pytest.raises(AuditContractError):
            validate_header(None)


class TestParseRowOkStatus:
    def test_valid_row_parses(self):
        row, error = parse_row(_raw(), row_number=1, plan_cell_keys=_PLAN_CELL_KEYS)
        assert error is None
        assert row is not None
        assert row.row_id == "row-1"
        assert row.is_ok is True

    def test_missing_row_id_is_row_error(self):
        row, error = parse_row(_raw(row_id=""), row_number=1, plan_cell_keys=_PLAN_CELL_KEYS)
        assert row is None
        assert error.error_code == "missing_field"
        assert error.field == "row_id"

    def test_unknown_classification_is_row_error(self):
        row, error = parse_row(_raw(cso_classification="X"), row_number=2, plan_cell_keys=_PLAN_CELL_KEYS)
        assert row is None
        assert error.error_code == "unknown_enum"
        assert error.field == "cso_classification"

    def test_empty_body_when_ok_is_row_error(self):
        row, error = parse_row(_raw(body_text="   "), row_number=3, plan_cell_keys=_PLAN_CELL_KEYS)
        assert row is None
        assert error.error_code == "empty_body"

    def test_unknown_coverage_cell_is_row_error(self):
        row, error = parse_row(
            _raw(coverage_cell_key="C|9|nope|nope|nope|-"), row_number=4, plan_cell_keys=_PLAN_CELL_KEYS
        )
        assert row is None
        assert error.error_code == "unknown_cell"

    def test_missing_coverage_cell_key_when_ok_is_row_error(self):
        row, error = parse_row(_raw(coverage_cell_key=""), row_number=5, plan_cell_keys=_PLAN_CELL_KEYS)
        assert row is None
        assert error.error_code == "missing_field"
        assert error.field == "coverage_cell_key"


class TestParseRowNonOkStatus:
    def test_non_ok_status_row_parses_without_content_validation(self):
        """설계 문서 §6: status != ok 행은 에러가 아니라 실패 분포로 집계한다 —
        body_text가 비어 있거나 classification이 이상해도 RowError로 격리하지 않는다."""
        raw = _raw(status="error", body_text="", cso_classification="", coverage_cell_key="")
        row, error = parse_row(raw, row_number=1, plan_cell_keys=_PLAN_CELL_KEYS)
        assert error is None
        assert row is not None
        assert row.is_ok is False

    def test_missing_status_is_still_a_row_error(self):
        row, error = parse_row(_raw(status=""), row_number=1, plan_cell_keys=_PLAN_CELL_KEYS)
        assert row is None
        assert error.error_code == "missing_field"
        assert error.field == "status"
