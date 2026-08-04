import pytest

from rd2.generators.security_mark import (
    SYNTHETIC_SECURITY_STAMP_LABELS,
    generate_classification_stamp,
    generate_military_secret_content_notice,
    generate_military_secret_mark,
    generate_page_watermark,
    generate_reclassification_notice,
    generate_reclassification_old_mark,
    generate_security_mark,
    generate_synthetic_security_stamp,
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


class TestGenerateSyntheticSecurityStamp:
    def test_creates_each_supported_monochrome_stamp(self, tmp_path):
        outputs = []
        for label in SYNTHETIC_SECURITY_STAMP_LABELS:
            output = generate_synthetic_security_stamp(
                tmp_path / f"{label.replace(' ', '_').lower()}.png",
                label,
            )
            assert output.exists()
            assert output.stat().st_size > 0
            outputs.append(output.read_bytes())

        assert len(set(outputs)) == len(SYNTHETIC_SECURITY_STAMP_LABELS)

    def test_normalizes_case_and_spacing_deterministically(self, tmp_path):
        a = generate_synthetic_security_stamp(
            tmp_path / "a.png",
            " confidential ",
        )
        b = generate_synthetic_security_stamp(
            tmp_path / "b.png",
            "CONFIDENTIAL",
        )
        assert a.read_bytes() == b.read_bytes()

    def test_rejects_unregistered_label(self, tmp_path):
        with pytest.raises(ValueError, match="알 수 없는 가상 보안"):
            generate_synthetic_security_stamp(tmp_path / "x.png", "CLASSIFIED")


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


class TestGenerateSecurityMarkAlias:
    """generate_security_mark는 하위호환용 별칭 — generate_classification_stamp와 동일 동작."""

    def test_alias_produces_same_output_as_stamp(self, tmp_path):
        alias_out = generate_security_mark(tmp_path / "alias.png", seed=5)
        stamp_out = generate_classification_stamp(tmp_path / "stamp.png", seed=5)
        assert alias_out.read_bytes() == stamp_out.read_bytes()


class TestGenerateMilitarySecretContentNotice:
    """비밀표시 규정 제9항 — 일반 기관 문서에 군사기밀 사항이 섞였을 때 붙는 붉은 문구."""

    def test_creates_png_file(self, tmp_path):
        output = generate_military_secret_content_notice(tmp_path / "notice.png", seed=1)
        assert output.exists()
        assert output.suffix == ".png"
        assert output.stat().st_size > 0

    def test_deterministic_given_same_seed(self, tmp_path):
        a = generate_military_secret_content_notice(tmp_path / "a.png", seed=7)
        b = generate_military_secret_content_notice(tmp_path / "b.png", seed=7)
        assert a.read_bytes() == b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "notice.png"
        output = generate_military_secret_content_notice(nested, seed=1)
        assert output.exists()


class TestGenerateReclassificationOldMark:
    """[별표 2] 7호 — 재분류된 문서의 예전 등급 마크(붉은 대각선으로 삭제 표시)."""

    def test_creates_png_file_for_each_grade(self, tmp_path):
        for grade in ("1급", "2급", "3급"):
            output = generate_reclassification_old_mark(tmp_path / f"{grade}.png", grade, seed=1)
            assert output.exists()
            assert output.suffix == ".png"
            assert output.stat().st_size > 0

    def test_different_grades_produce_different_images(self, tmp_path):
        a = generate_reclassification_old_mark(tmp_path / "1.png", "1급", seed=1)
        b = generate_reclassification_old_mark(tmp_path / "2.png", "2급", seed=1)
        assert a.read_bytes() != b.read_bytes()

    def test_deterministic_given_same_seed(self, tmp_path):
        a = generate_reclassification_old_mark(tmp_path / "a.png", "1급", seed=42)
        b = generate_reclassification_old_mark(tmp_path / "b.png", "1급", seed=42)
        assert a.read_bytes() == b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "mark.png"
        output = generate_reclassification_old_mark(nested, "3급", seed=1)
        assert output.exists()

    def test_unknown_grade_raises(self, tmp_path):
        with pytest.raises(ValueError, match="알 수 없는 군사기밀 등급"):
            generate_reclassification_old_mark(tmp_path / "x.png", "4급", seed=1)

    def test_differs_from_plain_grade_mark(self, tmp_path):
        """붉은 대각선이 그어진 예전 등급 마크는 일반 등급 마크와 달라야 한다."""
        old_mark = generate_reclassification_old_mark(tmp_path / "old.png", "1급", seed=1)
        plain_mark = generate_military_secret_mark(tmp_path / "plain.png", "1급", seed=1)
        assert old_mark.read_bytes() != plain_mark.read_bytes()


class TestGenerateReclassificationNotice:
    """[별표 2] 7호 — 재분류 근거(...에 따른 재분류(날짜) / 직책·계급·성명) 표시."""

    def _make(self, path, **overrides):
        kwargs = {
            "basis_text": "군사기밀 보호법 시행령 제7조",
            "reclass_date": "2026-03-15",
            "position": "보안담당관",
            "rank": "대령",
            "name": "김도현",
            "seed": 1,
        }
        kwargs.update(overrides)
        return generate_reclassification_notice(path, **kwargs)

    def test_creates_png_file(self, tmp_path):
        output = self._make(tmp_path / "notice.png")
        assert output.exists()
        assert output.suffix == ".png"
        assert output.stat().st_size > 0

    def test_different_names_produce_different_images(self, tmp_path):
        a = self._make(tmp_path / "a.png", name="김도현")
        b = self._make(tmp_path / "b.png", name="이준서")
        assert a.read_bytes() != b.read_bytes()

    def test_different_dates_produce_different_images(self, tmp_path):
        a = self._make(tmp_path / "a.png", reclass_date="2026-03-15")
        b = self._make(tmp_path / "b.png", reclass_date="2027-01-01")
        assert a.read_bytes() != b.read_bytes()

    def test_creates_parent_directory_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "notice.png"
        output = self._make(nested)
        assert output.exists()
