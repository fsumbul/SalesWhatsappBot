"""Unit tests for the agent-builder's LLM response parser — pure, no LLM
or DB needed. Real LLM output can be malformed (ignores instructions,
adds commentary, wraps in markdown, ...), so this needs to be strict."""

from __future__ import annotations

import pytest

from src.modules.agents.builder import BuilderResponseParseError, parse_llm_response


class TestParseLlmResponse:
    def test_parses_minimal_valid_response(self) -> None:
        result = parse_llm_response('{"reply": "Hi there!"}')
        assert result.reply == "Hi there!"
        assert result.draft_patch == {}
        assert result.ready_to_promote is False

    def test_parses_full_response(self) -> None:
        raw = """
        {"reply": "Got it, what languages should the agent speak?",
         "draft_patch": {"persona": "Friendly and concise", "tone": "friendly"},
         "ready_to_promote": false}
        """
        result = parse_llm_response(raw)
        assert result.draft_patch == {"persona": "Friendly and concise", "tone": "friendly"}
        assert result.ready_to_promote is False

    def test_strips_json_code_fence(self) -> None:
        raw = '```json\n{"reply": "hi", "draft_patch": null, "ready_to_promote": false}\n```'
        result = parse_llm_response(raw)
        assert result.reply == "hi"

    def test_strips_bare_code_fence(self) -> None:
        raw = '```\n{"reply": "hi"}\n```'
        result = parse_llm_response(raw)
        assert result.reply == "hi"

    def test_null_draft_patch_becomes_empty_dict(self) -> None:
        result = parse_llm_response('{"reply": "hi", "draft_patch": null}')
        assert result.draft_patch == {}

    def test_ready_to_promote_true(self) -> None:
        result = parse_llm_response('{"reply": "All set!", "ready_to_promote": true}')
        assert result.ready_to_promote is True

    def test_missing_reply_field_raises(self) -> None:
        with pytest.raises(BuilderResponseParseError, match="reply"):
            parse_llm_response('{"draft_patch": {}}')

    def test_reply_wrong_type_raises(self) -> None:
        with pytest.raises(BuilderResponseParseError, match="reply"):
            parse_llm_response('{"reply": 123}')

    def test_unknown_patch_field_raises(self) -> None:
        with pytest.raises(BuilderResponseParseError, match="unknown"):
            parse_llm_response('{"reply": "hi", "draft_patch": {"not_a_real_field": "x"}}')

    def test_draft_patch_wrong_type_raises(self) -> None:
        with pytest.raises(BuilderResponseParseError, match="draft_patch"):
            parse_llm_response('{"reply": "hi", "draft_patch": "not an object"}')

    def test_ready_to_promote_wrong_type_raises(self) -> None:
        with pytest.raises(BuilderResponseParseError, match="ready_to_promote"):
            parse_llm_response('{"reply": "hi", "ready_to_promote": "yes"}')

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(BuilderResponseParseError, match="not valid JSON"):
            parse_llm_response("this is not json at all")

    def test_json_array_instead_of_object_raises(self) -> None:
        with pytest.raises(BuilderResponseParseError, match="JSON object"):
            parse_llm_response('["reply", "hi"]')

    def test_valid_patch_field_names_accepted(self) -> None:
        raw = """{"reply": "ok", "draft_patch": {
            "persona": "x", "tone": "y", "languages": ["tr", "en"],
            "product_knowledge": "z", "qualification_questions": ["q1"],
            "guardrails": {"forbidden_topics": ["pricing"]},
            "reply_policies": {"max_response_length": 300}
        }}"""
        result = parse_llm_response(raw)
        assert set(result.draft_patch.keys()) == {
            "persona", "tone", "languages", "product_knowledge",
            "qualification_questions", "guardrails", "reply_policies",
        }
