import re

from rd2.storage import naming

_FORBIDDEN_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')
_HANGUL_RANGE = re.compile(r"[가-힣]")


def _all_code_values() -> list[str]:
    values = [
        v
        for name, v in vars(naming).items()
        if name.isupper() and isinstance(v, str)
    ]
    values += list(naming.LEGACY_SOURCE_MAP.values())
    values += list(naming.LEGACY_DOC_TYPE_MAP.values())
    return values


def test_all_naming_constants_are_filesystem_safe():
    for value in _all_code_values():
        assert not _FORBIDDEN_PATH_CHARS.search(value), f"{value!r} contains a forbidden path char"


def test_all_naming_constants_are_english_not_korean():
    for value in _all_code_values():
        assert not _HANGUL_RANGE.search(value), f"{value!r} still contains Korean characters"


def test_legacy_source_map_covers_every_renamed_source():
    assert naming.LEGACY_SOURCE_MAP == {
        "보건복지부": naming.SOURCE_MOHW,
        "정보공개포털": naming.SOURCE_OPEN_GO_KR,
    }


def test_legacy_doc_type_map_covers_every_renamed_doc_type():
    assert naming.LEGACY_DOC_TYPE_MAP == {
        "연구보고서": naming.DOC_TYPE_RESEARCH_REPORT,
        "입찰공고": naming.DOC_TYPE_BID_NOTICE,
        "사전규격공개": naming.DOC_TYPE_PRE_SPEC_NOTICE,
        "입찰재공고": naming.DOC_TYPE_BID_RENOTICE,
        "공모": naming.DOC_TYPE_PUBLIC_OFFERING,
        "공고": naming.DOC_TYPE_NOTICE,
        "공문": naming.DOC_TYPE_OFFICIAL_DOCUMENT,
        "합성문서": naming.DOC_TYPE_SYNTHETIC_DOCUMENT,
    }


def test_legacy_maps_have_no_duplicate_english_values():
    assert len(set(naming.LEGACY_SOURCE_MAP.values())) == len(naming.LEGACY_SOURCE_MAP)
    assert len(set(naming.LEGACY_DOC_TYPE_MAP.values())) == len(naming.LEGACY_DOC_TYPE_MAP)
