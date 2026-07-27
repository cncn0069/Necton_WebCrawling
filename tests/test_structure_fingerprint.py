"""rd2.audit.structure_fingerprint 회귀 테스트 — 설계 문서 §7.4."""

from rd2.audit.structure_fingerprint import (
    body_length_bucket,
    numbered_list_count_bucket,
    paragraph_count_bucket,
    structure_fingerprint,
)


class TestParagraphCountBucket:
    def test_empty_body_is_zero_bucket(self):
        assert paragraph_count_bucket("") == "0"

    def test_single_paragraph(self):
        assert paragraph_count_bucket("한 문단짜리 본문") == "0-2"

    def test_many_paragraphs_bucketed(self):
        body = "\n\n".join(f"문단 {i}" for i in range(12))
        assert paragraph_count_bucket(body) == "11+"


class TestNumberedListCountBucket:
    def test_no_numbered_lines(self):
        assert numbered_list_count_bucket("그냥 평문") == "0"

    def test_counts_arabic_numbered_lines(self):
        body = "1. 첫째\n2. 둘째"
        assert numbered_list_count_bucket(body) == "0-2"

    def test_counts_korean_ordinal_lines(self):
        body = "가. 첫째\n나. 둘째"
        assert numbered_list_count_bucket(body) == "0-2"


class TestBodyLengthBucket:
    def test_empty(self):
        assert body_length_bucket("") == "0"

    def test_short(self):
        assert body_length_bucket("가" * 100) == "0-200"

    def test_long(self):
        assert body_length_bucket("가" * 5000) == "4001+"


class TestStructureFingerprint:
    def test_deterministic_for_same_input(self):
        a = structure_fingerprint("T1-1", "report", "본문 내용")
        b = structure_fingerprint("T1-1", "report", "본문 내용")
        assert a == b

    def test_different_template_differs(self):
        a = structure_fingerprint("T1-1", "report", "본문 내용")
        b = structure_fingerprint("T1-2", "report", "본문 내용")
        assert a != b

    def test_prefixed_with_sha256(self):
        assert structure_fingerprint("T1-1", "report", "x").startswith("sha256:")

    def test_same_bucket_gives_same_fingerprint_despite_different_text(self):
        # 같은 template/doc_type/bucket 조합이면 실제 문장이 달라도 같은 fingerprint —
        # "형식" 반복을 잡는 게 목적이지 "내용" 중복을 잡는 게 아니다.
        a = structure_fingerprint("T1-1", "report", "짧은 본문 A")
        b = structure_fingerprint("T1-1", "report", "짧은 본문 B")
        assert a == b
