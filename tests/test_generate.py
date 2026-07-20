import json

import pytest

from rd2.generators import generate
from rd2.generators.clause_data import CLAUSES, ClauseDefinition
from rd2.schema.models import CsoClassification, DisclosureStatus


def test_every_non_on_hold_clause_has_scenario_prompts():
    """CLAUSES 데이터 무결성: on_hold가 아닌 조항은 시나리오가 최소 1개 있어야
    generate_clause_document가 실제로 동작할 수 있다."""
    for clause_no, clause in CLAUSES.items():
        assert clause.clause_no == clause_no
        if not clause.on_hold:
            assert clause.scenario_prompts, f"clause {clause_no} has no scenario prompts"


def test_on_hold_clause_is_marked_and_has_no_prompts():
    assert CLAUSES["기타"].on_hold is True
    assert CLAUSES["기타"].scenario_prompts == []


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **kwargs):
        class _Response:
            choices = [_FakeChoice(self._content)]

        return _Response()


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class FakeOpenAIClient:
    """OpenAI SDK가 노출하는 client.chat.completions.create(...).choices[0].message.content
    경로만 흉내내는 테스트용 더블 — 실제 API 호출 없이 generate_clause_document를 검증한다."""

    def __init__(self, content: str = "가상의 공공기관 문서 본문") -> None:
        self.chat = _FakeChat(content)


def test_generate_clause_document_builds_expected_document():
    doc = generate.generate_clause_document(
        "1",
        ordering_agency="실제기관명",
        production_date="2025-03-01",
        client=FakeOpenAIClient("테스트용 합성 본문"),
        scenario_index=0,
    )
    assert doc.body_text == "테스트용 합성 본문"
    assert doc.cso_classification == CsoClassification.C
    assert doc.cso_sub_clause == "1"
    assert doc.source == "synthetic-llm"
    assert doc.source_url is None
    assert doc.is_synthetic is True
    assert doc.disclosure_status == DisclosureStatus.CLOSED
    assert doc.non_disclosure_reason is not None
    assert doc.title.startswith("[합성]")
    # R3(2026-07-20): 기관명/생산일자는 실제 값을 그대로 보존해야 한다 —
    # "가상기관(합성)" 같은 가짜 이름으로 되돌아가면 안 된다.
    assert doc.ordering_agency == "실제기관명"
    assert str(doc.production_date) == "2025-03-01"


def test_build_user_prompt_marks_agency_and_date_as_real_not_invented():
    clause = CLAUSES["1"]
    prompt = generate._build_user_prompt(
        clause, "테스트 시나리오", ordering_agency="실제기관명", production_date="2025-03-01"
    )
    assert "실제기관명" in prompt
    assert "2025-03-01" in prompt
    assert "그대로 쓰고 바꾸지 마라" in prompt
    # 인명/전화번호/금액은 여전히 가상으로 지어내라는 지시가 남아있어야 한다.
    assert "가상으로 지어내라" in prompt


def test_system_prompt_no_longer_forbids_naming_real_agency():
    # R3(2026-07-20): "실제 기관을 지칭하지 마라"던 이전 지시를 뒤집어, 실제
    # 기관명을 그대로 보존하라고 지시해야 한다 — "가상기관" 같은 표현이
    # 시스템 프롬프트에 남아있으면 회귀다.
    assert "가상기관" not in generate._SYSTEM_PROMPT
    assert "그대로 쓰고" in generate._SYSTEM_PROMPT


def test_generate_clause_document_rejects_on_hold_clause():
    with pytest.raises(ValueError, match="on hold"):
        generate.generate_clause_document(
            "기타", ordering_agency="실제기관명", production_date="2025-03-01",
            client=FakeOpenAIClient(),
        )


def test_default_client_raises_clear_error_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        generate._default_client()


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeSeededOpenAIClient:
    """generate_seeded_body가 쓰는 json 응답 + usage 경로를 흉내내는 더블."""

    def __init__(self, parsed: dict, tokens_in: int = 100, tokens_out: int = 50) -> None:
        self._content = json.dumps(parsed, ensure_ascii=False)
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self.last_kwargs: dict | None = None
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.last_kwargs = kwargs

        class _Response:
            choices = [_FakeChoice(self._content)]
            usage = _FakeUsage(self._tokens_in, self._tokens_out)

        return _Response()


def test_generate_seeded_body_preserves_real_seed_and_parses_json():
    seed = generate.SeedInput(
        ordering_agency="실제기관명",
        clause_no="5",
        classification="S",
        non_disclosure_reason="제5호 — 감사·검사·입찰계약 등 내부검토",
        content_summary="실제 초록 텍스트",
    )
    parsed = {
        "department": "감사담당관실",
        "unit_task": "내부감사 계획 수립",
        "production_date": "2026-03-01",
        "body_text": "실제기관명의 내부 감사 관련 문서 본문",
    }
    client = FakeSeededOpenAIClient(parsed)

    result = generate.generate_seeded_body(seed, client=client)

    assert result.department == "감사담당관실"
    assert result.unit_task == "내부감사 계획 수립"
    assert result.production_date == "2026-03-01"
    assert result.body_text == "실제기관명의 내부 감사 관련 문서 본문"
    assert result.tokens_in == 100
    assert result.tokens_out == 50
    # 시드의 실제 필드(기관명 등)가 프롬프트에 그대로 들어갔는지 확인 —
    # _SYSTEM_PROMPT(완전 가상 생성용)가 아니라 _SYSTEM_PROMPT_SEEDED가 쓰였는지도 확인.
    sent_messages = client.last_kwargs["messages"]
    assert sent_messages[0]["content"] == generate._SYSTEM_PROMPT_SEEDED
    assert "실제기관명" in sent_messages[1]["content"]
    assert "실제 초록 텍스트" in sent_messages[1]["content"]
    assert client.last_kwargs["response_format"]["type"] == "json_schema"


def test_generate_anchored_body_uses_anchor_prompt_and_preserves_seed():
    seed = generate.SeedInput(
        ordering_agency="실제기관명",
        clause_no="5",
        classification="S",
        non_disclosure_reason="제5호 — 감사·검사·입찰계약 등 내부검토",
        content_summary="실제 초록 텍스트",
    )
    anchor = generate.AnchorInput(seed=seed, anchor_body_text="실제 공개문서 형식 참고 전문입니다")
    parsed = {
        "department": "감사담당관실",
        "unit_task": "내부감사 계획 수립",
        "production_date": "2026-03-01",
        "body_text": "생성된 본문",
    }
    client = FakeSeededOpenAIClient(parsed)

    result = generate.generate_anchored_body(anchor, client=client)

    assert result.body_text == "생성된 본문"
    sent_messages = client.last_kwargs["messages"]
    # _SYSTEM_PROMPT_SEEDED가 아니라 앵커 전용 프롬프트(참고자료만 쓰라는 지시 포함)여야 함.
    assert sent_messages[0]["content"] == generate._SYSTEM_PROMPT_ANCHORED
    assert "구체적 사실" in sent_messages[0]["content"]
    assert "실제기관명" in sent_messages[1]["content"]
    assert "실제 초록 텍스트" in sent_messages[1]["content"]
    assert "실제 공개문서 형식 참고 전문입니다" in sent_messages[1]["content"]


def test_build_anchored_user_prompt_truncates_long_anchor_text():
    seed = generate.SeedInput(
        ordering_agency="기관",
        clause_no="1",
        classification="C",
        non_disclosure_reason="사유",
        content_summary="요약",
    )
    long_text = "가" * (generate._MAX_ANCHOR_TEXT_CHARS + 500)
    anchor = generate.AnchorInput(seed=seed, anchor_body_text=long_text)

    prompt = generate._build_anchored_user_prompt(anchor)

    assert "가" * generate._MAX_ANCHOR_TEXT_CHARS in prompt
    assert "가" * (generate._MAX_ANCHOR_TEXT_CHARS + 1) not in prompt


class TestTemplateConstraintLines:
    def test_none_template_returns_empty_string(self):
        assert generate._template_constraint_lines(None) == ""

    def test_pending_template_mentions_incomplete_approval_and_forbidden_phrases(self):
        from rd2.generators.doc_templates import TEMPLATES
        from rd2.storage.naming import DOC_TYPE_APPROVAL

        spec = TEMPLATES[("5", DOC_TYPE_APPROVAL)]  # T5-1, approval_state=PENDING
        lines = generate._template_constraint_lines(spec)

        assert "T5-1" in lines
        assert "결재가 완료되지 않은 내부검토" in lines
        assert "결재 완료" in lines  # forbidden phrase 포함 확인

    def test_complete_template_mentions_finalized_approval(self):
        from rd2.generators.doc_templates import TEMPLATES
        from rd2.storage.naming import DOC_TYPE_PERSONNEL

        spec = TEMPLATES[("6", DOC_TYPE_PERSONNEL)]  # T6-1, approval_state=COMPLETE
        lines = generate._template_constraint_lines(spec)

        assert "결재가 완료된 확정 문서" in lines


class TestGenerateSpanSeededBody:
    def test_preserves_real_agency_and_uses_document_text_as_evidence(self):
        seed = generate.SpanSeedInput(
            ordering_agency="고용노동부",
            clause_no="5",
            classification="S",
            doc_type="notification",
            source_document_text="문서 원문 전체 내용입니다. 감사 관련 내부검토가 진행 중입니다.",
            matched_span_text="감사 관련 내부검토",
        )
        parsed = {
            "department": "감사담당관실",
            "unit_task": "내부감사 계획 수립",
            "production_date": "2026-03-01",
            "body_text": "고용노동부의 내부 감사 관련 문서 본문",
        }
        client = FakeSeededOpenAIClient(parsed)

        result = generate.generate_span_seeded_body(seed, client=client)

        assert result.body_text == "고용노동부의 내부 감사 관련 문서 본문"
        assert result.department == "감사담당관실"
        sent_messages = client.last_kwargs["messages"]
        assert sent_messages[0]["content"] == generate._SYSTEM_PROMPT_SPAN_SEEDED
        assert "원문에 없는 사실을 지어내지 마라" in sent_messages[0]["content"]
        assert "고용노동부" in sent_messages[1]["content"]
        assert "문서 원문 전체 내용입니다" in sent_messages[1]["content"]
        assert "감사 관련 내부검토" in sent_messages[1]["content"]
        assert client.last_kwargs["response_format"]["type"] == "json_schema"

    def test_injects_template_constraints_when_template_provided(self):
        from rd2.generators.doc_templates import TEMPLATES
        from rd2.storage.naming import DOC_TYPE_APPROVAL

        spec = TEMPLATES[("5", DOC_TYPE_APPROVAL)]
        seed = generate.SpanSeedInput(
            ordering_agency="고용노동부",
            clause_no="5",
            classification="S",
            doc_type=DOC_TYPE_APPROVAL,
            source_document_text="내부 검토 문서 원문",
            matched_span_text="내부검토",
            template=spec,
        )
        prompt = generate._build_span_seeded_user_prompt(seed)
        assert "T5-1" in prompt
        assert "결재 완료" in prompt

    def test_no_template_omits_constraint_section(self):
        seed = generate.SpanSeedInput(
            ordering_agency="고용노동부",
            clause_no="5",
            classification="S",
            doc_type="notification",
            source_document_text="문서 원문",
            matched_span_text="근거",
        )
        prompt = generate._build_span_seeded_user_prompt(seed)
        assert "템플릿" not in prompt

    def test_truncates_long_source_document_text(self):
        long_text = "나" * (generate._MAX_ANCHOR_TEXT_CHARS + 500)
        seed = generate.SpanSeedInput(
            ordering_agency="고용노동부",
            clause_no="5",
            classification="S",
            doc_type="notification",
            source_document_text=long_text,
            matched_span_text="근거",
        )
        prompt = generate._build_span_seeded_user_prompt(seed)
        assert "나" * generate._MAX_ANCHOR_TEXT_CHARS in prompt
        assert "나" * (generate._MAX_ANCHOR_TEXT_CHARS + 1) not in prompt


class TestSeedInputDepartmentPrompt:
    """PRISM 실데이터에 department가 있으면 LLM이 지어내지 않고 그대로 쓰도록
    프롬프트에 반영돼야 한다 — 2026-07-14 department 버그 수정."""

    def test_department_present_is_included_and_preserved_in_prompt(self):
        seed = generate.SeedInput(
            ordering_agency="실제기관명",
            clause_no="5",
            classification="S",
            non_disclosure_reason="사유",
            content_summary="요약",
            department="감사담당관실",
        )
        prompt = generate._build_seeded_user_prompt(seed)
        assert "담당부서: 감사담당관실" in prompt
        assert "담당부서도 위 값을 그대로 쓰고" in prompt

    def test_department_absent_is_not_mentioned_in_prompt(self):
        seed = generate.SeedInput(
            ordering_agency="실제기관명",
            clause_no="5",
            classification="S",
            non_disclosure_reason="사유",
            content_summary="요약",
        )
        prompt = generate._build_seeded_user_prompt(seed)
        assert "담당부서" not in prompt


def test_generate_clause_document_rejects_clause_without_scenarios(monkeypatch):
    empty_clause = ClauseDefinition(
        clause_no="9",
        classification=CsoClassification.S,
        title="테스트용 빈 조항",
        description="시나리오가 없는 임시 조항",
        scenario_prompts=[],
        on_hold=False,
    )
    monkeypatch.setitem(generate.CLAUSES, "9", empty_clause)

    with pytest.raises(ValueError, match="no scenario prompts"):
        generate.generate_clause_document(
            "9", ordering_agency="실제기관명", production_date="2025-03-01",
            client=FakeOpenAIClient(),
        )
