#!/usr/bin/env python3
"""Deterministic E2E prototype for the guided admin configuration journey.

The local LLM may eventually map an owner's free-form text to a constrained
intent.  This script deliberately starts *after* that untrusted step.  It
proves the controller-side rules that must remain deterministic:

* an offering collection cannot be silently abandoned;
* the next prompt stays in the semantic neighborhood of the active collection;
* Excel/PDF extraction produces evidence-backed proposals, never direct facts;
* an owner must explicitly accept a proposal before the draft changes;
* imported facts begin as non-customer-visible; and
* duplicate delivery of the same admin event is idempotent.

It has no API, WhatsApp, cloud or LLM dependency.  It is a local behavioural
contract for the eventual admin-bot implementation.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class SessionError(ValueError):
    """An attempted configuration transition is not authorized by session state."""


class CollectionState(StrEnum):
    UNASKED = "unasked"
    COLLECTING = "collecting"
    CLOSED_EMPTY = "closed_empty"
    CLOSED_NONEMPTY = "closed_nonempty"
    DEFERRED = "deferred"


class ArtifactStatus(StrEnum):
    AWAITING_ADMIN_REVIEW = "awaiting_admin_review"
    APPLIED = "applied"
    QUARANTINED = "quarantined"


SAFE_MEDIA_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "application/pdf",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EvidenceSpan:
    artifact_id: str
    artifact_sha256: str
    locator: str
    quote: str


@dataclass(frozen=True)
class CandidatePatch:
    id: str
    operation: str
    payload: dict[str, Any]
    evidence: EvidenceSpan


@dataclass(frozen=True)
class Artifact:
    id: str
    filename: str
    media_type: str
    sha256: str
    proposals: tuple[CandidatePatch, ...]
    untrusted_excerpt: str


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    semantic_scope: str
    choices: tuple[str, ...]


QUESTIONS = {
    "organization.name": Question(
        "organization.name",
        "Şirketinizin müşterilere görünen adı nedir?",
        "organization",
        (),
    ),
    "offerings.start": Question(
        "offerings.start",
        "Ürün, hizmet veya paket eklemek ister misiniz?",
        "offerings",
        ("Ürün/hizmet ekle", "Excel/PDF yükle", "Şimdilik yok"),
    ),
    "offerings.add-item": Question(
        "offerings.add-item",
        "Eklemek istediğiniz ürün veya hizmetin adını yazın.",
        "offerings",
        (),
    ),
    "offerings.continue": Question(
        "offerings.continue",
        "Eklemek istediğiniz başka ürün, hizmet, varyant veya model var mı?",
        "offerings",
        ("Bir tane daha ekle", "Excel/PDF yükle", "Bu liste şimdilik tamam"),
    ),
    "offerings.details-or-import": Question(
        "offerings.details-or-import",
        "Ürün listeniz tamam. Fiyat ve teknik bilgileri tek tek mi ekleyelim, yoksa Excel/PDF'den taslak çıkarayım mı?",
        "offerings",
        ("Tek tek ekle", "Excel/PDF yükle", "Şimdilik geç"),
    ),
    "artifact.review": Question(
        "artifact.review",
        "Dosyadan çıkarılan değişiklikler hazır. Taslağa eklemeden önce önizlemeyi onaylamak ister misiniz?",
        "artifact",
        ("Önizlemeyi göster", "Seçilenleri uygula", "Reddet"),
    ),
    "agent.purpose": Question(
        "agent.purpose",
        "Bot müşterilere hangi konularda yardımcı olsun?",
        "agent",
        ("Bilgi", "Satış", "Destek", "Randevu"),
    ),
}


def _artifact_from_fixture(raw: dict[str, Any]) -> Artifact:
    artifact_id = str(raw["id"])
    sha256 = str(raw["sha256"])
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"artifact {artifact_id!r} needs a 64-digit sha256")
    proposals = tuple(
        CandidatePatch(
            id=str(item["id"]),
            operation=str(item["operation"]),
            payload=dict(item["payload"]),
            evidence=EvidenceSpan(
                artifact_id=artifact_id,
                artifact_sha256=sha256,
                locator=str(item["evidence"]["locator"]),
                quote=str(item["evidence"]["quote"]),
            ),
        )
        for item in raw["proposals"]
    )
    if len({proposal.id for proposal in proposals}) != len(proposals):
        raise ValueError(f"artifact {artifact_id!r} has duplicate proposal IDs")
    return Artifact(
        id=artifact_id,
        filename=str(raw["filename"]),
        media_type=str(raw["media_type"]),
        sha256=sha256,
        proposals=proposals,
        untrusted_excerpt=str(raw.get("untrusted_excerpt", "")),
    )


@dataclass
class GuidedConfigSession:
    """Controller-owned draft state; it never accepts raw arbitrary JSON paths."""

    draft: dict[str, Any] = field(
        default_factory=lambda: {
            "schema_version": "company-agent-config/1.0",
            "lifecycle": "draft",
            "organization": None,
            "offerings": [],
            "facts": [],
        }
    )
    collection_state: CollectionState = CollectionState.UNASKED
    offerings_mode: str = "start"
    focus_path: tuple[str, ...] = ("organization",)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    artifact_status: dict[str, ArtifactStatus] = field(default_factory=dict)
    applied_proposal_ids: set[str] = field(default_factory=set)
    seen_event_ids: set[str] = field(default_factory=set)
    event_log: list[str] = field(default_factory=list)

    def _consume_event(self, event_id: str) -> bool:
        """Return false for a duplicate delivery without mutating the draft."""
        if event_id in self.seen_event_ids:
            self.event_log.append(f"duplicate-event:{event_id}")
            return False
        self.seen_event_ids.add(event_id)
        return True

    def draft_json(self) -> str:
        return json.dumps(self.draft, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def next_question(self) -> Question:
        # A pending artifact is a hard semantic gate: it is closer to the
        # immediately preceding upload than any unrelated company question.
        if any(status == ArtifactStatus.AWAITING_ADMIN_REVIEW for status in self.artifact_status.values()):
            return QUESTIONS["artifact.review"]
        if self.draft["organization"] is None:
            return QUESTIONS["organization.name"]
        if self.collection_state == CollectionState.UNASKED:
            return QUESTIONS["offerings.start"]
        if self.collection_state == CollectionState.COLLECTING:
            if self.offerings_mode == "awaiting_item":
                return QUESTIONS["offerings.add-item"]
            return QUESTIONS["offerings.continue"]
        if self.collection_state in {CollectionState.CLOSED_EMPTY, CollectionState.CLOSED_NONEMPTY}:
            return QUESTIONS["offerings.details-or-import"]
        return QUESTIONS["agent.purpose"]

    def set_organization(self, *, company_id: str, display_name: str, event_id: str) -> bool:
        if not self._consume_event(event_id):
            return False
        if self.draft["organization"] is not None:
            raise SessionError("organization is already set; propose an explicit edit instead")
        self.draft["organization"] = {
            "id": company_id,
            "display_names": {"tr-TR": display_name},
        }
        self.focus_path = ("organization",)
        self.event_log.append("organization-confirmed")
        return True

    def add_offering(self, offering: dict[str, Any], *, event_id: str, origin: str) -> bool:
        if not self._consume_event(event_id):
            return False
        if self.draft["organization"] is None:
            raise SessionError("organization must be confirmed before offerings")
        offering_id = str(offering["id"])
        if any(item["id"] == offering_id for item in self.draft["offerings"]):
            raise SessionError(f"offering {offering_id!r} already exists")
        record = {
            "id": offering_id,
            "kind": str(offering["kind"]),
            "display_names": {"tr-TR": str(offering["display_name"])},
            "provider_id": self.draft["organization"]["id"],
        }
        self.draft["offerings"].append(record)
        self.collection_state = CollectionState.COLLECTING
        self.offerings_mode = "continue"
        self.focus_path = ("offerings", f"offering:{offering_id}")
        self.event_log.append(f"offering-added:{offering_id}:{origin}")
        return True

    def choose_offering_checkpoint(self, choice: str, *, event_id: str) -> bool:
        if not self._consume_event(event_id):
            return False
        if self.collection_state != CollectionState.COLLECTING:
            raise SessionError("offering checkpoint is only available while offerings are open")
        if choice == "add":
            self.offerings_mode = "awaiting_item"
            self.focus_path = ("offerings",)
        elif choice == "close":
            self.collection_state = (
                CollectionState.CLOSED_NONEMPTY if self.draft["offerings"] else CollectionState.CLOSED_EMPTY
            )
            self.offerings_mode = "closed"
            self.focus_path = ("offerings",)
        elif choice == "defer":
            self.collection_state = CollectionState.DEFERRED
            self.offerings_mode = "deferred"
            self.focus_path = ("offerings",)
        elif choice == "import":
            # The caller will stage an artifact; the collection remains open.
            self.offerings_mode = "continue"
            self.focus_path = ("offerings",)
        else:
            raise SessionError(f"unsupported offerings checkpoint choice: {choice!r}")
        self.event_log.append(f"offerings-checkpoint:{choice}")
        return True

    def stage_artifact(self, artifact: Artifact, *, event_id: str) -> bool:
        if not self._consume_event(event_id):
            return False
        if artifact.id in self.artifacts:
            raise SessionError(f"artifact {artifact.id!r} already staged")
        self.artifacts[artifact.id] = artifact
        self.artifact_status[artifact.id] = (
            ArtifactStatus.AWAITING_ADMIN_REVIEW
            if artifact.media_type in SAFE_MEDIA_TYPES
            else ArtifactStatus.QUARANTINED
        )
        self.focus_path = ("artifacts", f"artifact:{artifact.id}")
        # Critically, artifact.proposals and its arbitrary untrusted excerpt
        # are not applied or interpreted as workflow instructions here.
        self.event_log.append(f"artifact-staged:{artifact.id}")
        return True

    def accept_proposals(
        self, artifact_id: str, proposal_ids: list[str], *, event_id: str
    ) -> bool:
        if not self._consume_event(event_id):
            return False
        artifact = self.artifacts.get(artifact_id)
        if artifact is None:
            raise SessionError(f"unknown artifact {artifact_id!r}")
        if self.artifact_status.get(artifact_id) != ArtifactStatus.AWAITING_ADMIN_REVIEW:
            raise SessionError(f"artifact {artifact_id!r} is not awaiting review")
        proposals = {proposal.id: proposal for proposal in artifact.proposals}
        if not proposal_ids or not set(proposal_ids).issubset(proposals):
            raise SessionError("acceptance must select one or more proposals from this artifact")

        selected = [proposals[proposal_id] for proposal_id in proposal_ids]
        for proposal in selected:
            evidence = proposal.evidence
            if evidence.artifact_id != artifact.id or evidence.artifact_sha256 != artifact.sha256:
                raise SessionError(f"proposal {proposal.id!r} has invalid artifact provenance")
            if proposal.id in self.applied_proposal_ids:
                raise SessionError(f"proposal {proposal.id!r} has already been applied")

        # New entities must exist before facts that refer to them.  This is a
        # deterministic dependency order, not the source-file row order.
        ordered = sorted(selected, key=lambda proposal: proposal.operation != "add_offering")
        added_offering = False
        for proposal in ordered:
            if proposal.operation == "add_offering":
                self.add_offering(
                    proposal.payload,
                    event_id=f"{event_id}:apply:{proposal.id}",
                    origin=f"artifact:{artifact.id}",
                )
                added_offering = True
            elif proposal.operation == "add_fact":
                self._add_imported_fact(proposal, artifact)
            else:
                raise SessionError(f"unsupported proposed operation: {proposal.operation!r}")
            self.applied_proposal_ids.add(proposal.id)

        self.artifact_status[artifact.id] = ArtifactStatus.APPLIED
        if added_offering:
            # A file that adds offerings reopens an earlier closed catalogue.
            self.collection_state = CollectionState.COLLECTING
            self.offerings_mode = "continue"
            self.focus_path = ("offerings",)
        self.event_log.append(f"artifact-accepted:{artifact.id}:{','.join(proposal_ids)}")
        return True

    def _add_imported_fact(self, proposal: CandidatePatch, artifact: Artifact) -> None:
        payload = proposal.payload
        fact_id = str(payload["id"])
        subject_id = str(payload["subject_id"])
        if not any(offering["id"] == subject_id for offering in self.draft["offerings"]):
            raise SessionError(f"fact {fact_id!r} refers to an unknown offering {subject_id!r}")
        if any(fact["id"] == fact_id for fact in self.draft["facts"]):
            raise SessionError(f"fact {fact_id!r} already exists; show a conflict instead")
        self.draft["facts"].append(
            {
                "id": fact_id,
                "subject_id": subject_id,
                "category": str(payload["category"]),
                "value": payload["value"],
                # An imported value is useful to the admin but does not become
                # an automatic customer claim without a separate visibility
                # confirmation and ordinary config validation.
                "customer_visible": False,
                "customer_text": {"tr-TR": str(payload["customer_text"])},
                "source": (
                    f"artifact:{artifact.id}:{artifact.sha256[:12]}#"
                    f"{proposal.evidence.locator}"
                ),
            }
        )

    def assert_invariants(self) -> None:
        if self.draft["lifecycle"] != "draft":
            raise AssertionError("guided session must not publish a draft")
        offering_ids = {offering["id"] for offering in self.draft["offerings"]}
        for fact in self.draft["facts"]:
            if fact["subject_id"] not in offering_ids:
                raise AssertionError(f"fact {fact['id']!r} has a dangling subject")
            if fact["source"].startswith("artifact:") and fact["customer_visible"]:
                raise AssertionError("imported facts cannot become customer-visible automatically")


@dataclass(frozen=True)
class Check:
    id: str
    holds: bool
    detail: str


def _check(checks: list[Check], check_id: str, holds: bool, detail: str) -> None:
    checks.append(Check(check_id, holds, detail))


def run_e2e(fixtures: dict[str, Any]) -> dict[str, Any]:
    artifacts = {item["id"]: _artifact_from_fixture(item) for item in fixtures["artifacts"]}
    session = GuidedConfigSession()
    checks: list[Check] = []

    _check(
        checks,
        "starts-at-company-identity",
        session.next_question().id == "organization.name",
        session.next_question().id,
    )
    session.set_organization(
        company_id=fixtures["organization"]["id"],
        display_name=fixtures["organization"]["display_name"],
        event_id="admin-1",
    )
    _check(
        checks,
        "organization-opens-offerings-neighborhood",
        session.next_question().id == "offerings.start",
        session.next_question().id,
    )

    first, second = fixtures["manual_offerings"]
    session.add_offering(first, event_id="admin-2", origin="manual")
    _check(
        checks,
        "product-add-does-not-jump-to-distant-stage",
        session.next_question().id == "offerings.continue"
        and session.next_question().semantic_scope == "offerings",
        session.next_question().id,
    )
    _check(
        checks,
        "open-collection-has-no-silent-closure",
        session.collection_state == CollectionState.COLLECTING,
        session.collection_state.value,
    )

    session.choose_offering_checkpoint("add", event_id="admin-3")
    _check(
        checks,
        "explicit-more-product-choice-asks-for-neighbor-item",
        session.next_question().id == "offerings.add-item",
        session.next_question().id,
    )
    session.add_offering(second, event_id="admin-4", origin="manual")
    _check(
        checks,
        "second-product-repeats-collection-checkpoint",
        session.next_question().id == "offerings.continue",
        session.next_question().id,
    )

    session.choose_offering_checkpoint("close", event_id="admin-5")
    _check(
        checks,
        "explicit-owner-closure-is-required-before-next-neighborhood",
        session.collection_state == CollectionState.CLOSED_NONEMPTY
        and session.next_question().id == "offerings.details-or-import",
        f"{session.collection_state.value}; {session.next_question().id}",
    )

    xlsx = artifacts["catalog-xlsx-2026-07"]
    before_xlsx = session.draft_json()
    session.stage_artifact(xlsx, event_id="admin-6")
    _check(
        checks,
        "xlsx-upload-is-staged-not-auto-applied",
        before_xlsx == session.draft_json()
        and session.artifact_status[xlsx.id] == ArtifactStatus.AWAITING_ADMIN_REVIEW,
        session.artifact_status[xlsx.id].value,
    )
    _check(
        checks,
        "file-upload-prompts-preview-before-config-change",
        session.next_question().id == "artifact.review",
        session.next_question().id,
    )
    _check(
        checks,
        "file-instructions-are-data-not-workflow-authority",
        xlsx.untrusted_excerpt not in session.draft_json() and session.draft.get("agent") is None,
        "draft has no imported instruction or agent mutation",
    )

    session.accept_proposals(
        xlsx.id,
        ["xlsx-add-ax-500", "xlsx-price-ax-500"],
        event_id="admin-7",
    )
    ax500_price = next(fact for fact in session.draft["facts"] if fact["id"] == "ax-500-price")
    _check(
        checks,
        "accepted-xlsx-adds-evidence-backed-offering-and-fact",
        any(offering["id"] == "ax-500" for offering in session.draft["offerings"])
        and ax500_price["source"].startswith("artifact:catalog-xlsx-2026-07:")
        and not ax500_price["customer_visible"],
        ax500_price["source"],
    )
    _check(
        checks,
        "imported-offering-reopens-collection",
        session.collection_state == CollectionState.COLLECTING
        and session.next_question().id == "offerings.continue",
        f"{session.collection_state.value}; {session.next_question().id}",
    )

    session.choose_offering_checkpoint("close", event_id="admin-8")
    pdf = artifacts["delivery-pdf-2026-07"]
    before_pdf = session.draft_json()
    session.stage_artifact(pdf, event_id="admin-9")
    _check(
        checks,
        "pdf-upload-is-staged-not-auto-applied",
        before_pdf == session.draft_json()
        and session.next_question().id == "artifact.review",
        session.next_question().id,
    )
    session.accept_proposals(pdf.id, ["pdf-delivery-ax-300"], event_id="admin-10")
    delivery = next(fact for fact in session.draft["facts"] if fact["id"] == "ax-300-lead-time")
    _check(
        checks,
        "accepted-pdf-fact-keeps-page-evidence-and-private-default",
        "page:3;table:1;row:4" in delivery["source"] and not delivery["customer_visible"],
        delivery["source"],
    )

    before_duplicate = session.draft_json()
    repeated = session.accept_proposals(pdf.id, ["pdf-delivery-ax-300"], event_id="admin-10")
    _check(
        checks,
        "same-admin-event-is-idempotent",
        not repeated and before_duplicate == session.draft_json(),
        "duplicate event caused no draft mutation",
    )
    session.assert_invariants()
    _check(
        checks,
        "draft-never-publishes-itself",
        session.draft["lifecycle"] == "draft",
        session.draft["lifecycle"],
    )

    return {
        "design": {
            "company_config": "durable draft only",
            "conversation_state": "separate GuidedConfigSession",
            "llm_authority": "intent/proposal only; controller chooses questions and applies accepted patches",
            "file_authority": "evidence-backed proposal only; customer_visible defaults false",
        },
        "metrics": {
            "check_count": len(checks),
            "pass_count": sum(check.holds for check in checks),
            "pass_rate": round(sum(check.holds for check in checks) / len(checks), 4),
            "closure_violation_rate": 0.0
            if next(check for check in checks if check.id == "product-add-does-not-jump-to-distant-stage").holds
            else 1.0,
            "unconfirmed_commit_rate": 0.0
            if next(check for check in checks if check.id == "xlsx-upload-is-staged-not-auto-applied").holds
            and next(check for check in checks if check.id == "pdf-upload-is-staged-not-auto-applied").holds
            else 1.0,
        },
        "checks": [check.__dict__ for check in checks],
        "final_draft": session.draft,
        "event_log": session.event_log,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).with_name("guided_admin_config_e2e_fixtures.json"),
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = run_e2e(json.loads(args.fixtures.read_text(encoding="utf-8")))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0 if report["metrics"]["pass_rate"] == 1.0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
