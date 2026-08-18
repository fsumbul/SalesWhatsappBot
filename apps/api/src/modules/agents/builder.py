"""The agent-builder bot's LLM interaction contract: the system prompt that
tells the model what to do and how to respond, and a strict parser for its
output. Kept separate from service.py because this is "what we ask the LLM
for and how we validate what it gives back" — a distinct concern from
session/draft orchestration, and one that's fully testable without a real
LLM call (the parser is a pure function; canned strings exercise it same
as a real response would).

Expected LLM output, every turn, no exceptions: a single JSON object,
optionally fenced in ```json ... ```:

    {
      "reply": "<text to show the tenant>",
      "draft_patch": {<zero or more AgentVersion fields to update>} | null,
      "ready_to_promote": <bool>
    }

Free-text replies with no JSON structure are treated as a malformed
response (BuilderResponseParseError), not silently passed through — an
LLM that ignores its instructions needs to be caught, not have its raw
prose stored as if it were a validated patch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .models import AgentVersion

_PATCH_FIELDS = {
    "persona",
    "tone",
    "languages",
    "product_knowledge",
    "qualification_questions",
    "guardrails",
    "reply_policies",
}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class BuilderResponseParseError(ValueError):
    """The LLM's output didn't match the expected JSON envelope."""


@dataclass(frozen=True)
class BuilderLLMResponse:
    reply: str
    draft_patch: dict[str, Any] = field(default_factory=dict)
    ready_to_promote: bool = False


def parse_llm_response(raw: str) -> BuilderLLMResponse:
    text = _FENCE_RE.sub("", raw.strip())

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BuilderResponseParseError(f"LLM output was not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise BuilderResponseParseError("LLM output must be a JSON object")

    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        # A blank/whitespace 'reply' is worse than an outright parse error:
        # it's a syntactically valid envelope that silently produces an
        # empty chat bubble for the tenant instead of surfacing anything
        # they (or the retry-once path in builder_service.py) can react
        # to. Treat it the same as a missing field so it gets one retry
        # instead of shipping nothing to the WhatsApp-style chat UI.
        raise BuilderResponseParseError("LLM output missing a non-empty string 'reply' field")

    raw_patch = data.get("draft_patch")
    if raw_patch is None:
        patch: dict[str, Any] = {}
    elif isinstance(raw_patch, dict):
        unknown = set(raw_patch) - _PATCH_FIELDS
        if unknown:
            raise BuilderResponseParseError(f"draft_patch has unknown field(s): {sorted(unknown)}")
        patch = raw_patch
    else:
        raise BuilderResponseParseError("draft_patch must be a JSON object or null")

    ready = data.get("ready_to_promote", False)
    if isinstance(ready, str) and ready.strip().lower() in ("true", "false"):
        # A 7B-class local model (this project's default — see llm.py)
        # reliably gets the envelope shape right but not always JSON's
        # bare-word boolean syntax; it'll quote it like any other value
        # often enough that rejecting the whole turn over it would make
        # the builder flow flaky for no benefit. Still reject anything
        # that isn't recognizably a yes/no the model meant as a bool.
        ready = ready.strip().lower() == "true"
    if not isinstance(ready, bool):
        raise BuilderResponseParseError("ready_to_promote must be a boolean")

    return BuilderLLMResponse(reply=reply, draft_patch=patch, ready_to_promote=ready)


def build_system_prompt(agent_name: str, draft: AgentVersion) -> str:
    """Describes the task to the LLM and includes the current draft state
    so it doesn't re-ask questions the tenant already answered."""
    current_state = json.dumps(
        {
            "persona": draft.persona,
            "tone": draft.tone,
            "languages": draft.languages,
            "product_knowledge": draft.product_knowledge,
            "qualification_questions": draft.qualification_questions,
            "guardrails": draft.guardrails,
            "reply_policies": draft.reply_policies,
        },
        ensure_ascii=False,
    )
    return f"""You are helping a business owner configure "{agent_name}", a WhatsApp \
sales agent for their company, through a conversation. Ask short, concrete \
questions to fill in whatever is still missing from the fields below. Don't \
re-ask about a field that already has a real value in the current draft.

Fields to gather, with their exact JSON types — match these types exactly, \
never substitute a list for a string or vice versa:
- persona: string (tone/personality description)
- tone: string (a short label like "friendly" or "formal")
- languages: array of strings (language codes the agent should speak)
- product_knowledge: string (a paragraph about their product/service, NOT a list)
- qualification_questions: array of strings (questions the agent should ask leads)
- guardrails: object with "forbidden_topics" (array of strings) and \
"escalation_triggers" (array of strings)
- reply_policies: object (freeform behavioral rules, e.g. {{"notes": "..."}})

Current draft state:
{current_state}

Respond with ONLY a single JSON object, no other text, in exactly this shape:
{{"reply": "<what to say to the tenant next>", \
"draft_patch": {{<only the fields you're updating this turn>}} or null, \
"ready_to_promote": <true only once every field above has a real, useful value>}}
"""
