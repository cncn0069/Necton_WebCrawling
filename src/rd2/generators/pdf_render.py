"""Jinja2 HTML/CSS 템플릿을 Chromium으로 인쇄해 선택 가능한 텍스트 PDF를 만든다."""

from __future__ import annotations

import base64
import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from rd2.generators.agency_categories import (
    CATEGORY_CENTRAL_MINISTRY,
    CATEGORY_EDUCATION_OFFICE,
    CATEGORY_METRO_LOCAL_GOVERNMENT,
    CATEGORY_PUBLIC_CORPORATION,
    CATEGORY_RESEARCH_INSTITUTE,
)
from rd2.generators.doc_templates import (
    RECIPIENT_CIVIL_PETITIONER,
    AdminStatus,
    ApprovalState,
    find_status_variant,
    find_template,
    validate_row,
)
from rd2.storage.naming import DOC_TYPE_AUDIT_RESULT

_TEMPLATE_DIR = Path(__file__).with_name("templates")
_CSS_PATH = _TEMPLATE_DIR / "document.css"
_GANADARA = ("가", "나", "다", "라", "마", "바", "사", "아")
_APPROVAL_BOX_KEYWORDS = ("감사", "기획", "인사")
_OFFICIAL_FORM_SLOGAN = "국민의 나라 정의로운 대한민국"
_SYNTHETIC_SIGNER_NAMES = ("김민준", "이서연", "박지훈", "최수아", "정도현", "강하은", "윤재원")
_SYNTHETIC_PETITIONER_NAMES = ("오세림", "한도윤", "임가언", "신우철", "배소민", "송재이")
_SYNTHETIC_RANKS = ("행정사무관", "공업사무관", "시설사무관", "행정주사", "행정서기")
_SYNTHETIC_DEPARTMENTS = ("경영관리과", "장비구매과", "기획조정과", "운영지원과", "총무과")
_SYNTHETIC_SUPPLIERS = ("한빛정보시스템(주)", "누리테크(주)", "(주)다솔아이씨티")
_SYNTHETIC_RESEARCH_ORGS = ("한빛정책연구원", "다솔기술연구소", "누리행정연구원")
_SYNTHETIC_ITEMS = (
    ("업무용 데스크톱", "i7/16GB/512GB", 1_250_000),
    ("네트워크 스위치", "48포트 기가비트", 2_840_000),
    ("문서스캐너", "A3 양면 고속", 1_980_000),
    ("서버 랙", "42U 표준형", 3_150_000),
)
_SYNTHETIC_ADDRESSES = (
    "우 12345  한빛시 중앙대로 88 (가온동)",
    "우 54321  다솔시 미래로 24 (새길동)",
    "우 33221  누리시 행정로 7 (온빛동)",
)
# 회의록 셸 — 제22대국회 제436회(임시회) 제4차 국회본회의(전체회의) (2026.06.30.)
# 회의록에서 확인한 등록형 헤더(회기·차수·일시), 의사일정, 개의/산회 시각,
# 발언자 태그(◯직책성명) 관례를 모든 meeting_minutes 템플릿 공통 셸로 사용한다.
# 실제 회기·발언자·안건은 국회 사안이므로 복사하지 않고 위원회명·역할·발언은 합성한다.
_WEEKDAYS_KO = ("월", "화", "수", "목", "금", "토", "일")


def _assembly_time(hour: int, minute: int) -> str:
    period = "오전" if hour < 12 else "오후"
    display_hour = hour if hour <= 12 else hour - 12
    return f"{period} {display_hour}시 {minute:02d}분"


def _object_particle(word: str) -> str:
    """받침 유무에 따라 '을'/'를'을 고른다 — 의사일정 상정 문구용."""
    if not word:
        return "를"
    last = word[-1]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - 0xAC00) % 28 != 0 else "를"
    return "를"


@dataclass(frozen=True)
class LayoutSpec:
    category: str
    title_align: str
    title_font_size: int
    info_style: str
    margins_mm: tuple[float, float, float, float]


@dataclass(frozen=True)
class _TextNode:
    """템플릿 컨텍스트 단위 테스트용 경량 텍스트 노드."""
    text: str


@dataclass(frozen=True)
class Table:
    """템플릿 컨텍스트 단위 테스트용 경량 표 노드(reportlab과 무관)."""
    _cellvalues: list[list[str]]


LAYOUT_SPECS: dict[str, LayoutSpec] = {
    CATEGORY_CENTRAL_MINISTRY: LayoutSpec(CATEGORY_CENTRAL_MINISTRY, "center", 16, "table_row", (25, 25, 25, 25)),
    CATEGORY_METRO_LOCAL_GOVERNMENT: LayoutSpec(CATEGORY_METRO_LOCAL_GOVERNMENT, "left", 15, "table_kv", (20, 25, 20, 20)),
    CATEGORY_RESEARCH_INSTITUTE: LayoutSpec(CATEGORY_RESEARCH_INSTITUTE, "center", 18, "boxed", (25, 30, 20, 20)),
    CATEGORY_EDUCATION_OFFICE: LayoutSpec(CATEGORY_EDUCATION_OFFICE, "left", 14, "plain_list", (18, 25, 22, 22)),
    CATEGORY_PUBLIC_CORPORATION: LayoutSpec(CATEGORY_PUBLIC_CORPORATION, "left", 13, "minimal", (15, 25, 15, 15)),
}


def _file_uri(path: Path | None) -> str | None:
    return path.resolve().as_uri() if path is not None and path.exists() else None


def _image_data_uri(path: Path | None) -> str | None:
    """header_template/footer_template용 base64 data URI.

    Playwright 헤더/푸터 템플릿은 body와 별도의 격리된 컨텍스트라 file:// 상대
    경로를 안정적으로 못 불러온다 — 바이트를 직접 인라인해야 한다.
    """
    if path is None or not path.exists():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@lru_cache(maxsize=1)
def _embedded_css() -> str:
    # base.html sets its base URI to the templates directory, so the font URLs
    # in document.css resolve to local assets without copying ~12 MB into every
    # temporary HTML file.
    return _CSS_PATH.read_text(encoding="utf-8")


def _paragraphs(row: dict) -> list[str]:
    return [p.strip() for p in (row.get("body_text") or "").splitlines() if p.strip()]


def _seed(row: dict) -> int:
    return sum(ord(ch) for ch in (row.get("row_id") or ""))


def _petitioner(seed: int) -> dict[str, str]:
    address = _SYNTHETIC_ADDRESSES[seed % len(_SYNTHETIC_ADDRESSES)].split("  ", 1)[-1]
    return {
        "name": _SYNTHETIC_PETITIONER_NAMES[seed % len(_SYNTHETIC_PETITIONER_NAMES)],
        "phone": f"010-{1000 + seed % 9000:04d}-{1000 + (seed * 7) % 9000:04d}",
        "address": address,
    }


def _approval_context(department: str | None) -> dict | None:
    if not any(keyword in (department or "") for keyword in _APPROVAL_BOX_KEYWORDS):
        return None
    return {"headers": ("담당", "검토", "결재"), "values": ("주무관", "팀장", "부서장")}


def _signature_context(template, seed: int, status: AdminStatus | None) -> dict | None:
    if template.approval_state is ApprovalState.NONE:
        return None
    if status is AdminStatus.DRAFT:
        signed = 0
    elif template.approval_state is ApprovalState.PENDING:
        signed = template.approval_signed_count
    else:
        signed = len(template.approval_positions)
    names = tuple(
        _SYNTHETIC_SIGNER_NAMES[(seed + idx) % len(_SYNTHETIC_SIGNER_NAMES)] if idx < signed else ""
        for idx in range(len(template.approval_positions))
    )
    return {"headers": (f"★{template.approval_positions[0]}", *template.approval_positions[1:]), "names": names}


def _build_signature_line(positions, signed_count, _style=None, *, seed=0):
    if not positions:
        return []
    names = [_SYNTHETIC_SIGNER_NAMES[(seed + i) % len(_SYNTHETIC_SIGNER_NAMES)] if i < signed_count else "" for i in range(len(positions))]
    return [Table([[f"★{positions[0]}", *positions[1:]], names])]


def _build_approval_box(department, _style=None, *, state=None):
    if state is ApprovalState.NONE:
        return []
    if state is None and not any(k in (department or "") for k in _APPROVAL_BOX_KEYWORDS):
        return []
    values = ["주무관", "", ""] if state is ApprovalState.PENDING else ["주무관", "팀장", "부서장"]
    return [Table([["담당", "검토", "결재"], values])]


def _nodes_for_body(row: dict, body_format: str):
    variant = find_status_variant(row.get("document_status"))
    ctx = _body_context(row, body_format, variant.status if variant else None)
    text: list[str] = []
    tables: list[Table] = []
    if body_format == "audit_interim":
        text = ["감사반장 직책 : 감사실장 성명 : OOO", "1. 감사개요", f"감사기간 : {ctx['production_date']} ~ (진행중)", "2. 진행 경과", "3. 향후 계획"]
    elif body_format == "bid_review":
        text = ["평가위원회 구성(안)", "배점 기준(안)", "위원 후보 명단은 선정 확정 시까지 비공개 관리"]
        tables = [Table([["구분", "배점비율", "평가요소"]])]
    elif body_format == "personnel_order":
        tables = [Table([["소  속", "직  급", "성  명", "발 령 사 항"], *ctx["personnel_rows"]])]
    elif body_format == "unit_price":
        text = [f"{ctx['supplier']}와의 납품단가 협상 결과", "영업상 비밀"]
        tables = [Table([["품목", "규격", "수량", "단가(원)", "금액(원)"], *ctx["price_rows"], ["합계", "", "", "", ctx["price_total"]]])]
    elif body_format == "meeting_pending":
        text = ["위원 명단은 심의 종료 시까지 비공개", *[("ㅇ" if i % 2 == 0 else "☞") + " " + p for i, p in enumerate(ctx["discussion"])], "<보류>"]
    elif body_format == "personnel_eval":
        text = [f"평가기간 : {ctx['production_date']} ~ (진행중)"]
        tables = [Table([["순번", "소  속", "직  급", "성  명", "비  고"], *ctx["eval_rows"]])]
    elif body_format == "civil_reply":
        text = [ctx["civil_opening"], ctx["civil_result_heading"], ctx["civil_closing"]]
        p = ctx["petitioner"]
        tables = [Table([["성  명", p["name"]], ["연 락 처", p["phone"]], ["주  소", p["address"]]])]
    elif body_format == "legal_confidential":
        text = ["1. 적용 법률 및 비공개 근거", ctx["purpose"], "2. 보호 대상 및 열람 제한 범위", *ctx["progress"]]
    elif body_format == "national_security":
        text = ["1. 검토 개요", ctx["purpose"], "2. 국가이익 보호 대상", *ctx["progress"], "3. 공개 시 우려"]
    elif body_format == "public_safety":
        text = ["1. 보호 대상", ctx["purpose"], "2. 위험 분석 및 공개 제한 범위", *ctx["progress"]]
    elif body_format == "legal_proceeding":
        text = ["1. 사건·절차 개요", ctx["purpose"], "2. 현재 진행 상태", *ctx["progress"], "3. 공개 제한 정보"]
    return [_TextNode(t) for t in text] + tables


def _build_audit_interim_body_flowables(row, _style=None): return _nodes_for_body(row, "audit_interim")
def _build_personnel_order_body_flowables(row, _style=None, _divider=None): return _nodes_for_body(row, "personnel_order")
def _build_bid_review_body_flowables(row, _style=None): return _nodes_for_body(row, "bid_review")
def _build_unit_price_body_flowables(row, _style=None): return _nodes_for_body(row, "unit_price")
def _build_meeting_pending_body_flowables(row, _style=None): return _nodes_for_body(row, "meeting_pending")
def _build_personnel_eval_body_flowables(row, _style=None): return _nodes_for_body(row, "personnel_eval")
def _build_civil_reply_body_flowables(row, _style=None): return _nodes_for_body(row, "civil_reply")


def _body_context(row: dict, body_format: str, status: AdminStatus | None) -> dict:
    seed = _seed(row)
    paragraphs = _paragraphs(row)
    context: dict = {
        "paragraphs": paragraphs,
        "overview": paragraphs[: len(_GANADARA)],
        "details": paragraphs[len(_GANADARA) :],
        "ganadara": _GANADARA,
        "purpose": paragraphs[0] if paragraphs else "감사 목적 검토 중",
        "subject": paragraphs[0] if paragraphs else (row.get("title") or "(제목 없음)"),
        "progress": paragraphs[1:] or ["관련 자료 징구 및 대상 부서 실지감사 진행 중."],
        "discussion": paragraphs[1:] or ["안건 세부 내용에 대한 위원 질의 및 소관 부서 답변 진행."],
        "production_date": row.get("production_date") or "",
        "department": row.get("department") or "감사 대상 부서",
        "ending": "[이하 작성 중]" if status is AdminStatus.DRAFT else "끝.",
        "agenda_no": 1000 + seed % 900,
    }
    context["personnel_rows"] = [
        [row.get("ordering_agency") or "(기관명)", _SYNTHETIC_RANKS[(seed + i) % len(_SYNTHETIC_RANKS)], _SYNTHETIC_SIGNER_NAMES[(seed + i * 3 + 1) % len(_SYNTHETIC_SIGNER_NAMES)], f"{_SYNTHETIC_DEPARTMENTS[(seed + i) % len(_SYNTHETIC_DEPARTMENTS)]} 근무를 명함"]
        for i in range(2 + seed % 2)
    ]
    context["eval_rows"] = [
        [str(i + 1), _SYNTHETIC_DEPARTMENTS[(seed + i) % len(_SYNTHETIC_DEPARTMENTS)], _SYNTHETIC_RANKS[(seed + i) % len(_SYNTHETIC_RANKS)], _SYNTHETIC_SIGNER_NAMES[(seed + i * 3 + 2) % len(_SYNTHETIC_SIGNER_NAMES)], "평가 진행중"]
        for i in range(2 + seed % 2)
    ]
    price_rows, total = [], 0
    for i in range(2 + seed % 2):
        item, spec, unit = _SYNTHETIC_ITEMS[(seed + i) % len(_SYNTHETIC_ITEMS)]
        qty, amount = 2 + (seed + i) % 8, unit * (2 + (seed + i) % 8)
        total += amount
        price_rows.append([item, spec, str(qty), f"{unit:,}", f"{amount:,}"])
    context.update(
        price_rows=price_rows, price_total=f"{total:,}",
        supplier=_SYNTHETIC_SUPPLIERS[seed % len(_SYNTHETIC_SUPPLIERS)],
        supplier_rep=_SYNTHETIC_SIGNER_NAMES[(seed + 5) % len(_SYNTHETIC_SIGNER_NAMES)],
        contract_no=f"R{25 + seed % 3}TA{10000 + seed % 90000:05d}-00",
    )
    petitioner = _petitioner(seed)
    in_progress = status is AdminStatus.PETITION_IN_PROGRESS
    results = paragraphs[1:] or ["관련 사실관계를 확인 중이며, 확인 내용을 바탕으로 검토할 예정입니다." if in_progress else "관련 규정을 검토한 결과를 안내드립니다."]
    receipt = 100000 + seed % 900000
    context.update(
        petitioner=petitioner,
        civil_results=results,
        civil_opening=(f"귀하께서 신청하신 민원(접수번호 제{receipt}호)의 사실관계 확인 진행 상황을 아래와 같이 안내드립니다." if in_progress else f"귀하께서 신청하신 민원(접수번호 제{receipt}호)에 대한 검토 결과를 아래와 같이 회신합니다."),
        civil_result_heading="2. 확인 진행 상황" if in_progress else "2. 검토 결과",
        civil_closing="ㅇ 사실관계 확인 후 처리 결과를 별도로 안내드릴 예정입니다." if in_progress else "ㅇ 본 회신 내용에 이의가 있으신 경우 담당 부서로 문의하시기 바랍니다.",
    )
    context.update(_assembly_context(row, seed, context["discussion"], context["subject"]))
    context.update(_bid_notice_context(seed, row.get("production_date") or ""))
    context.update(
        research_org=_SYNTHETIC_RESEARCH_ORGS[seed % len(_SYNTHETIC_RESEARCH_ORGS)],
        research_lead=_SYNTHETIC_SIGNER_NAMES[(seed + 2) % len(_SYNTHETIC_SIGNER_NAMES)],
        dept_manager=_SYNTHETIC_SIGNER_NAMES[(seed + 4) % len(_SYNTHETIC_SIGNER_NAMES)],
        dept_officer=_SYNTHETIC_SIGNER_NAMES[(seed + 6) % len(_SYNTHETIC_SIGNER_NAMES)],
    )
    return context


def _bid_notice_context(seed: int, production_date: str) -> dict:
    """bid_review 셸 전용 — 실제 LH 전자입찰공고(입찰공고.pdf)의 건명·금액·일정 표
    구조를 참고하되, 제5호(공고 게시 전 내부검토)이므로 모든 일정은 '예정'이다."""
    base_price = 50_000_000 + (seed * 37_919) % 400_000_000
    try:
        base_date = date.fromisoformat(production_date)
    except ValueError:
        base_date = date(2026, 7, 16)
    notice_date = base_date + timedelta(days=7 + seed % 5)
    submit_open = notice_date + timedelta(days=3)
    submit_close = notice_date + timedelta(days=7)
    open_at = submit_close
    return {
        "bid_estimated_price": f"{base_price:,}",
        "bid_vat": f"{base_price // 10:,}",
        "bid_notice_date": f"{notice_date.isoformat()}(예정)",
        "bid_submit_open": submit_open.isoformat(),
        "bid_submit_close": submit_close.isoformat(),
        "bid_open_at": f"{open_at.isoformat()} 14:00(예정)",
    }


def _assembly_context(row: dict, seed: int, discussion: list[str], subject: str) -> dict:
    """meeting_minutes 셸 전용 필드 — 실제 국회본회의 회의록의 등록형 헤더·의사일정·
    개의/산회 시각·발언자 태그(◯직책성명) 관례를 위원회명·발언은 합성해 재사용한다."""
    chair_role = row.get("meeting_chair_role") or "위원장"
    member_role = row.get("meeting_member_role") or "위원"
    chair_name = _SYNTHETIC_SIGNER_NAMES[seed % len(_SYNTHETIC_SIGNER_NAMES)]
    member_name = _SYNTHETIC_SIGNER_NAMES[(seed + 3) % len(_SYNTHETIC_SIGNER_NAMES)]
    speakers = [
        {
            "role": chair_role, "name": chair_name,
            "line": f"의사일정 제1항 {subject}{_object_particle(subject)} 상정합니다.",
        }
    ]
    for i, line in enumerate(discussion):
        role, name = (member_role, member_name) if i % 2 == 0 else (chair_role, chair_name)
        speakers.append({"role": role, "name": name, "line": line})
    convene_hour, convene_minute = 14 + seed % 4, (seed * 7) % 60
    adjourn_hour, adjourn_minute = convene_hour + 1 + seed % 2, (seed * 11) % 60
    weekday = "-"
    production_date = row.get("production_date") or ""
    try:
        weekday = _WEEKDAYS_KO[date.fromisoformat(production_date).weekday()]
    except ValueError:
        pass
    return {
        "assembly_committee_name": row.get("meeting_committee_name") or f"{row.get('department') or '실무'} 회의",
        "assembly_session_kind": row.get("meeting_session_kind") or "정례회의",
        "assembly_session_label": f"제{58 + seed % 40}회",
        "assembly_doc_label": f"제{1 + seed % 9}차 회의록",
        "assembly_weekday": weekday,
        "assembly_convene_time": _assembly_time(convene_hour, convene_minute),
        "assembly_adjourn_time": _assembly_time(adjourn_hour, adjourn_minute),
        "assembly_agenda_items": [subject],
        "assembly_speakers": speakers,
        "assembly_chair_role": chair_role,
        "assembly_chair_name": chair_name,
    }


def _render_context(
    row: dict,
    category: str,
    watermark_path: Path | None,
    agency_mark_path: Path | None = None,
) -> dict:
    layout = LAYOUT_SPECS.get(category, LAYOUT_SPECS[CATEGORY_PUBLIC_CORPORATION])
    doc_type = row.get("doc_type") or ""
    template = find_template(
        row.get("clause_no") or "", doc_type, row.get("cso_subclause_key")
    )
    variant = find_status_variant(row.get("document_status"))
    status = variant.status if variant else None
    seed = _seed(row)
    context = {
        "row": row,
        "layout": layout,
        "template": template,
        "status": row.get("status") or "ok",
        "status_description": variant.description if variant else None,
        "fields": (("기관", row.get("ordering_agency") or ""), ("담당부서", row.get("department") or ""), ("생산일자", row.get("production_date") or ""), ("분류", row.get("cso_classification") or "")),
        "generic_body_format": "audit_result" if doc_type == DOC_TYPE_AUDIT_RESULT else "standard",
        "approval": _approval_context(row.get("department")),
        "base_uri": _TEMPLATE_DIR.resolve().as_uri() + "/",
        "css": Markup(_embedded_css()),
        "watermark_uri": _file_uri(watermark_path),
        "agency_mark_uri": _file_uri(agency_mark_path),
    }
    context.update(_body_context(row, template.body_format if template else context["generic_body_format"], status))
    if template:
        recipient = template.recipient or "수신자 참조"
        if recipient == RECIPIENT_CIVIL_PETITIONER:
            recipient = f"{context['petitioner']['name']} 귀하"
        title = row.get("title") or "(제목 없음)"
        if status is AdminStatus.DRAFT:
            title = f"(초안) {title}"
        notice = None
        if status is AdminStatus.AGENCY_CONSULT:
            notice = f"※ {row.get('pending_agency') or '관계 부처'} 의견 조회 중 — 회신 접수 후 후속 절차 진행 예정"
        elif status is AdminStatus.ATTACHMENT_MISSING:
            notice = "붙임  관련 검토자료 1부."
        elif status is AdminStatus.DEIDENTIFY_PENDING:
            notice = "※ 본 문서의 개인정보는 비식별 처리 예정임(처리 전 원본)"
        context.update(
            slogan=_OFFICIAL_FORM_SLOGAN, agency=row.get("ordering_agency") or "(기관명)",
            department=row.get("department") or "담당부서", recipient=recipient, title=title,
            body_format=template.body_format, extra_notice=notice,
            form_template=f"forms/{template.form_format}.html",
            signature=_signature_context(template, seed, status), doc_no=10000 + seed % 90000,
            address=_SYNTHETIC_ADDRESSES[seed % len(_SYNTHETIC_ADDRESSES)],
            tel_suffix=1000 + seed % 9000, email_suffix=f"{seed % 100:02d}",
            disclosure_label=template.disclosure_label or "대국민공개",
        )
    return context


_FOOTER_MARK_HEIGHT_MM = 12  # 하단 여백(최소 25mm)엔 여유 있게 들어감
# 실사(2026-07-21)로 확인: Chromium 헤더 템플릿은 내용 앞에 ~5.3mm 정도의
# 여백을 자체적으로 넣는다(원인 불명, 명세에 없는 동작) — 그만큼을 감안해
# 상단은 더 작게 잡아야 최소 여백(public_corporation 15mm) 카테고리에서도
# 본문과 안 겹친다.
_HEADER_MARK_HEIGHT_MM = 8


def _html_to_pdf(
    html: str,
    output_path: Path,
    layout: LayoutSpec,
    *,
    stamp_uri: str | None = None,
    stamp_top_uri: str | None = None,
) -> None:
    """대외비/군사기밀 마크는 body에 position:fixed로 심지 않고 Playwright의
    header_template/footer_template로 그린다.

    실사(2026-07-21)로 확인: 이 Chromium 인쇄 엔진은 상하 여백이 다르면
    position:fixed 오프셋이 콘텐츠 높이 기준으로 반복 타일링돼(페이지 물리
    높이가 아니라) 계산이 어긋나고, 심하면 본문 텍스트와 겹친다. 반면
    header_template/footer_template(페이지 번호가 이미 매 페이지 정상 반복되는
    바로 그 메커니즘)는 브라우저가 각 페이지 여백 영역에 결정적으로 배치해줘서
    이 문제가 없다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("PDF 렌더링에는 playwright가 필요합니다. 프로젝트 의존성을 설치하세요.") from exc
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    footer_mark = (
        f'<img src="{stamp_uri}" style="display:block;margin:0 auto 1mm;height:{_FOOTER_MARK_HEIGHT_MM}mm;">'
        if stamp_uri else ""
    )
    footer_template = (
        f'<div style="width:100%;text-align:center">{footer_mark}'
        '<div style="font-size:9px">- <span class="pageNumber"></span> -</div></div>'
    )
    header_template = (
        f'<div style="width:100%;text-align:center">'
        f'<img src="{stamp_top_uri}" style="display:block;margin:0 auto;height:{_HEADER_MARK_HEIGHT_MM}mm;">'
        "</div>"
        if stamp_top_uri else "<span></span>"
    )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=executable, args=["--allow-file-access-from-files"]
            )
            page = browser.new_page()
            html_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".html", delete=False,
                    dir=output_path.parent,
                ) as temporary:
                    temporary.write(html)
                    html_path = Path(temporary.name)
                page.goto(html_path.resolve().as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                page.emulate_media(media="print")
                page.pdf(
                    path=str(output_path), format="A4", print_background=True,
                    prefer_css_page_size=True, display_header_footer=True,
                    margin={
                        "top": f"{layout.margins_mm[0]}mm", "bottom": f"{layout.margins_mm[1]}mm",
                        "left": f"{layout.margins_mm[2]}mm", "right": f"{layout.margins_mm[3]}mm",
                    },
                    header_template=header_template,
                    footer_template=footer_template,
                )
            finally:
                browser.close()
                if html_path is not None:
                    html_path.unlink(missing_ok=True)
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            raise RuntimeError("Playwright Chromium이 없습니다. `playwright install chromium`을 실행하세요.") from exc
        raise


def render_document_pdf(
    row: dict,
    category: str,
    output_path: Path,
    *,
    security_mark_path: Path | None = None,
    watermark_path: Path | None = None,
    stamp_path: Path | None = None,
    stamp_top_path: Path | None = None,
    agency_mark_path: Path | None = None,
) -> Path:
    """공개 진입점. C 문서에만 페이지 반복 워터마크와 스탬프를 적용한다.

    stamp_top_path는 군사기밀 [별표 2] 등급 마크처럼 상단·하단 양쪽에 같은 마크를
    붙여야 하는 경우에만 넘긴다 — 일반 "대외비" 마크는 하단(stamp_path)만 쓴다.
    agency_mark_path는 문서 좌상단에 한 번 표시하는 기관 마크(레터헤드)다 — 대외비/
    군사기밀 마크와 별개로, C 문서에만 적용한다(2026-07-21 사용자 결정).
    """
    output_path = Path(output_path)
    confidential = (row.get("cso_classification") or "").strip().upper() == "C"
    effective_stamp = (stamp_path or security_mark_path) if confidential else None
    effective_stamp_top = stamp_top_path if confidential else None
    effective_watermark = watermark_path if confidential else None
    effective_agency_mark = agency_mark_path if confidential else None
    env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=select_autoescape(("html",)))
    context = _render_context(row, category, effective_watermark, effective_agency_mark)
    template = context["template"]
    if row.get("cso_subclause_key") and template is None:
        raise ValueError(
            "명시한 세부조항에 맞는 문서 템플릿이 없습니다: "
            f"clause_no={row.get('clause_no')!r}, "
            f"cso_subclause_key={row.get('cso_subclause_key')!r}, "
            f"doc_type={row.get('doc_type')!r}"
        )
    if template is not None:
        violations = validate_row(template, row)
        if violations:
            raise ValueError("템플릿 검증 실패:\n- " + "\n- ".join(violations))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = env.get_template("base.html").render(**context)
    _html_to_pdf(
        html, output_path, context["layout"],
        stamp_uri=_image_data_uri(effective_stamp), stamp_top_uri=_image_data_uri(effective_stamp_top),
    )
    return output_path
