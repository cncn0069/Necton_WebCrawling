from rd2.generators.agency_capabilities import (
    CAP_COMMERCIAL_PRODUCT_SALES,
    CAP_MEDIA_CONTENT,
    CAP_MEDICAL_CARE,
    get_agency_capability_profile,
    is_agency_scenario_compatible,
)


def test_national_cancer_center_has_medical_but_not_product_sales_capability():
    profile = get_agency_capability_profile("국립암센터")
    assert CAP_MEDICAL_CARE in profile.capabilities
    assert CAP_COMMERCIAL_PRODUCT_SALES not in profile.capabilities
    assert profile.confidence == "verified"


def test_gugak_broadcasting_has_media_but_not_product_sales_capability():
    profile = get_agency_capability_profile("국악방송")
    assert CAP_MEDIA_CONTENT in profile.capabilities
    assert CAP_COMMERCIAL_PRODUCT_SALES not in profile.capabilities


def test_unknown_new_agency_is_rejected_for_commercial_scenario():
    assert not is_agency_scenario_compatible("새로추가기관", "7", 0)
    assert not is_agency_scenario_compatible("새로추가기관", "7", 1)
    assert not is_agency_scenario_compatible("새로추가기관", "7", 4)
    assert not is_agency_scenario_compatible("새로추가기관", "7", 6)
    assert not is_agency_scenario_compatible("새로추가기관", "7", 7)
    assert not is_agency_scenario_compatible("새로추가기관", "7", 8)


def test_unknown_new_agency_remains_available_for_generic_scenario():
    assert is_agency_scenario_compatible("새로추가기관", "7", 3)


def test_explicit_commercial_legal_form_can_use_product_scenario():
    assert is_agency_scenario_compatible("(주)공영홈쇼핑", "7", 4)


def test_verified_research_institution_can_use_technology_scenario():
    assert is_agency_scenario_compatible("국립암센터", "7", 0)


def test_media_institution_cannot_be_forced_into_technology_scenario():
    assert not is_agency_scenario_compatible("국악방송", "7", 0)
    assert not is_agency_scenario_compatible("국악방송", "7", 8)


def test_central_government_can_review_strategic_technology_designation():
    assert is_agency_scenario_compatible("산업통상자원부", "7", 8)


def test_kisa_cannot_be_assigned_welfare_eligibility_scenario():
    assert not is_agency_scenario_compatible("한국인터넷진흥원", "6", 2)


def test_welfare_and_medical_agencies_match_their_domains():
    assert is_agency_scenario_compatible("양육비이행관리원", "6", 2)
    assert is_agency_scenario_compatible("충북대학교병원", "6", 3)
