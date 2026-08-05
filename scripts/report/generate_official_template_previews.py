"""동일한 문서 데이터로 Jinja2 + WeasyPrint 공문 템플릿 10종을 생성한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

# 템플릿은 저장소 배치가 아니라 패키지에 속한다. 저장소 루트에서 거슬러 찾으면
# 스크립트가 몇 층 깊이에 있는지를 스크립트가 알아야 해서, 폴더를 옮길 때마다 틀린다.
from rd2.generators.official_document_rendering import TEMPLATE_DIR as _TEMPLATE_DIR
from rd2.generators.weasyprint_runtime import HTML

# 공통 내용은 유지하고, 장문/서식형에 필요한 구조 데이터만 함께 제공한다.
_SHARED_CONTEXT = {
    "emblem": "한빛",
    "slogan": "시민과 함께 만드는 안전한 한빛",
    "agency_name": "한 빛 시",
    "brand_note": "시민 우선",
    "recipient": "관계기관장",
    "via": "업무담당부서",
    "title": "2026년 지역 안전문화 행사 운영계획 알림",
    "intro": (
        "시민의 안전의식을 높이고 지역 공동체의 참여를 확대하기 위하여 "
        "다음과 같이 안전문화 행사를 운영하오니 업무에 참고하시기 바랍니다."
    ),
    "sections": [
        {"text": "관련: 안전정책과-1842(2026. 7. 20.)호", "items": []},
        {
            "text": "행사 운영 개요",
            "items": [
                {"label": "가", "text": "기간: 2026. 8. 17.부터 2026. 8. 21.까지"},
                {"label": "나", "text": "장소: 한빛문화광장 및 시민회관"},
                {"label": "다", "text": "내용: 안전체험, 교육 전시 및 시민 참여 프로그램"},
            ],
        },
        {
            "text": "관계기관에서는 담당자 지정 및 행사 홍보에 협조하여 주시기 바랍니다.",
            "items": [],
        },
    ],
    "long_sections": [
        {
            "title": "추진 목적",
            "items": [
                "시민 참여형 안전교육을 통하여 생활 속 위험 대응 역량을 높인다.",
                "관계기관의 협업 체계를 점검하고 지역 안전문화를 확산한다.",
            ],
        },
        {
            "title": "운영 원칙",
            "items": [
                "연령과 참여 경험을 고려한 체험 중심 프로그램으로 구성한다.",
                "기관별 역할과 책임자를 사전에 지정하여 현장 대응력을 확보한다.",
            ],
        },
        {
            "title": "세부 일정",
            "items": [
                "사전 준비: 2026. 8. 3.부터 8. 14.까지 시설 및 장비를 점검한다.",
                "본행사: 2026. 8. 17.부터 8. 21.까지 프로그램을 운영한다.",
                "사후 정리: 2026. 8. 24.까지 만족도와 운영 결과를 취합한다.",
            ],
        },
        {
            "title": "안전관리 계획",
            "items": [
                "행사 전 위험요소를 점검하고 통행 동선을 구분하여 표시한다.",
                "응급상황 연락망과 현장 의료지원 체계를 행사장에 비치한다.",
                "기상 악화 등 운영 중단 기준을 담당자에게 사전 안내한다.",
            ],
        },
        {
            "title": "기관별 역할",
            "items": [
                "안전정책과는 전체 일정, 현장 안전관리 및 결과 보고를 총괄한다.",
                "교육지원과는 교육 콘텐츠와 진행 인력을 배치한다.",
                "시설관리부서는 행사장 설비와 편의시설을 점검한다.",
            ],
        },
        {
            "title": "홍보 및 참가자 관리",
            "items": [
                "시 누리집과 기관별 안내 채널을 활용하여 행사 일정을 알린다.",
                "현장 접수 인원과 프로그램별 참여 현황을 일 단위로 관리한다.",
            ],
        },
        {
            "title": "행정 처리",
            "items": [
                "필요 물품과 용역은 관련 회계 규정에 따라 집행한다.",
                "기관별 협조사항과 변경 내역은 문서로 기록하여 공유한다.",
            ],
        },
        {
            "title": "결과 보고",
            "items": [
                "운영 실적, 안전사고 유무 및 개선 의견을 행사 종료 후 제출한다.",
                "성과 자료는 다음 연도 안전문화 사업계획 수립에 활용한다.",
            ],
        },
    ],
    "details": [
        {"label": "운영시간", "value": "매일 10:00~17:00"},
        {"label": "참여대상", "value": "한빛시민 누구나"},
        {"label": "참가비", "value": "무료"},
        {"label": "문의처", "value": "안전정책과"},
    ],
    "table": {
        "headers": ["구분", "주요 프로그램", "운영 장소", "담당 부서"],
        "rows": [
            ["체험", "생활안전 실습", "문화광장", "안전정책과"],
            ["교육", "재난대응 교육", "시민회관", "교육지원과"],
        ],
    },
    "attachments": ["행사 운영계획 1부", "기관별 협조사항 1부"],
    "checklist_items": [
        {
            "group": "사전 준비",
            "text": "행사장 시설 및 위험요소 점검",
            "owner": "안전정책과",
            "status": "완료",
        },
        {
            "group": "사전 준비",
            "text": "기관별 담당자와 비상연락망 확인",
            "owner": "안전기획팀",
            "status": "완료",
        },
        {
            "group": "운영 준비",
            "text": "체험 장비 작동 상태와 수량 확인",
            "owner": "시설관리부",
            "status": "진행",
        },
        {
            "group": "운영 준비",
            "text": "교육 자료와 참가자 안내문 배치",
            "owner": "교육지원과",
            "status": "완료",
        },
        {
            "group": "현장 운영",
            "text": "출입 동선과 안전 표지 설치",
            "owner": "현장운영반",
            "status": "예정",
        },
        {
            "group": "현장 운영",
            "text": "응급 의료지원 위치와 연락처 게시",
            "owner": "안전지원반",
            "status": "예정",
        },
        {
            "group": "사후 관리",
            "text": "프로그램별 참여 인원 취합",
            "owner": "운영지원반",
            "status": "예정",
        },
        {
            "group": "사후 관리",
            "text": "운영 결과 및 개선 의견 제출",
            "owner": "안전정책과",
            "status": "예정",
        },
    ],
    "issuer_title": "한 빛 시 장",
    "copy_recipients": "각 부서장, 한빛시설관리공단 이사장",
    "signers": [
        {"role": "주무관", "name": "김하늘", "date": ""},
        {"role": "안전기획팀장", "name": "박도윤", "date": ""},
        {"role": "안전정책과장", "name": "이서진", "date": "07/28"},
    ],
    "document_number": "안전정책과-4821",
    "issue_date": "2026. 7. 28.",
    "postal_code": "12345",
    "address": "한빛시 중앙대로 88 (가온동)",
    "website": "www.hanbit.go.kr",
    "phone": "02-1234-5601",
    "fax": "02-1234-5679",
    "email": "safety@hanbit.go.kr",
    "venue": "한빛문화광장 및 시민회관",
    "disclosure": "대국민 공개",
    "footer_note": "",
    "document_kind_label": "운영계획 보고",
    "document_kicker": "PROGRAM OPERATION REPORT",
    "details_heading": "행사 정보",
    "table_heading": "프로그램 운영 구성",
    "secondary_heading": "세부 추진사항",
    "form_label": "현장 확인 서식",
    "form_subtitle": "SAFETY OPERATION CHECKLIST",
    "guide_text": (
        "항목별 조치 상태를 확인하고 해당 칸에 표시한 후 "
        "담당자 서명을 완료합니다."
    ),
    "summary_text": (
        "사전 점검 항목은 계획에 따라 이행 중이며, 현장 운영 항목은 "
        "행사 개시 전 최종 확인이 필요함."
    ),
}

_VARIANTS = (
    {
        "slug": "01_classic_municipal",
        "template": "official_variants/classic_municipal.html",
    },
    {
        "slug": "02_fire_station",
        "template": "official_variants/fire_station.html",
    },
    {
        "slug": "03_internal_approval",
        "template": "official_variants/internal_approval.html",
    },
    {
        "slug": "04_personnel_notice",
        "template": "official_variants/personnel_notice.html",
    },
    {
        "slug": "05_field_report",
        "template": "official_variants/field_report.html",
    },
    {
        "slug": "06_modern_public",
        "template": "official_variants/modern_public.html",
    },
    {
        "slug": "07_monochrome_hwp",
        "template": "official_variants/monochrome_hwp.html",
    },
    {
        "slug": "08_table_first_report",
        "template": "official_variants/table_first_report.html",
    },
    {
        "slug": "09_long_form",
        "template": "official_variants/long_form.html",
    },
    {
        "slug": "10_checklist_form",
        "template": "official_variants/checklist_form.html",
    },
)


def generate_previews(output_dir: Path) -> list[dict[str, str]]:
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(("html", "xml")),
        undefined=StrictUndefined,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, str]] = []

    for variant in _VARIANTS:
        template = environment.get_template(variant["template"])
        html = template.render(**_SHARED_CONTEXT)
        html_path = output_dir / f"{variant['slug']}.html"
        pdf_path = output_dir / f"{variant['slug']}.pdf"
        html_path.write_text(html, encoding="utf-8")
        HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf(pdf_path)
        manifest.append(
            {
                "slug": variant["slug"],
                "template": variant["template"],
                "html": str(html_path),
                "pdf": str(pdf_path),
            }
        )
        print(f"[ok] {variant['slug']} -> {pdf_path}")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="미리보기 HTML·PDF를 쓸 디렉터리 (예: output/pdf/official_template_previews)",
    )
    args = parser.parse_args()
    generate_previews(args.output_dir)


if __name__ == "__main__":
    main()
