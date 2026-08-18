# Schema-Total Universal Configuration Flow

## Claim boundary

The product must not claim to enumerate every possible real-world company.
The testable claim is narrower and useful:

\[
\mathrm{Reach}(F_{\Sigma,R}) = \mathcal{C}_{\Sigma,R}
\]

- \(\Sigma\) is the closed `CompanyAgentConfig` core schema.
- \(R\) is the set of registered, version-matched domain module providers.
- \(\mathcal{C}_{\Sigma,R}\) is the set of company graphs valid under that
  schema and those registered modules.
- \(F_{\Sigma,R}\) is the compiled configuration question graph plus its typed
  staged reducer.

This does **not** make a claim about an unregistered `DomainModule.config`, an
unknown future sector, or arbitrary real-world concepts that have not entered
the ontology.

## The semantic slot manifest

An editable schema field is represented by a semantic path, rather than a raw
JSON Patch path. Examples:

```text
organization.display_names
parties[*].kind
offerings[*].provider_id
facts[*].customer_visible
customer_profiles[*].fields[*].allowed_values
processes[*].transitions[*].action
policies[*].template_fact_id
agent.unknown_fact_action
modules[clinic.appointments@1.0.0].config
```

System-owned `schema_version` is not an admin-editable slot. `lifecycle` is a
review gate, not an unrestricted value field.

For the core schema, let \(S_\Sigma\) be this finite manifest. The registry
must satisfy:

\[
\forall p \in S_\Sigma,\ \exists!\ f \in \mathrm{FlowSpecs}: p \in f.\mathrm{targetPaths}
\]

`exists!` means exactly one owner. Missing ownership makes a company shape
unreachable; multiple owners makes the next question ambiguous. Both are
build/test failures. A second, independent invariant prevents a weaker
questionnaire-only result:

\[
\forall f\in\mathrm{CoreFlowSpecs},\quad
\exists\ \mathrm{TypedReducer}(f)
\]

`apps/api/src/modules/agents/config_flow.py` checks this mechanically against
the Pydantic models. A newly added core field cannot be silently omitted from
the manifest or from a `FlowSpec`.
`apps/api/src/modules/agents/configuration_session.py` independently fails
construction if a core `FlowSpec` has no typed reducer.

## Generic FlowSpec families

The flow is not a product wizard. Every core path is assigned to one of these
structural editors:

| Family | Examples | Flow behavior |
|---|---|---|
| Singleton form | organization, agent policy | structured values; explicit defer only when optional |
| Entity collection | parties, offerings | add/import/defer/explicit collection closure |
| Reference binding | provider, fact subject, customer profile, template fact | selector appears only after referenced graph nodes exist |
| Graph-edge collection | relationships | subject–predicate–object editor |
| Evidence collection | facts | evidence → proposal → preview → explicit acceptance |
| Nested collection | profile fields, process transitions, policy conditions | same collection gate at the nearest graph anchor |
| Process builder | states, initial state, actions | transition editor follows state/action definition |
| Policy builder | conditions and effect | effect-specific reference branch when necessary |
| Review gate | lifecycle | validate → request review → separately approve |
| Module config | registered `DomainModule.config` | provider-owned form/validation/review |

All collections use an explicit closure gate. Thus the invariant previously
implemented only for offerings becomes generic:

\[
\mathrm{open}(c) \Rightarrow
\mathrm{next.anchor} \in \mathrm{neighbourhood}(c)
\cup \{\mathrm{review},\mathrm{defer}\}
\]

The LLM may propose a closed intent, but it cannot choose a `FlowSpec`, name a
mutation path, close a collection, apply a patch, or approve a lifecycle
transition.

## Constructive reducer and staged graph

The question graph alone is not enough to establish reachability: a fact may
be collected before the owner chooses its subject; a policy may need a fact
template collected later. Those are valid conversation states but invalid
`CompanyAgentConfig` documents. The session therefore has a separate typed
aggregate:

```text
typed command → immutable proposal preview → explicit accepted action
              → StagedCompanyDraft → materialize → CompanyAgentConfig
```

`StagedCompanyDraft` keeps `FactCore` without a subject, `OfferingCore`
without a provider, `PartyCore` without profiles, `PolicyCore` without
conditions/template, and analogous nested collections. It contains no generic
JSON Patch. A server-issued `DraftRef` identifies an instance; business IDs
such as `linen-set` or `seller-ada` are only materialized at the end.

The reducer's transition ref is opaque and HMAC-bound to:

```text
tenant_id, admin_id, session_id, revision, FlowSpec.id,
command type / proposal id / payload digest / collection scope, expiry
```

Consequences:

1. A proposal is inert until a separate explicit acceptance action arrives.
2. A stale, forged, cross-tenant or cross-admin action cannot reduce state.
3. A duplicate accepted webhook is a same-session no-op.
4. A reference command accepts only a live, correctly typed `DraftRef` in the
   current session—not an arbitrary `provider_id` or `subject_id` string.
5. A root or nested collection cannot advance until it has an explicit close
   (or an allowed explicit defer); every existing nested profile/process/policy
   scope has its own close gate.

Only `materialize()` turns the staged graph into the durable Pydantic model.
It requires all needed instance bindings, all open scopes closed, a complete
typed reducer trace, canonical graph validation, and module totality.

## Extension contract

The core intentionally does not interpret arbitrary `DomainModule.config`.
For a configured module `m`, totality adds one required slot:

\[
S_{\Sigma,R} = S_\Sigma \cup
\{\mathrm{modules}[m.id@m.version].\mathrm{config}\}
\]

The matching `ModuleFlowProvider` must provide all three:

1. exact module ID + schema version match;
2. a configuration validator;
3. a non-empty runtime adapter ID and a `FlowSpec` that owns the module config
   root.

If a provider is missing or broken, compilation is deliberately incomplete.
If its current config fails validation, its own `MODULE_CONFIG` FlowSpec stays
reachable so the admin can repair it, but materialization remains blocked. The
module is never treated as a generic JSON editor or customer-facing knowledge.

## Evidence and limits

The deterministic E2E is:

```bash
PYTHONPATH=apps/api python experiments/universal_config_flow_e2e.py
```

It verifies core-slot totality, one-owner uniqueness, typed-reducer totality,
collection gates, topological dependencies, a catalogue-free clinic, a
marketplace graph, unregistered-module failure, registered-module restoration
of totality, and a broken module provider's fail-closed behavior. It then
constructs and materializes:

- a marketplace target graph through accepted typed events, checking an exact
  JSON-equivalent round trip; and
- a catalogue-free clinic with profile fields, a process transition, a policy
  template fact and a registered module config.

The same E2E checks that previews do not mutate, opaque actions are bound to
the admin/revision, duplicate accepts are no-ops, and an open collection
blocks a distant question.

This establishes **structural reachability as an executable contract**. Let
`E(c)` be the canonical typed command encoding of a valid target graph `c`,
`δ*` repeated deterministic reduction, and `M` materialization. Because
ownership is unique, every owner has a reducer, references use instance-level
selectors, and dependencies form a DAG, the intended construction property is:

\[
\forall c\in\mathcal C_{\Sigma,R},\qquad
M(\delta^*(s_0,E(c)))=c
\]

For unbounded literal values this is a parametric schema claim, not finite
enumeration: each command accepts exactly the relevant Pydantic value domain.
The E2E uses graph shapes that exercise every core editor family and a module
boundary. It is not a claim that an LLM can invent a valid company
configuration unaided.

This is a **structural** result. It does not prove that every value inside an
open `JsonValue` is semantically useful. That remains the responsibility of a
registered module validator or a typed fact editor. It also does not make
optional concepts mandatory: a lead-capture company can skip products,
processes and policies while retaining a complete, publishable core contract.

## Integration boundary

`ConfigurationFlowRegistry.compile(config)` returns ordered,
channel-neutral questions with only allowed transitions.
`ConfigurationFlowSession` implements their typed, in-memory reference reducer
and materializer. The existing intent policy may render a session step as reply
buttons, a list, a Flow, a document prompt or sequential text.

A production persistence adapter must store the staged aggregate and action
records atomically, preserving their tenant/admin/session/revision bindings.
The existing direct `PATCH .../draft` route must either be restricted to an
explicit audited override/migration path or invalidate active configuration
sessions; it must not become a parallel LLM-authorized write path. Artifact and
LLM output remain proposals until the session's explicit admin acceptance.

## Captured local result — 2026-07-31

`universal_config_flow_e2e.py` passed **14/14** checks: 57 core semantic
slots, 18 core FlowSpecs, exact one-owner coverage, typed reducer coverage,
two constructive graph traces, module fail-closed behavior, and action/scope
guards.
