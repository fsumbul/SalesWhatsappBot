# Admin chat phone formatting fix — 2026-09-14

A natural outreach request containing a spaced phone number failed with HTTP 502:
Qwen returned the same digits without spaces, but planner grounding required a
literal substring. The live model probe reproduced `Ungrounded argument`.

The planner now matches a complete international phone token using only formatting
normalization (spaces, parentheses, dots and hyphens), then restores the operator's
literal recipient text before the remaining grounding checks. Digits and the
explicit `+`/`00` prefix must match; country codes are never inferred. Workflow
translation compacts each validated recipient separately, because the shared form
uses whitespace to separate recipients. This starts preparation, not confirmation.

Validation:

- 50 focused planner, workflow and PostgreSQL/RLS tests passed against the local
  disposable database. The initial default-port run could not connect; rerunning
  against the documented test database on port 55432 passed.
- Two HTTP lifecycle cases (compact and formatted phone) passed after extending
  the existing review/queue/idempotency test. Provider transport was mocked.
- Ruff and `git diff --check` passed.
- Real production Qwen reproduced the failure before the change and accepted the
  same request with the candidate planner loaded in memory.
- Production HTTPS test after deployment returned HTTP 200, model source, outreach
  details form, awaiting-input status and no field errors. Repeating the same
  client message ID returned an identical response with exactly one saved turn.
  No live message was queued or sent by this verification.

Deployment:

- Verified both original production source hashes matched Git HEAD.
- Confirmed no pending/processing/queued/sending runtime or outbound work across
  tenant contexts before cutover.
- Server-only source, private environment and database backup:
  `C:\sites\ashiraai\backups\phone-grounding-20260914-180633`.
- Replaced only `planner.py` and `workflow_intents.py`, with byte hashes verified;
  restarted API, agent worker and recovery tasks. Private environment hash stayed
  unchanged. No migrations or company configuration changes.
- Full production runtime preflight with `--tenant-slug kasnak --require-llm`
  completed successfully. Intermittent SSH banner timeouts were terminal before
  retrying; they were not deployment results.
