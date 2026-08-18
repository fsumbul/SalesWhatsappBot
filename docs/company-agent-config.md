# Universal Company Agent Config

`CompanyAgentConfig` is the configuration language for a customer-facing
agent. It is intentionally sector-agnostic: elevator sheaves, a SaaS product,
a consultancy, a clinic, or a retailer are instances of the same core model.

## What the schema spans

The configuration decodes into a typed company graph:

```
Company = (entities, relationships, facts, customer_profiles, processes, policies, agent_policy)
```

- **entities**: the company, customers/partners, and offerings;
- **relationships**: typed edges such as `company --offers--> service`;
- **facts**: source-controlled facts about an entity, optionally approved for
  customer-facing replies;
- **customer profiles**: the fields that a category of customer can have, not
  individual customer records;
- **processes**: deterministic state machines;
- **policies**: rules whose effect is allow, deny, clarification, handoff, or
  a fact-backed template;
- **agent policy**: languages, response mode, length, and unknown-fact action.

The core is closed (`additionalProperties: false`). Future sector detail enters
through namespaced, versioned `modules`; module data cannot become
customer-facing knowledge until a matching validator and runtime adapter are
registered.

## Optional fields and the publish contract

A new agent receives a valid empty `draft` envelope. This lets a non-technical
administrator build a configuration incrementally and in any order; unresolved
references are tolerated only while it is a draft. At `ready_for_review` and
`approved`, the graph becomes closed-world and every entity/fact/profile
reference must resolve.

The graph sections are intentionally optional: a company may have no catalogue,
price list, customer profile, process, or policy.  They are not made mandatory
merely because another sector happens to need them.

Only an approved, customer-facing bot needs this minimum contract:

- company display name in the default conversation locale;
- agent purpose (`sales`, `support`, `lead_capture`, and so on);
- at least one supported locale and a default locale;
- fact-grounded customer claims; and
- an explicit safe unknown-fact action (the schema serializes its deterministic
  default, `handoff`, when the administrator does not change it).

`AgentService.promote_to_live` enforces this contract.  A later capability
module can add conditional requirements: for example, a price-answering module
will require a registered price policy, but a lead-capture-only company never
will.

## Persistence and API

The complete document is stored in `agent_versions.company_config`, so it is
cloned with a draft and preserved by rollback history.

The canonical JSON Schema is available to managers from:

```
GET /api/v1/agents/company-config/schema
```

Drafts can be updated through the existing agent draft endpoint using a full,
validated `company_config` document. The conversational builder will later
compile admin language into a proposed change + preview; it does not yet write
this document itself.

## Local LLM runtime boundary

`CompanyAgentRuntime` is the bridge from an **approved** config to a local
LLM. It does not send the whole JSON document to the model. It projects only
the organization name, reply policy, and `customer_visible` fact texts into a
system prompt. Internal `Fact.value`, sources, customer-profile definitions,
and unregistered module data never cross that boundary.

The local model must return a small JSON reply envelope:

```json
{"action":"reply","reply":"...","fact_ids":["approved-fact-id"]}
```

The runtime rejects unknown/internal fact IDs, duplicate citations, malformed
JSON, and replies above the configured character limit. A malformed or
unavailable model fails closed to the configuration's deterministic
`unknown_fact_action`; raw model text is never sent to a customer. This makes
the LLM the linguistic layer, while the JSON remains the authority for what
the company is allowed to say.
