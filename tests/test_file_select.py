from rd2.adapters.file_select import normalize_for_match, pick_primary_file


def test_normalize_for_match_strips_whitespace_and_lowercases():
    assert normalize_for_match("공개 연구 샘플") == normalize_for_match("공개연구샘플")
    assert normalize_for_match("ABC Report") == "abcreport"


def test_pick_primary_file_prefers_closest_title_match():
    files = [
        {"fileNm": "정책연구과제_심의신청서(샘플).pdf"},
        {"fileNm": "공개 연구 샘플.pdf"},
    ]
    picked = pick_primary_file(files, "공개 연구 샘플")
    assert picked["fileNm"] == "공개 연구 샘플.pdf"


def test_pick_primary_file_supports_custom_filename_key():
    """mohw.py는 fileNm이 아니라 filename 키를 쓴다 — 두 어댑터가 같은 함수를
    다른 딕셔너리 스키마로 공유할 수 있어야 한다."""
    files = [
        {"filename": "입찰공고서(전자바우처 통합카드사업).hwpx"},
        {"filename": "전자바우처 통합카드사업자 모집 공고.pdf"},
    ]
    picked = pick_primary_file(
        files, "전자바우처 통합카드사업자 모집 공고", filename_key="filename"
    )
    assert picked["filename"] == "전자바우처 통합카드사업자 모집 공고.pdf"


def test_pick_primary_file_single_file_always_wins():
    files = [{"fileNm": "아무거나.pdf"}]
    assert pick_primary_file(files, "전혀 다른 제목") == files[0]
