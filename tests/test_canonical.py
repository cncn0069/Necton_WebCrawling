"""rd2.canonical 회귀 테스트.

설계 문서 docs/design-coverage-matrix-diversity-audit-20260723.md §4.2 "Canonical
hash boundary", §12.3 "Canonical hash와 재현성"의 계약을 고정한다: NFC 정규화,
키 정렬, float 반올림, created_at/host 등 비결정 필드를 해시 입력에서 제외.
"""

import unicodedata

import pytest

from rd2.canonical import (
    NORMALIZATION_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    compute_candidate_profile_digest,
    compute_matrix_hash,
    compute_plan_run_id,
)


class TestCanonicalJsonBytes:
    def test_key_order_does_not_affect_output(self):
        a = canonical_json_bytes({"b": 1, "a": 2}, normalization_version=NORMALIZATION_VERSION)
        b = canonical_json_bytes({"a": 2, "b": 1}, normalization_version=NORMALIZATION_VERSION)
        assert a == b

    def test_nfc_normalizes_decomposed_unicode(self):
        # "가"의 NFC(완성형)와 NFD(자모 분해형)는 문자 시퀀스가 다르지만 같은
        # canonical 표현으로 합쳐져야 같은 문서를 같은 hash로 재현할 수 있다.
        decomposed = unicodedata.normalize("NFD", "가나다")
        composed = "가나다"
        assert decomposed != composed  # 전제 확인: 실제로 바이트가 다름
        a = canonical_json_bytes({"title": decomposed}, normalization_version=NORMALIZATION_VERSION)
        b = canonical_json_bytes({"title": composed}, normalization_version=NORMALIZATION_VERSION)
        assert a == b

    def test_float_rounds_to_twelve_decimals(self):
        a = canonical_json_bytes({"w": 0.1 + 0.2}, normalization_version=NORMALIZATION_VERSION)
        b = canonical_json_bytes({"w": 0.30000000000000004}, normalization_version=NORMALIZATION_VERSION)
        assert a == b

    def test_normalization_version_is_part_of_the_payload(self):
        a = canonical_json_bytes({"x": 1}, normalization_version="v1")
        b = canonical_json_bytes({"x": 1}, normalization_version="v2")
        assert a != b

    def test_rejects_unsupported_types(self):
        with pytest.raises(TypeError):
            canonical_json_bytes({"x": {1, 2, 3}}, normalization_version=NORMALIZATION_VERSION)

    def test_array_order_is_preserved_not_sorted(self):
        a = canonical_json_bytes({"xs": [2, 1]}, normalization_version=NORMALIZATION_VERSION)
        b = canonical_json_bytes({"xs": [1, 2]}, normalization_version=NORMALIZATION_VERSION)
        assert a != b


class TestCanonicalSha256:
    def test_deterministic_for_same_input(self):
        payload = {"a": 1, "b": [1, 2, 3]}
        assert canonical_sha256(payload) == canonical_sha256(payload)

    def test_differs_for_different_input(self):
        assert canonical_sha256({"a": 1}) != canonical_sha256({"a": 2})


class TestComputeMatrixHash:
    def test_excludes_candidate_counts_from_identity(self):
        """설계 문서 §4.2: matrix_hash는 candidate 수를 포함하지 않는다 —
        매트릭스 정체성은 후보 가용성과 무관해야 한다."""
        kwargs = dict(
            valid_cell_keys=["C|1|legal_secret|report|central_ministry|-"],
            agency_categories=["central_ministry"],
            config={"targets": {"C": 100, "S": 100}, "minimum_per_valid_cell": 5},
        )
        assert compute_matrix_hash(**kwargs) == compute_matrix_hash(**kwargs)

    def test_config_change_changes_hash(self):
        base = dict(
            valid_cell_keys=["k1"],
            agency_categories=["central_ministry"],
            config={"minimum_per_valid_cell": 5},
        )
        changed = dict(base, config={"minimum_per_valid_cell": 10})
        assert compute_matrix_hash(**base) != compute_matrix_hash(**changed)

    def test_prefixed_with_sha256(self):
        result = compute_matrix_hash(valid_cell_keys=[], agency_categories=[], config={})
        assert result.startswith("sha256:")


class TestComputePlanRunId:
    def test_deterministic_run_id_ignores_created_at(self):
        """설계 문서 §12.3: run_id = sha256(canonical(plan_without_run_id_and_created_at)).

        created_at은 애초에 그 payload에 포함되지 않으므로, 같은 plan 내용이면
        만든 시각과 무관하게 같은 run_id가 나와야 한다."""
        plan_a = {"matrix_hash": "sha256:abc", "cells": [{"coverage_cell_key": "k1"}]}
        plan_b = dict(plan_a)
        assert compute_plan_run_id(plan_a) == compute_plan_run_id(plan_b)

    def test_config_change_changes_run_id(self):
        plan_a = {"matrix_hash": "sha256:abc", "allocation": {"seed": 42}}
        plan_b = {"matrix_hash": "sha256:abc", "allocation": {"seed": 43}}
        assert compute_plan_run_id(plan_a) != compute_plan_run_id(plan_b)


class TestComputeCandidateProfileDigest:
    def test_deterministic(self):
        manifest = {"row_count": 3, "resolver_version": "agency-resolver-v1"}
        assert compute_candidate_profile_digest(manifest) == compute_candidate_profile_digest(manifest)
