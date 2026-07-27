import random

import pytest

import datetime

from rd2.generators.agency_resolver import (
    AGENCY_LOGO_FILENAMES,
    FIXED_AGENCY_BY_SOURCE,
    GENERAL_TRACK_SUPPLEMENTARY_AGENCIES,
    MARKING_SPEC_AGENCY_WHITELIST,
    PER_DOC_AGENCY_SOURCES,
    RECLASSIFICATION_RATIO,
    fetch_real_agency_date_samples,
    resolve_agency_for_candidate,
    sample_diverse_agency_and_date_for_fallback,
    sample_real_agency_and_date_for_fallback,
    scenario_contains_military_secret,
    select_reclassification,
    select_whitelisted_agency,
    synthesize_plausible_date,
    synthesize_plausible_date_after,
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


class TestSampleDiverseAgencyAndDateForFallback:
    def test_deduplicates_agency_before_choosing(self):
        # 기관A가 문서 99건, 기관B가 1건이어도 "기관 하나당 한 표"이므로
        # 여러 시드에 걸쳐 기관B도 뽑혀야 한다(가중 추출이면 사실상 불가능).
        samples = [("기관A", "2025-01-01")] * 99 + [("기관B", "2025-06-15")]
        picked_agencies = {
            sample_diverse_agency_and_date_for_fallback(random.Random(seed), samples)[0]
            for seed in range(20)
        }
        assert "기관B" in picked_agencies

    def test_date_matches_the_chosen_agencys_own_history(self):
        samples = [("기관A", "2025-01-01"), ("기관A", "2025-02-02"), ("기관B", "2025-06-15")]
        for seed in range(20):
            agency, prod_date, source = sample_diverse_agency_and_date_for_fallback(
                random.Random(seed), samples
            )
            if agency in GENERAL_TRACK_SUPPLEMENTARY_AGENCIES:
                assert source == "whitelist_synthetic"
                continue
            assert (agency, prod_date) in samples
            assert source == "real_db_sample"

    def test_supplementary_agency_gets_synthesized_date(self):
        samples = [("기관A", "2025-01-01")]
        found_supplementary = False
        for seed in range(200):
            agency, prod_date, source = sample_diverse_agency_and_date_for_fallback(
                random.Random(seed), samples
            )
            if agency in GENERAL_TRACK_SUPPLEMENTARY_AGENCIES:
                found_supplementary = True
                assert source == "whitelist_synthetic"
                datetime.date.fromisoformat(prod_date)
        assert found_supplementary, "보충 목록 기관이 한 번도 안 뽑혔다 — 확률 또는 로직 확인 필요"

    def test_deterministic_given_fixed_seed(self):
        samples = [("기관A", "2025-01-01"), ("기관B", "2025-06-15")]
        result_a = sample_diverse_agency_and_date_for_fallback(random.Random(42), samples)
        result_b = sample_diverse_agency_and_date_for_fallback(random.Random(42), samples)
        assert result_a == result_b

    def test_empty_samples_still_works_via_supplementary_list(self):
        agency, prod_date, source = sample_diverse_agency_and_date_for_fallback(
            random.Random(42), []
        )
        assert agency in GENERAL_TRACK_SUPPLEMENTARY_AGENCIES
        assert source == "whitelist_synthetic"
        datetime.date.fromisoformat(prod_date)

    def test_empty_samples_and_empty_supplementary_list_raises(self, monkeypatch):
        monkeypatch.setattr(
            "rd2.generators.agency_resolver.GENERAL_TRACK_SUPPLEMENTARY_AGENCIES", []
        )
        with pytest.raises(RuntimeError, match="실제 \\(ordering_agency, production_date\\) 쌍이 비어"):
            sample_diverse_agency_and_date_for_fallback(random.Random(42), [])


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

    def test_scenario_index_narrows_to_scenario_specific_pool(self):
        from rd2.generators.clause_data import CLAUSES

        for clause_no in ("1", "2", "3", "4"):
            clause = CLAUSES[clause_no]
            for idx, expected_pool in enumerate(clause.scenario_agencies):
                for seed in range(10):
                    agency, logo_filename = select_whitelisted_agency(
                        clause_no, random.Random(seed), scenario_index=idx
                    )
                    assert agency in expected_pool, (
                        f"clause {clause_no} scenario {idx} picked {agency!r}, "
                        f"expected one of {expected_pool}"
                    )
                    assert logo_filename == AGENCY_LOGO_FILENAMES[agency]

    def test_scenario_specific_pools_are_subsets_of_clause_whitelist(self):
        from rd2.generators.clause_data import CLAUSES

        for clause_no in ("1", "2", "3", "4"):
            clause = CLAUSES[clause_no]
            whitelist = set(MARKING_SPEC_AGENCY_WHITELIST[clause_no])
            assert len(clause.scenario_agencies) == len(clause.scenario_prompts), (
                f"clause {clause_no} scenario_agencies must align 1:1 with scenario_prompts"
            )
            for pool in clause.scenario_agencies:
                assert set(pool) <= whitelist, (
                    f"clause {clause_no} scenario pool {pool} has agencies outside the whitelist"
                )

    def test_no_scenario_index_falls_back_to_full_clause_pool(self):
        # 하위 호환: scenario_index 없이 호출하는 기존 경로(예: template_samples.py)는
        # 여전히 조항 전체 화이트리스트에서 고른다.
        rng = random.Random(42)
        agency, logo_filename = select_whitelisted_agency("2", rng, scenario_index=None)
        assert agency in MARKING_SPEC_AGENCY_WHITELIST["2"]
        assert logo_filename == AGENCY_LOGO_FILENAMES[agency]


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


class TestScenarioContainsMilitarySecret:
    """비밀표시 규정 제9항 — 비군사기관 문서에 군사기밀 사항이 섞였는지 판단."""

    def test_military_agency_is_always_false(self):
        assert scenario_contains_military_secret("2", 4, "국방부") is False
        assert scenario_contains_military_secret("2", 4, "국가정보원") is False

    def test_tagged_scenario_with_non_military_agency_is_true(self):
        assert scenario_contains_military_secret("2", 4, "외교부") is True

    def test_untagged_scenario_is_false(self):
        assert scenario_contains_military_secret("2", 1, "외교부") is False

    def test_scenario_index_none_is_false(self):
        assert scenario_contains_military_secret("2", None, "외교부") is False

    def test_unknown_clause_is_false(self):
        assert scenario_contains_military_secret("not-a-clause", 0, "외교부") is False

    def test_clause_without_tagging_is_always_false(self):
        for idx in range(10):
            assert scenario_contains_military_secret("1", idx, "검찰청") is False


class TestSynthesizePlausibleDateAfter:
    def test_returns_iso_date_string_after_base_date(self):
        rng = random.Random(1)
        result = synthesize_plausible_date_after(
            rng, "2026-01-01", today=datetime.date(2026, 7, 27)
        )
        parsed = datetime.date.fromisoformat(result)
        assert parsed > datetime.date(2026, 1, 1)
        assert parsed <= datetime.date(2026, 7, 27)

    def test_stays_within_max_days_after_window(self):
        rng = random.Random(1)
        result = synthesize_plausible_date_after(
            rng, "2026-01-01", max_days_after=30, today=datetime.date(2026, 7, 27)
        )
        parsed = datetime.date.fromisoformat(result)
        assert (parsed - datetime.date(2026, 1, 1)).days <= 30

    def test_deterministic_given_fixed_seed(self):
        result_a = synthesize_plausible_date_after(
            random.Random(3), "2026-01-01", today=datetime.date(2026, 7, 27)
        )
        result_b = synthesize_plausible_date_after(
            random.Random(3), "2026-01-01", today=datetime.date(2026, 7, 27)
        )
        assert result_a == result_b

    def test_unparseable_base_date_raises(self):
        with pytest.raises(ValueError):
            synthesize_plausible_date_after(
                random.Random(1), "not-a-date", today=datetime.date(2026, 7, 27)
            )

    def test_base_date_today_has_no_valid_reclassification_window(self):
        with pytest.raises(ValueError, match="과거 날짜 구간"):
            synthesize_plausible_date_after(
                random.Random(1), "2026-07-27", today=datetime.date(2026, 7, 27)
            )


class TestSelectReclassification:
    """[별표 2] 7호 재분류 표시 — 등급 문서 중 일부를 무작위로 재분류 변형으로 만든다."""

    def test_top_grade_is_never_reclassified(self):
        rng = random.Random(1)
        for _ in range(50):
            assert select_reclassification(rng, "1급", "2026-01-01") is None

    def test_lower_grades_can_be_reclassified_with_ratio_one(self):
        rng = random.Random(1)
        result = None
        for _ in range(50):
            result = select_reclassification(rng, "3급", "2026-01-01")
            if result is not None:
                break
        assert result is not None
        assert result["old_grade"] in ("1급", "2급")

    def test_old_grade_is_always_more_severe_than_new_grade(self):
        rng = random.Random(1)
        for _ in range(200):
            result = select_reclassification(rng, "2급", "2026-01-01")
            if result is not None:
                assert result["old_grade"] == "1급"

    def test_zero_ratio_never_reclassifies(self, monkeypatch):
        monkeypatch.setattr(
            "rd2.generators.agency_resolver.RECLASSIFICATION_RATIO", 0.0
        )
        rng = random.Random(1)
        for _ in range(50):
            assert select_reclassification(rng, "3급", "2026-01-01") is None

    def test_result_has_expected_keys(self):
        rng = random.Random(1)
        result = None
        for _ in range(50):
            result = select_reclassification(rng, "3급", "2026-01-01")
            if result is not None:
                break
        assert result is not None
        assert set(result.keys()) == {
            "old_grade", "basis_text", "reclass_date", "position", "rank", "name",
        }

    def test_ratio_constant_is_between_zero_and_one(self):
        assert 0.0 < RECLASSIFICATION_RATIO < 1.0

    def test_future_production_date_is_never_reclassified(self, monkeypatch):
        monkeypatch.setattr(
            "rd2.generators.agency_resolver.RECLASSIFICATION_RATIO", 1.0
        )
        assert select_reclassification(
            random.Random(1), "3급", "2026-07-28", today=datetime.date(2026, 7, 27)
        ) is None

    def test_reclassification_date_never_exceeds_today(self, monkeypatch):
        monkeypatch.setattr(
            "rd2.generators.agency_resolver.RECLASSIFICATION_RATIO", 1.0
        )
        result = select_reclassification(
            random.Random(1), "3급", "2026-07-26", today=datetime.date(2026, 7, 27)
        )
        assert result is not None
        assert result["reclass_date"] == "2026-07-27"
