"""형식·출처 집중도 — 설계 문서 §7.4.

동일 template 반복 자체는 실패가 아니다 — 특정 셀/기관군이 하나의 fingerprint에
과도하게 몰렸을 때만 anomaly가 된다(§7.4). 이 모듈은 판정을 내리지 않고
집중도 지표만 계산한다. 판정은 review_sampler.py의 reason 선정에서 쓴다.
"""

from __future__ import annotations

from rd2.audit.row_contract import AuditRow
from rd2.audit.structure_fingerprint import structure_fingerprint


class FormatConcentrationAccumulator:
    def __init__(self) -> None:
        self._fingerprint_counts: dict[str, int] = {}
        self._template_counts: dict[str, int] = {}
        self._agency_template_counts: dict[str, dict[str, int]] = {}
        self._total = 0
        self._agency_category_seen = False

    def add_row(self, row: AuditRow) -> None:
        self._total += 1
        fingerprint = structure_fingerprint(row.template_id, row.doc_type, row.body_text)
        self._fingerprint_counts[fingerprint] = self._fingerprint_counts.get(fingerprint, 0) + 1
        if row.template_id:
            self._template_counts[row.template_id] = self._template_counts.get(row.template_id, 0) + 1
        if row.agency_category:
            self._agency_category_seen = True
            per_agency = self._agency_template_counts.setdefault(row.agency_category, {})
            per_agency[row.template_id] = per_agency.get(row.template_id, 0) + 1

    def fingerprint_of(self, row: AuditRow) -> str:
        return structure_fingerprint(row.template_id, row.doc_type, row.body_text)

    def largest_fingerprint_cluster_id(self) -> str | None:
        if not self._fingerprint_counts:
            return None
        return max(self._fingerprint_counts.items(), key=lambda kv: kv[1])[0]

    def finalize(self) -> dict:
        if self._total == 0:
            return {
                "total_rows": 0,
                "distinct_fingerprints": 0,
                "largest_fingerprint_cluster": None,
                "distinct_templates": 0,
                "largest_template_share": None,
                "template_counts": {},
                "agency_template_distribution": "not_available",
            }

        largest_fp, largest_fp_count = max(self._fingerprint_counts.items(), key=lambda kv: kv[1])
        largest_template_share = None
        if self._template_counts:
            largest_template_share = max(self._template_counts.values()) / self._total

        return {
            "total_rows": self._total,
            "distinct_fingerprints": len(self._fingerprint_counts),
            "largest_fingerprint_cluster": {
                "fingerprint": largest_fp,
                "count": largest_fp_count,
                "share": largest_fp_count / self._total,
            },
            "distinct_templates": len(self._template_counts),
            "largest_template_share": largest_template_share,
            "template_counts": dict(sorted(self._template_counts.items())),
            "agency_template_distribution": (
                {
                    agency: dict(sorted(counts.items()))
                    for agency, counts in sorted(self._agency_template_counts.items())
                }
                if self._agency_category_seen
                else "not_available"
            ),
        }
