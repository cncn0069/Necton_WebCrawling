import pytest

from rd2.generators.security_mark import (
    generate_agency_letterhead_mark,
    generate_agency_watermark,
    generate_classification_stamp,
    generate_military_secret_mark,
    generate_page_watermark,
    generate_security_mark,
)


class TestGenerateClassificationStamp:
    def test_creates_png_file(self, tmp_path):
        output = generate_classification_stamp(tmp_path / "stamp.png", seed=1)
        assert output.exists()
        assert output.suffix == ".png"
        assert output.stat().st_size > 0

    def test_deterministic_given_same_seed(self, tmp_path):
        a = generate_classification_stamp(tmp_path / "a.png", seed=42)
        b = generate_classification_stamp(tmp_path / "b.png", seed=42)
        assert a.read_bytes() == b.read_bytes()

    def test_seed_no_longer_affects_output(self, tmp_path):
        """마크는 회전하지 않는다(2026-07-21 사용자 결정) — 회전이 유일한 seed
        의존 변화 요소였으므로, 서로 다른 seed도 이제 동일한 이미지를 낸다."""
        a = generate_classification_stamp(tmp_path / "a.png", seed=1)
        b = generate_classification_stamp(tmp_path / "b.png", seed=2)
        assert a.read_bytes() == b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "stamp.png"
        output = generate_classification_stamp(nested, seed=1)
        assert output.exists()


class TestGeneratePageWatermark:
    def test_creates_png_file(self, tmp_path):
        output = generate_page_watermark(tmp_path / "watermark.png", seed=1)
        assert output.exists()
        assert output.suffix == ".png"
        assert output.stat().st_size > 0

    def test_deterministic_given_same_seed(self, tmp_path):
        a = generate_page_watermark(tmp_path / "a.png", seed=7)
        b = generate_page_watermark(tmp_path / "b.png", seed=7)
        assert a.read_bytes() == b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "watermark.png"
        output = generate_page_watermark(nested, seed=1)
        assert output.exists()


class TestGenerateMilitarySecretMark:
    def test_creates_png_file_for_each_grade(self, tmp_path):
        for grade in ("1급", "2급", "3급"):
            output = generate_military_secret_mark(tmp_path / f"{grade}.png", grade, seed=1)
            assert output.exists()
            assert output.suffix == ".png"
            assert output.stat().st_size > 0

    def test_different_grades_produce_different_images(self, tmp_path):
        a = generate_military_secret_mark(tmp_path / "1.png", "1급", seed=1)
        b = generate_military_secret_mark(tmp_path / "2.png", "2급", seed=1)
        assert a.read_bytes() != b.read_bytes()

    def test_deterministic_given_same_seed(self, tmp_path):
        a = generate_military_secret_mark(tmp_path / "a.png", "1급", seed=42)
        b = generate_military_secret_mark(tmp_path / "b.png", "1급", seed=42)
        assert a.read_bytes() == b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "mark.png"
        output = generate_military_secret_mark(nested, "3급", seed=1)
        assert output.exists()

    def test_unknown_grade_raises(self, tmp_path):
        with pytest.raises(ValueError, match="알 수 없는 군사기밀 등급"):
            generate_military_secret_mark(tmp_path / "x.png", "4급", seed=1)


class TestGenerateAgencyLetterheadMark:
    def test_creates_png_file(self, tmp_path):
        output = generate_agency_letterhead_mark(tmp_path / "mark.png", "국정원.png", seed=1)
        assert output.exists()
        assert output.suffix == ".png"
        assert output.stat().st_size > 0

    def test_different_logos_produce_different_images(self, tmp_path):
        a = generate_agency_letterhead_mark(tmp_path / "a.png", "국정원.png", seed=1)
        b = generate_agency_letterhead_mark(tmp_path / "b.png", "정부부처.png", seed=1)
        assert a.read_bytes() != b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "mark.png"
        output = generate_agency_letterhead_mark(nested, "정부부처.png", seed=1)
        assert output.exists()


class TestGenerateAgencyWatermark:
    def test_creates_png_file(self, tmp_path):
        output = generate_agency_watermark(tmp_path / "wm.png", "국정원.png", seed=1)
        assert output.exists()
        assert output.suffix == ".png"
        assert output.stat().st_size > 0

    def test_different_logos_produce_different_images(self, tmp_path):
        a = generate_agency_watermark(tmp_path / "a.png", "국정원.png", seed=1)
        b = generate_agency_watermark(tmp_path / "b.png", "정부부처.png", seed=1)
        assert a.read_bytes() != b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "wm.png"
        output = generate_agency_watermark(nested, "정부부처.png", seed=1)
        assert output.exists()


class TestGenerateSecurityMarkAlias:
    """generate_security_mark는 하위호환용 별칭 — generate_classification_stamp와 동일 동작."""

    def test_alias_produces_same_output_as_stamp(self, tmp_path):
        alias_out = generate_security_mark(tmp_path / "alias.png", seed=5)
        stamp_out = generate_classification_stamp(tmp_path / "stamp.png", seed=5)
        assert alias_out.read_bytes() == stamp_out.read_bytes()
