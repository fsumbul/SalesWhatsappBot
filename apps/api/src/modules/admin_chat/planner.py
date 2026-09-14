# ruff: noqa: RUF001
"""Model selects one bounded intent, never identities, SQL, or response prose."""

import asyncio
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.integrations.llm import LLMMessage, get_llm_client
from src.modules.selection.engine import normalize


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal[
        "workspace",
        "search",
        "analytics",
        "quotes",
        "summary",
        "missing",
        "files",
        "conversation",
        "delivery",
        "status",
        "assign",
        "note",
        "clarify",
        "outreach",
        "send_outreach",
        "cancel_outreach",
        "outreach_status",
        "capacity",
        "templates",
    ]
    target: str | None = Field(default=None, max_length=120)
    status: Literal["waiting_review", "in_review", "completed", "cancelled"] | None = None
    assignee: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, min_length=1, max_length=2000)
    today: bool = False
    recipients: list[str] = Field(default_factory=list, max_length=100)
    purpose: str | None = Field(default=None, max_length=1000)

    operation: (
        Literal[
            "agents",
            "knowledge",
            "configure",
            "test",
            "versions",
            "publish",
            "team",
            "invite",
            "platform",
            "create_company",
            "create_agent",
            "inbox",
            "confirm",
            "cancel",
            "select_agent",
        ]
        | None
    ) = None
    instruction: str | None = Field(default=None, max_length=4000)
    name: str | None = Field(default=None, max_length=160)
    slug: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=254)
    role: Literal["tenant_owner", "sales_manager", "sales_agent", "viewer"] | None = None
    # Only server-generated guided envelopes may carry an operation token.
    operation_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def validate_arguments(self) -> Any:
        workspace_args = (
            self.operation,
            self.instruction,
            self.name,
            self.slug,
            self.email,
            self.role,
            self.operation_id,
        )
        if self.tool != "workspace" and any(workspace_args):
            raise ValueError("Unexpected workspace arguments")
        if self.tool == "workspace":
            if not self.operation:
                raise ValueError("Workspace operation required")
            if self.instruction and self.operation not in {"configure", "test"}:
                raise ValueError("Unexpected instruction")
            if (self.name or self.slug) and self.operation not in {
                "create_company",
                "create_agent",
            }:
                raise ValueError("Unexpected name or slug")
            if self.email and self.operation not in {"invite", "create_company"}:
                raise ValueError("Unexpected email")
            if self.role and self.operation != "invite":
                raise ValueError("Unexpected role")
            if self.operation_id and self.operation not in {"confirm", "cancel", "select_agent"}:
                raise ValueError("Unexpected operation token")
        if self.tool == "status" and not self.status:
            raise ValueError("Status required")
        if self.tool == "assign" and not self.assignee:
            raise ValueError("Assignee required")
        if self.tool == "note" and not self.note:
            raise ValueError("Note required")
        if self.note and self.tool != "note":
            raise ValueError("Unexpected note")
        if self.assignee and self.tool != "assign":
            raise ValueError("Unexpected assignee")
        if self.status and self.tool not in {"search", "status", "analytics", "quotes"}:
            raise ValueError("Unexpected status")
        if (self.recipients or self.purpose) and self.tool != "outreach":
            raise ValueError("Unexpected outreach arguments")
        return self


def fast_intent(text: str) -> Intent | None:
    match = re.fullmatch(r"workspace:(confirm|cancel|select_agent):([0-9a-f-]{36})", text)
    if match:
        from uuid import UUID

        return Intent(tool="workspace", operation=match[1], operation_id=str(UUID(match[2])))
    n = normalize(text).strip(" .!?")
    workspace_actions = {
        "asistanlar": "agents",
        "sirket bilgileri": "knowledge",
        "bilgi ekle": "configure",
        "musteri testi": "test",
        "surumler": "versions",
        "taslagi yayinla": "publish",
        "ekip ve yetkiler": "team",
        "sirketler": "platform",
        "gelen kutusu": "inbox",
    }
    if n in workspace_actions:
        return Intent(tool="workspace", operation=workspace_actions[n])
    for phrase, tool in {
        "whatsapp limiti": "capacity",
        "onayli sablonlar": "templates",
        "tanitimi gonder": "send_outreach",
        "gonderimi iptal et": "cancel_outreach",
        "gonderim durumu": "outreach_status",
    }.items():
        if n == phrase:
            return Intent(tool=tool)
    if n in {"merhaba", "yardim", "ne yapabilirsin", "ic not ekle", "sorumlu ata"}:
        return Intent(tool="clarify")
    if n in {
        "analiz",
        "analizler",
        "analizlere bak",
        "talepleri analiz et",
        "genel durum",
        "bugunku talepleri analiz et",
    }:
        return Intent(tool="analytics", today="bugun" in n)
    if n in {
        "teklifler",
        "tekliflere bak",
        "teklifleri incele",
        "teklif taleplerine bak",
        "teklif taleplerini goster",
    }:
        return Intent(tool="quotes")
    if n in {
        "talepler",
        "talepleri goster",
        "bekleyen talepler",
        "bekleyen talepleri goster",
        "bugun bekleyen talepleri goster",
        "bugunku talepler",
        "bugun gelen talepleri goster",
    }:
        return Intent(
            tool="search",
            status="waiting_review" if "bekleyen" in n else None,
            today="bugun" in n,
        )
    if n in {"bunu incelemeye al", "incelemeye al"}:
        return Intent(tool="status", status="in_review")
    if n in {"bunu tamamla", "talebi tamamla"}:
        return Intent(tool="status", status="completed")
    if n in {"bunu iptal et", "talebi iptal et"}:
        return Intent(tool="status", status="cancelled")
    for tool, phrases in {
        "summary": {"ozet", "ozetle", "talebi ozetle", "bunu goster"},
        "missing": {
            "ne eksik",
            "bu talepte ne eksik",
            "eksik bilgiler",
            "eksik olculer",
        },
        "files": {"dosyalar", "dosyalari goster"},
        "conversation": {"konusmayi goster", "musteri konusmasi"},
        "delivery": {"son mesaj ulasti mi", "son mesaj durumu", "teslimat durumu"},
    }.items():
        if n in phrases:
            return Intent(tool=tool)
    if n.startswith("ic not:") or n.startswith("not ekle:"):
        note = text.split(":", 1)[1].strip()
        return Intent(tool="note", note=note) if note else Intent(tool="clarify")
    match = re.fullmatch(r"(.+?)(?:'|’)?(?:ya|ye|a|e) ata", n)
    if match:
        # Preserve the user's exact identity text; never accept a model supplied ID.
        return Intent(tool="assign", assignee=match[1].strip("'’ "))
    match = re.fullmatch(r"(\d{4,15})(?:'|’)?(?:in|nin|un|nun)? talebinde ne eksik", n)
    if match:
        return Intent(tool="missing", target=match[1])
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f-]{27})?", n):
        return Intent(tool="summary", target=n)
    return None


SYSTEM = """You select a single administrative tool intent from the supplied JSON schema.
The input is only the authenticated administrator's current message. No customer data is supplied.
Understand natural Turkish. target is a customer name/phone/request reference COPIED from that message;
null means the current selected request. Never invent IDs, users, tenant, revision or permissions.
Allowed: search requests; analytics (request counts/statuses and unassigned workload);
quotes (customer-confirmed technical quote requests, no prices or priced quotation records); summary; missing fields; files; conversation; delivery;
status (waiting_review/in_review/completed/cancelled); assign to an explicitly named teammate;
note containing only the administrator's literal note. today means today's requests.
WhatsApp tools: capacity (actual Meta limit and local quota); templates (Meta-approved marketing text templates);
outreach prepares a durable message preview for one or up to 100 numbers. Copy each recipient EXACTLY from the message,
including + or 00 country prefix. purpose is the literal request text copied from the operator message.
A request to send to NEW numbers always uses outreach to first show the selected template and recipients.
send_outreach sends the CURRENT prepared preview, only when explicitly instructed to send it with no new numbers;
cancel_outreach cancels the current prepared/queued send; outreach_status checks its recipient results.
Never claim sent: the execution tool reports actual states. Never infer consent or generate message text.
Workspace tools use tool=workspace with one operation:
agents/knowledge show assistants/company information and JSON/CSV import controls;
configure prepares company knowledge changes using instruction copied literally from the message;
test runs a customer message through the selected agent (instruction is the literal customer message, never send to WhatsApp);
versions shows versions and rollback controls; publish prepares the current draft for publication;
team shows teammates and role controls; invite prepares an invitation (literal email, optional role);
platform lists companies; create_company prepares a company (literal name, slug and owner email);
create_agent prepares an assistant (literal name, slug); inbox shows customer conversations with reply and bot resume controls.
confirm applies the CURRENT workspace preview only on explicit approval; cancel discards that preview.
Missing required values open an inline form, never invent them. target for workspace is a literal assistant name/code;
null uses the server-selected assistant or asks the user to select when ambiguous.
Never populate operation_id from natural language. select_agent is reserved for guided UI actions.
Do not confuse company configuration with a customer inquiry or agent test.
No pricing approval, shell, SQL or secrets tools exist.
Reading delivery/read receipts IS supported: delivery checks whether the last existing outbound
message was sent, delivered or read; it does NOT send anything. Never classify a read-receipt
question as sending. summary/missing/files/conversation/delivery are read-only tools.
Only populate status for search/status/analytics/quotes, assignee for assign, and note for add-note requests.
Never put explanations or assistant prose in note. Use clarify with all optional fields null
for unsupported, ambiguous or multiple actions. Pronouns like this/customer/bu/bunun are target null.
Examples:
"Taleplerin genel durumunu analiz eder misin?" -> {"tool":"analytics"}
"Tekliflere bakabilir miyiz?" -> {"tool":"quotes"}
"Bu müşteriye gönderdiğimiz son mesaj okunmuş mu?" -> {"tool":"delivery"}
"Son yazışmaları getirir misin?" -> {"tool":"conversation"}
"7775 numaralı müşteride hangi ölçü eksik?" -> {"tool":"missing","target":"7775"}
"Lütfen bunu incelemeye alır mısın?" -> {"tool":"status","status":"in_review"}
"+905551112233 numarasına tanıtım gönder" -> {"tool":"outreach","recipients":["+905551112233"],"purpose":"tanıtım gönder"}
"Hazırladığın tanıtımı gönder" -> {"tool":"send_outreach"}
"Meta mesaj limitimiz ne kadar?" -> {"tool":"capacity"}
Output schema JSON only.
"""


async def plan(text: str, *, pending_operation: str | None = None) -> tuple[Intent, str]:
    # Only explicit UI action envelopes bypass language understanding.
    # Natural-language messages must exercise the configured model.
    if text.startswith("action:"):
        fast = fast_intent(text.removeprefix("action:"))
        if fast:
            return fast, "guided"
    raw = await asyncio.wait_for(
        get_llm_client().complete(
            [LLMMessage(role="user", content=text)],
            system=SYSTEM
            + (
                "\nThe current server-owned workspace preview operation is: " + pending_operation
                if pending_operation
                in {"invite", "create_company", "create_agent", "accept_config", "publish"}
                else "\nThere is no workspace preview to confirm."
            ),
            max_tokens=2200,
            response_schema=Intent.model_json_schema(),
        ),
        timeout=45,
    )
    intent = Intent.model_validate_json(raw)
    # A model can only point at literal text from this administrator turn.
    if intent.operation_id or intent.operation == "select_agent":
        raise ValueError("Model cannot supply operation tokens")
    for value in (
        intent.target,
        intent.assignee,
        intent.note,
        intent.purpose,
        intent.instruction,
        intent.name,
        intent.slug,
        intent.email,
        *intent.recipients,
    ):
        if value and normalize(value) not in normalize(text):
            raise ValueError("Ungrounded argument")
    if intent.role:
        words = {
            "tenant_owner": ("sirket sahibi", "sahip", "tenant_owner"),
            "sales_manager": ("yonetici", "sales_manager"),
            "sales_agent": ("temsilci", "sales_agent"),
            "viewer": ("izleyici", "viewer"),
        }[intent.role]
        if not any(word in normalize(text) for word in words):
            raise ValueError("Ungrounded invitation role")
    # Model classification is not itself authorization to mutate a selected request.
    # Require an explicit operation phrase in the administrator's own message.
    n = normalize(text)
    if intent.tool == "workspace" and intent.operation in {"confirm", "cancel"}:
        phrases = (
            ("onayla", "uygula", "kabul et", "yayinla", "olustur", "davet et")
            if intent.operation == "confirm"
            else ("iptal et", "vazgec", "reddet")
        )
        if (
            not any(p in n for p in phrases)
            or re.search(
                r"\b(?:onaylama|uygulama|kabul etme|yayinlama|olusturma|davet etme|iptal etme|reddetme)\w*\b",
                n,
            )
            or any(p in n for p in ("nasil ", "ne zaman "))
        ):
            return Intent(tool="clarify"), "model_rejected_write"
    write_phrases: dict[str, Any] = {
        "status": {
            "in_review": ("incelemeye al", "incelemede yap", "in_review"),
            "waiting_review": ("beklemeye al", "bekleyen yap", "waiting_review"),
            "completed": ("tamamla", "tamamlandi yap", "completed"),
            "cancelled": ("iptal et", "iptal yap", "cancelled"),
        },
        "send_outreach": ("gonder", "yolla", "ilet"),
        "cancel_outreach": ("iptal et", "durdur"),
        "assign": ("ata", "sorumlu yap"),
        "note": ("not ekle", "not dus", "not yaz", "ic not"),
    }
    if intent.tool in write_phrases:
        phrases = write_phrases[intent.tool]
        if intent.tool == "status":
            phrases = phrases[intent.status]
        negated_write = re.search(
            r"\b(?:gonderme|yollama|iletme|durdurma|alma|alinma|tamamlama|tamamlanma|atama|atanma|yapma|etme|ekleme|dusme|yazma)\w*\b",
            n,
        )
        if (
            not any(phrase in n for phrase in phrases)
            or negated_write
            or any(phrase in n for phrase in ("nasil ", "ne zaman "))
        ):
            return Intent(tool="clarify"), "model_rejected_write"
    return intent, "model"
