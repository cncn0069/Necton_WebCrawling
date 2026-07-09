from pathlib import Path

from rd2.storage.files import save_body_file
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_OPEN_GO_KR, SOURCE_PRISM


def test_save_body_file_creates_nested_source_doc_type_dirs(tmp_path):
    path = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-1", "report.pdf", b"content")
    assert path == str(Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "doc-1_report.pdf")
    assert (tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "doc-1_report.pdf").read_bytes() == b"content"


def test_save_body_file_none_doc_type_falls_back_to_unclassified(tmp_path):
    path = save_body_file(tmp_path, SOURCE_OPEN_GO_KR, None, "doc-2", "notice.pdf", b"x")
    assert path == str(Path(SOURCE_OPEN_GO_KR) / "_unclassified" / "doc-2_notice.pdf")


def test_save_body_file_sanitizes_forbidden_path_characters(tmp_path):
    path = save_body_file(tmp_path, "g2b", "공고/입찰:서", "doc-3", "file.pdf", b"x")
    assert "/" not in path.split("g2b")[1].split("doc-3")[0].strip("\\/")
    assert (tmp_path / "g2b" / "공고입찰서" / "doc-3_file.pdf").exists()


def test_save_body_file_doc_type_only_forbidden_chars_falls_back_to_unclassified(tmp_path):
    path = save_body_file(tmp_path, "g2b", "///", "doc-4", "file.pdf", b"x")
    assert path == str(Path("g2b") / "_unclassified" / "doc-4_file.pdf")


def test_save_body_file_different_identifiers_avoid_collision(tmp_path):
    p1 = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-1", "same.pdf", b"a")
    p2 = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-2", "same.pdf", b"b")
    assert p1 != p2
    assert (tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "doc-1_same.pdf").read_bytes() == b"a"
    assert (tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "doc-2_same.pdf").read_bytes() == b"b"


def test_save_body_file_returns_relative_path_not_absolute(tmp_path):
    path = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-1", "report.pdf", b"content")
    assert not Path(path).is_absolute()
