from __future__ import annotations

from inspect import getsource
from pathlib import Path
import subprocess
import sys

from rd2.source_generation.classification_taxonomy import ClauseNumber
from rd2.source_generation.contracts import TargetClassification
from rd2.source_generation.rds_writeback import SourceRow
from scripts.run_seoul_official_batch import (
    _RDS_COLUMNS,
    _fetch_rds_rows,
    _iter_rds_items,
    _report_section,
    _targets,
    main,
)


def test_seoul_batch_targets_only_source_sensitive_clause_six():
    targets = _targets()

    assert targets
    assert all(item.classification == TargetClassification.S for item in targets)
    assert all(item.clause_no == ClauseNumber.CLAUSE_6 for item in targets)


def test_seoul_batch_does_not_override_the_source_aware_seed():
    """완성된 채용·민원 시나리오를 주입하지 않고 core 조립기에 맡긴다."""

    source = getsource(main)
    assert "sensitive_seed=" not in source
    assert "_sensitive_seed" not in source


def test_html_report_distinguishes_approval_from_technical_completion():
    html = _report_section(
        {
            "source_file": "원문.hwpx",
            "approval_status": "excluded_after_retry",
            "technical_succeeded": True,
            "attempt_count": 2,
            "attempts": [{"attempt": 1}, {"attempt": 2}],
        },
        1,
    )

    assert "재생성 후 제외" in html
    assert "S 학습 승인" not in html
    assert "technical_succeeded" in html


def test_seoul_batch_script_adds_repo_root_when_run_by_path():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "run_seoul_official_batch.py"
    code = (
        "import runpy, sys; "
        f"runpy.run_path({str(script)!r}); "
        f"assert {str(root)!r} in sys.path"
    )

    subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj


def _rds_row(**overrides):
    values = {
        "id": 7,
        "source": "alio",
        "doc_type": "audit_result",
        "title": "감사 결과 통보",
        "ordering_agency": "한국가스공사",
        "department": None,
        "unit_task": None,
        "production_date": None,
        "subject_category": None,
        "body_text": None,
        "body_file_path": None,
    }
    values.update(overrides)
    return tuple(values[name] for name in _RDS_COLUMNS)


def test_rds_reader_only_takes_open_documents_with_a_reachable_body():
    """입력은 항상 O다 — C/S 라벨 문서는 본문이 없어 후보가 아니다."""

    connection = _FakeConnection([_rds_row()])

    _fetch_rds_rows(
        connection,
        source="alio",
        doc_type=None,
        require_file=True,
        limit=40,
    )

    sql = connection.cursor_obj.sql
    assert "cso_classification = 'O'" in sql
    assert "body_file_path IS NOT NULL" in sql
    assert connection.cursor_obj.params == ["alio", 40]


def test_rds_reader_allows_body_text_rows_when_asked():
    connection = _FakeConnection([])

    _fetch_rds_rows(
        connection,
        source=None,
        doc_type=None,
        require_file=False,
        limit=10,
    )

    assert "body_text IS NOT NULL" in connection.cursor_obj.sql


def test_rds_items_skip_rows_whose_file_is_gone(tmp_path):
    """파일이 없는 행을 body_text로 조용히 대체하지 않는다 — 마스킹 자리가
    사라진 본문이면 mask_restoration route가 서지 않기 때문."""

    row = SourceRow(
        id=7,
        source="alio",
        body_file_path="alio/audit_result/1-500/없는파일.pdf",
        body_text="본문이 있긴 하다. 그래도 파일 경로가 먼저다.",
    )

    items = list(
        _iter_rds_items(
            [row],
            files_root=tmp_path,
            allow_body_text=False,
        )
    )

    assert items == []


def test_rds_items_fall_back_to_body_text_only_when_allowed(tmp_path):
    body = "\n".join(
        f"{index}. 심의 대상자에 대한 처리 결과를 다음과 같이 통보합니다."
        for index in range(1, 6)
    )
    row = SourceRow(id=7, source="alio", body_text=body)

    items = list(
        _iter_rds_items([row], files_root=tmp_path, allow_body_text=True)
    )

    assert len(items) == 1
    # 원문 행 id가 스냅샷 id에 남아야 생성 결과에서 원문을 역추적할 수 있다.
    assert items[0].snapshot.source_document_id == "alio-7"
    assert items[0].row is row


def test_rds_upsert_happens_after_rendering():
    """PDF가 최종 산출물이 되면 렌더 결과가 코퍼스 포함 여부를 정해야 한다.
    순서가 뒤집히면 --require-render-ok가 아무것도 막지 못한다."""

    source = getsource(main)

    render_at = source.index("render_input_file(")
    upsert_at = source.index("store.upsert(")

    assert upsert_at > render_at
    assert "require_render_ok" in source[render_at:upsert_at]


def test_render_gate_is_off_while_templates_are_being_rebuilt():
    """실측(2026-08-03): 검증기를 통과한 35건 중 23건이 렌더 검증의
    missing source text로 떨어졌는데, 원인이 생성이 아니라 곧 교체될
    템플릿이라 기본으로 막으면 멀쩡한 생성물을 버린다."""

    source = getsource(main)

    assert '"--require-render-ok"' in source
    # 기본이 꺼져 있어야 한다 — store_true는 기본값 False다.
    assert "args.require_render_ok and record.get" in source


def test_rds_reader_filters_to_extensions_the_snapshot_builders_can_read():
    """실측(2026-08-03 운영 RDS): O 행의 파일은 pdf 13,596 / hwp 9,647 /
    xlsx 3,435 / hwpx 2,580이다. 확장자를 SQL에서 거르지 않으면 스캔 한도가
    못 읽는 파일로 채워진다 — alio를 그대로 훑어 60행 전부 xlsx라 0건이었다."""

    connection = _FakeConnection([])

    _fetch_rds_rows(
        connection,
        source="alio",
        doc_type=None,
        require_file=True,
        limit=60,
    )

    sql = connection.cursor_obj.sql.lower()
    # %%로 써야 한다 — pymysql이 파라미터 바인딩에 % 포맷을 쓰므로 홑 %는
    # "unsupported format character"로 죽는다(실측 2026-08-03 EC2).
    assert "like '%%.pdf'" in sql
    assert "like '%%.hwpx'" in sql
