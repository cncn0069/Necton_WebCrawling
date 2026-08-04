from rd2.disclosure.agency_categories import (
    CATEGORY_CENTRAL_MINISTRY,
    CATEGORY_EDUCATION_OFFICE,
    CATEGORY_METRO_LOCAL_GOVERNMENT,
    CATEGORY_PUBLIC_CORPORATION,
    CATEGORY_RESEARCH_INSTITUTE,
    get_agency_category,
)


class TestGetAgencyCategory:
    def test_none_or_empty_falls_back_to_default(self):
        assert get_agency_category(None) == CATEGORY_PUBLIC_CORPORATION
        assert get_agency_category("") == CATEGORY_PUBLIC_CORPORATION

    def test_synthetic_fallback_agency_goes_to_catchall(self):
        """generate_fallback_row()가 채우는 실제 값 — 실기관이 아니므로 공공기관으로."""
        assert get_agency_category("가상기관(합성)") == CATEGORY_PUBLIC_CORPORATION

    def test_education_office_matched_before_central_ministry_suffix_rule(self):
        """세션 실제 사례 — "청" 접미사만으로는 교육청도 중앙부처로 잘못 분류된다."""
        assert get_agency_category("세종특별자치시교육청") == CATEGORY_EDUCATION_OFFICE
        assert get_agency_category("서울특별시교육청") == CATEGORY_EDUCATION_OFFICE

    def test_central_ministry_suffixes(self):
        for name in ["국방부", "법무부", "산업통상부", "경찰청", "방위사업청", "식품의약품안전처"]:
            assert get_agency_category(name) == CATEGORY_CENTRAL_MINISTRY, name

    def test_committee_is_central_ministry(self):
        assert get_agency_category("국가지식재산위원회") == CATEGORY_CENTRAL_MINISTRY

    def test_research_institute_keywords(self):
        for name in ["한국개발연구원", "OO연구소"]:
            assert get_agency_category(name) == CATEGORY_RESEARCH_INSTITUTE, name

    def test_metro_local_government(self):
        for name in ["경상북도", "전남광주통합특별시", "전남광주통합특별시 무안군", "인천광역시", "강원특별자치도"]:
            assert get_agency_category(name) == CATEGORY_METRO_LOCAL_GOVERNMENT, name

    def test_unmapped_name_falls_back_to_default(self):
        assert get_agency_category("아무거나회사") == CATEGORY_PUBLIC_CORPORATION
