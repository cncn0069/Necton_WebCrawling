from pathlib import Path

from scripts.explain_cs_provenance import explain_row


def test_span_seeded_report_explains_original_and_span():
    report = explain_row(
        {
            "row_id": "5-span-0",
            "seed_type": "span_seeded",
            "title": "제목",
            "ordering_agency": "기관",
            "seed_source_path": "data/source.hwp",
            "seed_line_ids": "[12, 13]",
            "matched_span_text": "심사평가위원",
            "seed_context_text": "앞 문맥\n심사평가위원\n뒤 문맥",
            "field_source": '{"body_text":"synthesized_from_source_document"}',
            "generation_trace_json": '{"pipeline":"span_seeded"}',
        },
        csv_path=Path("result.csv"),
    )
    assert "원본 상대경로: `data/source.hwp`" in report
    assert "line IDs: `[12, 13]`" in report
    assert "심사평가위원" in report
    assert "LLM 입력 문맥" in report


def test_legacy_fallback_report_is_honest_about_missing_scenario():
    report = explain_row(
        {
            "row_id": "7-fallback-3",
            "seed_type": "synthetic_fallback",
            "title": "신제품 출시 보고서",
            "ordering_agency": "국립암센터",
            "field_source": '{"ordering_agency":"sampled_from_real_db_value"}',
        },
        csv_path=Path("result.csv"),
    )
    assert "원본 파일: 없음" in report
    assert "구버전 CSV라 정확한 값 미기록" in report
