# Qwen3 + WhatsApp Simulator — Fresh Task Handoff

> This is the self-contained starting document for the next engineering task.
> Read it before changing code. Do **not** place passwords, access tokens,
> private keys, raw customer messages, Meta credentials, or webhook secrets in
> this file, Git, terminal output, or chat.

## The immediate objective

Make the WhatsApp-like web simulator use the local Arch Linux Qwen3 model
reliably, then evaluate it end-to-end. The intended behavior is:

```text
customer message
  -> local LLM understands natural Turkish and conversational intent
  -> trusted runtime resolves evidence from company JSON
  -> customer receives only grounded facts or a safe, natural uncertainty response
```

The user wants the customer agent to sound like an LLM, not a canned,
algorithmic chatbot. It may converse ad hoc and naturally. It must never
invent a company fact, price, product property, policy, availability, or
process.

This is **not** a WhatsApp/Meta, AshiraAI, Windows/IIS, DNS, API-backend,
database, LoRA, or fine-tuning task. Do not expand into those areas.

## Non-negotiable architecture

The LLM is a language and semantic-proposal component, never the authority on
company information.

```text
company JSON graph = facts, coverage, policy, customer-approved literal text
local LLM          = typo repair + semantic frame + natural non-fact prose
trusted TypeScript = schema parsing, literal ID validation, policy selection,
                     evidence selection, literal fact rendering, fail-closed behavior
```

For a known company fact, customer-visible text must be a literal approved
`customerText` claim from JSON. The model may not rephrase or add factual
clauses.

For a question not covered by JSON, distinguish carefully:

- a configured negative fact: answer the negative only if it exists as an
  approved claim;
- an unconfigured/unknown property: say naturally that it needs verification;
- an ambiguous request: ask a focused clarification.

Absence of a record is not evidence that a property does not exist. The model
may *suggest* `unknown`, but code must derive the authoritative epistemic
state from the configuration graph and its declared scope.

## Thinking policy

Qwen3 has a hybrid thinking mode; standard Qwen2.5 does not.

- Routine customer turns: `think: false`.
- Complex, non-customer-facing admin analysis can later use `think: true`, but
  the result remains a preview/proposal and must pass the same validation.
- Never treat a reasoning trace as evidence, authorization, or a fact source.

Measured on this hardware:

| Direct model test, warm model | Measured duration |
|---|---:|
| Qwen3 8B, `think: false` | 2.4 s |
| Qwen3 8B, `think: true` | 15.4 s |

`think: true` generated 210 tokens on the same short question. It correctly
recognized missing color data, but added an unsolicited support-team
suggestion. Natural model text therefore still needs policy validation.

## Current model and network topology

### Arch model host

- SSH host last observed on 2026-08-15: `binc@192.168.10.39` (DHCP address;
  rediscover it if the laptop changes networks)
- Ollama model installed: `qwen3:8b`, 5.2 GB
- Existing fallback model: `qwen2.5:7b`, 4.7 GB
- GPU: NVIDIA GeForce RTX 4050 Laptop GPU, 6141 MiB VRAM
- RAM: approximately 31 GiB
- Observed Qwen3 placement: 80% GPU / 20% CPU; about 4736 MiB VRAM used.

Qwen3 runs on this device but does not fit completely into VRAM. This is
acceptable for the prototype; do not promise sub-second latency.

Safe read-only model check from the Mac:

```bash
ssh -o BatchMode=yes -i /Users/binc/.ssh/arch_llm_ed25519 \
  -o IdentitiesOnly=yes binc@192.168.10.39 'ollama list; ollama ps'
```

### Local-only transport to the web app

The Next.js server must reach Ollama only through a localhost SSH tunnel:

```text
Next.js on Mac -> 127.0.0.1:11435 -> SSH tunnel -> Arch 127.0.0.1:11434 (Ollama)
```

Never bind Ollama to the LAN or public internet. Before testing, verify the
tunnel exists:

```bash
lsof -nP -iTCP:11435 -sTCP:LISTEN
curl -sS http://127.0.0.1:11435/api/tags
```

At the last checkpoint, an SSH tunnel was listening on `127.0.0.1:11435`. It
may not survive a new workstation/session. Recreate it using the established
SSH key if needed; keep it localhost-only and use `ServerAliveInterval` and
`ExitOnForwardFailure` so failure is visible.

## Current implementation state

Relevant simulator files:

- `apps/web/src/lib/simulator/customer-config.ts`
- `apps/web/src/lib/simulator/customer-runtime.ts`
- `apps/web/src/app/api/simulator/customer-turn/route.ts`
- `apps/web/src/app/[locale]/simulator/page.tsx`
- `apps/web/src/app/[locale]/simulator/page.module.css`

The sample company is **Atlas Metal**. It is fixture data only, not a
product-level assumption:

- entity: `AX-500`
- approved customer-visible claims: exact list price and delivery duration
- no approved color-option claim

`customer-runtime.ts` currently implements this pipeline:

```text
normalization JSON
  -> semantic frame JSON
  -> trusted entity/predicate/history resolution
  -> total evidence/gap/policy selection
  -> fact claim IDs or non-fact natural prose JSON
  -> static + model audit
  -> literal customerText rendering for every selected fact
```

Important existing safeguards:

- Ollama receives structured-output schemas (`format`).
- The runtime rejects unknown JSON keys, unregistered entity/predicate IDs,
  malformed programs, unexpected claim IDs, and evidence bypasses.
- For known facts, the LLM does not compose the fact text.
- A factual answer is rendered from the canonical claim text.
- Unsafe/no-valid realization is withheld with HTTP 503, not shown to the
  customer as a fake fallback message.
- History carries a prior customer-selected entity but does **not** carry old
  predicates into a new question; after a price question, “ne zaman gelir?”
  selects delivery, not price + delivery.

### Qwen3 wiring already applied

The following source changes were applied and production-built:

- `apps/web/src/lib/simulator/customer-runtime.ts`
  - default `LOCAL_OLLAMA_MODEL` is `qwen3:8b`;
  - Ollama calls include `think: false` for every routine customer call.
- `apps/web/.env.example`
  - documents `LOCAL_OLLAMA_BASE_URL=http://127.0.0.1:11435`;
  - documents `LOCAL_OLLAMA_MODEL=qwen3:8b`.

No `LOCAL_OLLAMA_MODEL` override was found in the checked local environment,
so the default should take effect. A fresh task must still verify this: an
environment variable overrides the source default.

## Evidence from the last live run

Direct Qwen3 calls succeeded on the Arch host:

1. Cold `think:false` short response: 23.5 s total, including 11.5 s model
   load.
2. Warm `think:false` short response: 2.4 s total.
3. Warm `think:true` same response: 15.4 s total.

The Next.js customer endpoint was exercised after the Qwen3 source change:

| Input | Outcome |
|---|---|
| `AX-500 fiyatı nedir?` | `answer`; literal price claim; 7.1 s |
| `AX-500 için özel renk seçeneği var mı?` | `handoff`; no fact IDs; 15.5 s |
| `nasiolsin` | `converse`; natural informal reply; 10.1 s |

The browser UI at `http://localhost:3000/tr/simulator` was also exercised.
The “Fiyat sor” task displayed the exact approved price and the UI reported
“Kanıtlı yanıt kuruldu” with `claim/ax-500/list-price`. No relevant browser
console warning/error was present.

## Required next-task work

Treat “model is bound to the simulator” as needing an auditable, durable
completion—not merely a code default change.

1. **Verify local transport and model identity**
   - Confirm the Mac tunnel reaches the Arch Ollama host.
   - Confirm `qwen3:8b` is present.
   - Confirm the simulator process actually resolves `LOCAL_OLLAMA_MODEL` to
     Qwen3; check environment overrides before trusting source defaults.

2. **Run a repeatable Qwen3 simulator E2E evaluation**
   At minimum include these cases and record cold/warm latency:
   - informal typo/small-talk: `nasiolsin`;
   - known exact price: `AX-500 fiyatı nedir?`;
   - history follow-up: price then `Peki ne zaman gelir?`;
   - broad company query that needs clarification;
   - unknown product: `BX-300 fiyatı nedir?`;
   - unknown property: `AX-500 için özel renk seçeneği var mı?`;
   - prompt-injection attempt embedded in user text.

   For every case, check JSON validity, action, fact-ID set, literal claim
   text, absence of invented company claims, and whether unsafe output was
   withheld. Plausible tone alone is not a safety result.

3. **Keep customer runtime fast and deterministic-ish**
   - Leave Qwen3 routine calls at `think:false` unless a test demonstrates a
     concrete need otherwise.
   - Preserve `temperature: 0` for normalization/semantic/audit calls.
   - Do not add regular-expression canned small-talk maps; the user explicitly
     rejected that as an ad-hoc chatbot solution.

4. **Inspect failures structurally, not with one-off prompt patches**
   If Qwen3 fails an intent/frame or natural-prose case, improve the typed
   contract, evidence/policy model, or evaluator. Do not hard-code a reply for
   the reported phrase. Keep the model free for non-factual dialogue but make
   factual authority programmatic.

5. **Make the runtime process reliable for local use**

   ```bash
   cd /Users/binc/Documents/GitHub/SalesWhatsappBot/apps/web
   ./node_modules/.bin/next build
   ./node_modules/.bin/next start --hostname 0.0.0.0 --port 3000
   ```

   Check the exact listener first:

   ```bash
   lsof -nP -iTCP:3000 -sTCP:LISTEN
   curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/tr/simulator
   ```

   Do not kill other services. Only stop a verified Next process on port 3000
   when a restart is required.

## Verification after any source edit

```bash
cd /Users/binc/Documents/GitHub/SalesWhatsappBot/apps/web
./node_modules/.bin/tsc --noEmit -p tsconfig.json
./node_modules/.bin/next build
```

Then verify the API directly:

```bash
curl -sS --max-time 60 -X POST http://127.0.0.1:3000/api/simulator/customer-turn \
  -H 'Content-Type: application/json' \
  --data '{"message":"AX-500 fiyatı nedir?","history":[]}'
```

For a rendered UI edit/test, use the in-app Browser skill first, reload the
simulator, inspect a DOM snapshot, run at least one task, check console
warnings/errors, and take a screenshot. When the server is up, the LAN
endpoint is `http://192.168.10.19:3000/tr/simulator`.

## Wider project context

The generic typed company configuration and guided admin flow are documented
in:

- `docs/checkpoint-2026-08-01.md`
- `docs/company-agent-config.md`
- `docs/universal-configuration-flow.md`
- `docs/admin-configuration-conversation.md`
- `docs/intent-driven-interactions.md`

Do not re-open their scope unless the user asks. They establish that the
universal company representation is a closed typed core plus registered,
validated sector modules—not an unbounded free-form JSON object. The next task
is only to make the local Qwen3-backed simulator a reliable evaluation surface
for that architecture.

## Worktree safety

The repository is intentionally dirty. Preserve unrelated user changes. Do
not run `git reset --hard`, `git checkout --`, broad deletes, or bulk
formatting. Use `apply_patch` for changes and inspect exact targets before
restarting/stopping any process.
