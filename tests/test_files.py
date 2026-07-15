from pathlib import Path

from rd2.storage.files import _BUCKET_SIZE, resolve_body_file_path, save_body_file
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_OPEN_GO_KR, SOURCE_PRISM


def test_save_body_file_creates_nested_source_doc_type_bucket_dirs(tmp_path):
    path = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-1", "report.pdf", b"content")
    assert path == str(Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "1-500" / "doc-1_report.pdf")
    assert (
        tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "1-500" / "doc-1_report.pdf"
    ).read_bytes() == b"content"


def test_save_body_file_none_doc_type_falls_back_to_unclassified(tmp_path):
    path = save_body_file(tmp_path, SOURCE_OPEN_GO_KR, None, "doc-2", "notice.pdf", b"x")
    assert path == str(Path(SOURCE_OPEN_GO_KR) / "_unclassified" / "1-500" / "doc-2_notice.pdf")


def test_save_body_file_sanitizes_forbidden_path_characters(tmp_path):
    path = save_body_file(tmp_path, "g2b", "공고/입찰:서", "doc-3", "file.pdf", b"x")
    assert "/" not in path.split("g2b")[1].split("doc-3")[0].strip("\\/").replace("1-500", "")
    assert (tmp_path / "g2b" / "공고입찰서" / "1-500" / "doc-3_file.pdf").exists()


def test_save_body_file_doc_type_only_forbidden_chars_falls_back_to_unclassified(tmp_path):
    path = save_body_file(tmp_path, "g2b", "///", "doc-4", "file.pdf", b"x")
    assert path == str(Path("g2b") / "_unclassified" / "1-500" / "doc-4_file.pdf")


def test_save_body_file_different_identifiers_avoid_collision(tmp_path):
    p1 = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-1", "same.pdf", b"a")
    p2 = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-2", "same.pdf", b"b")
    assert p1 != p2
    assert (
        tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "1-500" / "doc-1_same.pdf"
    ).read_bytes() == b"a"
    assert (
        tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "1-500" / "doc-2_same.pdf"
    ).read_bytes() == b"b"


def test_save_body_file_returns_relative_path_not_absolute(tmp_path):
    path = save_body_file(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-1", "report.pdf", b"content")
    assert not Path(path).is_absolute()


def test_resolve_body_file_path_rolls_over_to_next_bucket_at_500(tmp_path):
    """버킷 하나가 500개 차면 다음 호출은 501-1000 버킷으로 넘어가야 한다."""
    dir_path = tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "1-500"
    dir_path.mkdir(parents=True)
    for i in range(_BUCKET_SIZE):
        (dir_path / f"file-{i}.pdf").write_bytes(b"x")

    path = resolve_body_file_path(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-501", "report.pdf")
    assert path == tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "501-1000" / "doc-501_report.pdf"


def test_resolve_body_file_path_stays_in_current_bucket_below_500(tmp_path):
    dir_path = tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "1-500"
    dir_path.mkdir(parents=True)
    for i in range(_BUCKET_SIZE - 1):
        (dir_path / f"file-{i}.pdf").write_bytes(b"x")

    path = resolve_body_file_path(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-500", "report.pdf")
    assert path == tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "1-500" / "doc-500_report.pdf"


def test_resolve_body_file_path_ignores_pre_bucketing_flat_files(tmp_path):
    """버킷 분할 이전에 doc_type 폴더 바로 아래 flat하게 저장된 기존 파일은
    버킷 카운트에 영향을 주면 안 된다(마이그레이션 안 함, 2026-07-15 결정) —
    파일이지 디렉터리가 아니라 버킷 스캔에서 자연히 제외된다."""
    dir_path = tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT
    dir_path.mkdir(parents=True)
    for i in range(600):  # 버킷 분할 이전 관례로 쌓인 flat 파일 600개
        (dir_path / f"legacy-{i}.pdf").write_bytes(b"x")

    path = resolve_body_file_path(tmp_path, SOURCE_PRISM, DOC_TYPE_RESEARCH_REPORT, "doc-new", "report.pdf")
    # 기존 flat 파일 600개와 무관하게 새 버킷은 1-500부터 시작한다.
    assert path == tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "1-500" / "doc-new_report.pdf"
    # 기존 flat 파일은 그대로 남아있어야 한다(건드리지 않음).
    assert (dir_path / "legacy-599.pdf").exists()
