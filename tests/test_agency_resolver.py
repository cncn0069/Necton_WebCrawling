import random

import pytest

import datetime

from rd2.generators.agency_resolver import (
    AGENCY_LOGO_FILENAMES,
    FIXED_AGENCY_BY_SOURCE,
    MARKING_SPEC_AGENCY_WHITELIST,
    PER_DOC_AGENCY_SOURCES,
    fetch_real_agency_date_samples,
    resolve_agency_for_candidate,
    sample_real_agency_and_date_for_fallback,
    select_whitelisted_agency,
    synthesize_plausible_date,
)


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed_sql: str | None = None
        self.executed_params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed_sql = sql
        self.executed_params = params

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.last_cursor: _FakeCursor | None = None

    def cursor(self):
        self.last_cursor = _FakeCursor(self._rows)
        return self.last_cursor


class TestResolveAgencyForCandidate:
    def test_fixed_source_returns_without_db(self):
        candidate = {"source": "moel", "doc_type": "notification", "doc_id": "123"}
        assert resolve_agency_for_candidate(candidate, conn=None) == "고용노동부"

    def test_fixed_source_table_covers_expected_agencies(self):
        assert FIXED_AGENCY_BY_SOURCE == {
            "moel": "고용노동부",
            "moe": "교육부",
            "mohw": "보건복지부",
            "molit": "국토교통부",
        }

    def test_per_doc_source_queries_db_and_returns_agency(self):
        conn = _FakeConnection([{"ordering_agency": "실제기관명"}])
        candidate = {"source": "prism", "doc_type": "report", "doc_id": "999"}

        result = resolve_agency_for_candidate(candidate, conn=conn)

        assert result == "실제기관명"
        assert "body_file_path LIKE" in conn.last_cursor.executed_sql
        assert conn.last_cursor.executed_params == ("prism/report/%/999_%",)

    def test_per_doc_source_no_match_returns_none(self):
        conn = _FakeConnection([])
        candidate = {"source": "prism", "doc_type": "report", "doc_id": "999"}

        assert resolve_agency_for_candidate(candidate, conn=conn) is None

    def test_per_doc_source_without_conn_returns_none(self):
        candidate = {"source": "prism", "doc_type": "report", "doc_id": "999"}

        assert resolve_agency_for_candidate(candidate, conn=None) is None

    def test_unknown_source_returns_none(self):
        candidate = {"source": "unknown_source", "doc_type": "report", "doc_id": "1"}

        assert resolve_agency_for_candidate(candidate, conn=_FakeConnection([])) is None

    def test_missing_source_returns_none(self):
        assert resolve_agency_for_candidate({}, conn=None) is None

    def test_per_doc_agency_sources_set(self):
        assert PER_DOC_AGENCY_SOURCES == {
            "alio", "korea_kr", "open_go_kr", "orginl_info", "prism", "seoul_opengov", "me",
        }


class TestFetchRealAgencyDateSamples:
    def test_returns_agency_date_pairs(self):
        conn = _FakeConnection(
            [
                {"ordering_agency": "기관A", "production_date": "2025-01-01"},
                {"ordering_agency": "기관B", "production_date": "2025-06-15"},
            ]
        )

        result = fetch_real_agency_date_samples(conn)

        assert result == [("기관A", "2025-01-01"), ("기관B", "2025-06-15")]

    def test_skips_rows_with_missing_agency_or_date(self):
        conn = _FakeConnection(
            [
                {"ordering_agency": "기관A", "production_date": "2025-01-01"},
                {"ordering_agency": "", "production_date": "2025-06-15"},
                {"ordering_agency": "기관C", "production_date": None},
            ]
        )

        result = fetch_real_agency_date_samples(conn)

        assert result == [("기관A", "2025-01-01")]

    def test_empty_result_returns_empty_list(self):
        conn = _FakeConnection([])
        assert fetch_real_agency_date_samples(conn) == []


class TestSampleRealAgencyAndDateForFallback:
    def test_samples_from_list(self):
        rng = random.Random(42)
        samples = [("기관A", "2025-01-01"), ("기관B", "2025-06-15"), ("기관C", "2025-12-31")]
        result = sample_real_agency_and_date_for_fallback(rng, samples)
        assert result in samples

    def test_deterministic_given_fixed_seed(self):
        samples = [("기관A", "2025-01-01"), ("기관B", "2025-06-15"), ("기관C", "2025-12-31")]
        result_a = sample_real_agency_and_date_for_fallback(random.Random(42), samples)
        result_b = sample_real_agency_and_date_for_fallback(random.Random(42), samples)
        assert result_a == result_b

    def test_empty_list_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="실제 \\(ordering_agency, production_date\\) 쌍이 비어"):
            sample_real_agency_and_date_for_fallback(random.Random(42), [])


class TestSelectWhitelistedAgency:
    def test_clause_1_to_4_have_whitelist_entries(self):
        for clause_no in ("1", "2", "3", "4"):
            assert MARKING_SPEC_AGENCY_WHITELIST.get(clause_no), (
                f"clause {clause_no} has no whitelist entries"
            )

    def test_every_whitelist_agency_has_a_logo(self):
        for clause_no, agencies in MARKING_SPEC_AGENCY_WHITELIST.items():
            for agency in agencies:
                assert agency in AGENCY_LOGO_FILENAMES, (
                    f"clause {clause_no} whitelists {agency!r} but it has no logo mapping"
                )

    def test_returns_agency_from_clause_pool(self):
        rng = random.Random(42)
        agency, logo_filename = select_whitelisted_agency("2", rng)
        assert agency in MARKING_SPEC_AGENCY_WHITELIST["2"]
        assert logo_filename == AGENCY_LOGO_FILENAMES[agency]

    def test_unknown_clause_falls_back_to_generic_government(self):
        rng = random.Random(42)
        agency, logo_filename = select_whitelisted_agency("8", rng)
        assert agency == "정부부처"
        assert logo_filename == AGENCY_LOGO_FILENAMES["정부부처"]

    def test_deterministic_given_fixed_seed(self):
        result_a = select_whitelisted_agency("1", random.Random(7))
        result_b = select_whitelisted_agency("1", random.Random(7))
        assert result_a == result_b


class TestSynthesizePlausibleDate:
    def test_returns_iso_date_string_in_the_past(self):
        rng = random.Random(1)
        result = synthesize_plausible_date(rng)
        parsed = datetime.date.fromisoformat(result)
        assert parsed < datetime.date.today()

    def test_stays_within_years_back_window(self):
        rng = random.Random(1)
        result = synthesize_plausible_date(rng, years_back=1)
        parsed = datetime.date.fromisoformat(result)
        assert (datetime.date.today() - parsed).days <= 366

    def test_deterministic_given_fixed_seed(self):
        result_a = synthesize_plausible_date(random.Random(3))
        result_b = synthesize_plausible_date(random.Random(3))
        assert result_a == result_b
