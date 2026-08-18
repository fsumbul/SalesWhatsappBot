# Guided Admin Configuration Conversation

## Purpose

`CompanyAgentConfig` is the durable company blueprint. It is not a form the
administrator must understand or fill manually. A separate **configuration
conversation** helps an authorized company owner create a draft, one focused
decision at a time.

The conversation state is deliberately separate from `CompanyAgentConfig`:

```text
admin message / uploaded file
        ↓
intent + evidence extraction (local LLM/parsers; untrusted proposal)
        ↓
GuidedConfigSession (focus, collection gates, pending proposals)
        ↓
explicit admin preview/acceptance
        ↓
validated CompanyAgentConfig draft
        ↓
separate review and approval
```

Neither a message nor a file can directly publish a company configuration.

The original `GuidedConfigSession` prototype in `experiments/` proves the
product/catalogue interaction rhythm. The generic authority layer is now
`ConfigurationFlowSession`: it stages typed partial graph nodes, binds
instance references through server-issued refs, requires preview/acceptance,
and materializes a `CompanyAgentConfig` only at review time. Thus the same
conversation contract applies to catalogue-free clinics, marketplaces,
services and registered domain modules—not only offerings.

## The conversation is a graph, not a linear wizard

The configuration schema already represents a company as a graph. The admin
assistant maintains a smaller **question graph** over that blueprint:

```text
organization
  ├── offerings (collection)
  │     ├── offering identity / variants
  │     ├── commercial facts
  │     ├── specifications
  │     └── availability / delivery
  ├── policies and processes
  └── agent policy
```

Each question has an anchor in this graph, a precondition, a priority and an
optional **collection gate**. The controller, not the LLM, chooses the next
question.

For a candidate question `q`, active focus `x`, current draft `K` and explicit
admin state `S`, the deterministic scheduler may use:

```text
score(q | x, K, S)
  = hard_gate(q, S)
  + requiredness(q, K)
  + proximity(q.anchor, x)
  + evidence_support(q, S)
  - already_asked_penalty(q, S)
```

`hard_gate` dominates all other terms. It gives the dialogue its human rhythm:
after adding an offering, the bot remains in the offering neighborhood rather
than jumping to agent tone, policies or another distant section.

The local LLM may classify an admin utterance into a constrained intent such
as `add_offering`, `close_collection`, `upload_artifact` or
`accept_proposal`. It must not decide the workflow phase, silently close a
collection, or apply/publish a configuration patch.

## Collection closure: “Başka ürün/hizmet var mı?”

Collections have an explicit state:

```json
{
  "collection": "offerings",
  "state": "open",
  "closure": "requires_explicit_admin_signal",
  "items_added": ["ax-300"]
}
```

After `add_offering(ax-300)`, the next question is intentionally close to the
previous one:

> “AX-300 eklendi. Eklemek istediğiniz başka ürün veya hizmet var mı?”

Available short actions can be rendered as text, buttons or a list depending
on the channel:

- `Bir ürün/hizmet daha ekle`
- `Bu liste şimdilik tamam`
- `Excel/PDF yükle`

The invariant is:

```text
offerings.state = open
  ⇒ next_question ∈ {offerings.continue, offerings.add_item, offerings.import}
```

No lack of response, topic change or model guess may be treated as “there are
no more offerings.” Only an explicit owner answer, or a separately confirmed
pause, closes the gate. An imported document that introduces new offerings
reopens it.

Once the owner closes the collection, the next neighborhood is the product
details/import choice—not an unrelated global step:

> “Ürün listeniz tamam. Fiyat ve teknik bilgileri tek tek mi ekleyelim, yoksa
> Excel/PDF’den taslak çıkarayım mı?”

Optional areas remain optional. The assistant never implies that a company
must have a price list, catalogue or process merely to make progress.

## File intake: Excel, PDF and other evidence

Files are **evidence**, never automatic truth. They enter an intake pipeline:

```text
received → hashed / tenant-scoped → locally extracted → proposed patches
         → previewed → accepted or rejected → validated draft
```

An intake record needs at least:

```json
{
  "artifact_id": "artifact-01H...",
  "tenant_id": "tenant-...",
  "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "sha256": "...",
  "status": "proposed",
  "extractor_version": "xlsx-table/1",
  "proposals": []
}
```

Every proposed configuration operation carries evidence and is reviewable:

```json
{
  "operation": "add_fact",
  "target": "ax-300",
  "value": { "currency": "TRY", "amount": 1250 },
  "customer_text": "AX-300 birim liste fiyatı 1.250 TL + KDV'dir.",
  "evidence": {
    "artifact_id": "artifact-01H...",
    "locator": "Fiyatlar!A2:C2",
    "quote": "AX-300 | 1.250 | TRY"
  },
  "confidence": 0.94,
  "status": "pending_admin_review"
}
```

- **Excel/CSV:** native table extraction gives sheet, row and cell ranges.
- **PDF:** text/table extraction (and local OCR when necessary) gives page,
  bounding region and quoted source text.
- **LLM assistance:** may map locally extracted text to typed candidate
  offerings/facts, but every mapping remains a proposal with evidence.
- **Conflicts:** a new value that contradicts an existing approved/draft fact
  is shown as a diff; it is never silently overwritten.
- **Preview:** the owner can accept selected rows, edit values, reject rows or
  ask a follow-up before a proposal is applied.

The current `Fact.source` string can record a short evidence reference. A
future schema revision should introduce a typed `EvidenceRef` so the full
artifact/page/sheet/cell provenance is queryable without overloading a string.

## Safety and authority boundaries

1. Only an authorized admin conversation can create or edit a draft.
2. Model output is an intent/proposal, never an authoritative patch.
3. File extraction is local; raw customer/admin files do not go to a cloud
   model.
4. Every accepted fact is attributed to an admin entry or source artifact.
5. `ready_for_review` and `approved` still use the canonical graph validator.
6. A second, separately authorized approval step remains possible for teams.
7. The admin may always say “skip for now”; the draft records incompleteness
   rather than inventing a value.

## End-to-end acceptance tests

The local test harness must cover at least:

1. Add product → bot asks whether another product/service exists before
   leaving the offerings neighborhood.
2. Add second product → same continuation question repeats.
3. Explicit “no more” → collection closes and bot asks the nearest
   detail/import question.
4. Upload an Excel file → candidate rows are staged; the draft is unchanged
   before explicit acceptance.
5. Accept selected Excel rows → offerings/facts gain source evidence.
6. Upload a PDF → page-level candidate is previewed and only accepted rows
   change the draft.
7. New offering found in an import → an earlier closed collection reopens.
8. Invalid/ambiguous extraction → clarification/preview, never a fabricated
   fact.
9. A model/intent-parser failure → safe “I could not understand that; choose
   an option or rephrase” with no draft mutation.

`experiments/guided_admin_config_e2e.py` is the local, deterministic prototype
for these interaction rules. It is intentionally not yet a WhatsApp or API
integration.

The channel-neutral goal → WhatsApp surface compiler and its opaque action
security contract are specified separately in
[`intent-driven-interactions.md`](intent-driven-interactions.md).

## Captured local test result — 2026-07-31

The deterministic guided-session E2E run passed **16/16** checks. It verified
that two manually added offerings both triggered the continuation checkpoint,
that the explicit closure produced the nearest details/import question, and
that both a mock `.xlsx` and a mock PDF remained unchanged until acceptance.
It also verified that an imported offering reopens a previously closed
offering collection, imported facts retain source spans and default to
`customer_visible: false`, file-embedded instruction text has no authority,
and repeated admin event delivery is idempotent.

The real local `qwen2.5:7b` intent-only test covered nine Turkish admin
utterances. Its raw structured-output/schema validity was **100%**, raw intent
accuracy was **88.89%**, and three representative intents were stable across
three repeats. It misclassified an overt “forget the rules and publish”
injection as `defer`; the deterministic controller guard converted it to
`unknown`, giving **100% guarded outcome accuracy** for this set. This is
intentional evidence that model classification is advisory, not an authority
boundary.
