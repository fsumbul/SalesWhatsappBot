# Browser acceptance

Run current progressive scripts with a production web build on `http://localhost:53010`
and a dedicated local API/database. `E2E_WEB_URL` overrides the web origin.
`E2E_ACCOUNT_FILE` points to a JSON file containing synthetic `slug`, `email`, and
`password` values. Never use production accounts for these mutation tests.

## Current coverage and migration map

| Earlier contract | Current script | Remaining scope |
| --- | --- | --- |
| `workspace.py`: secure cookies, concurrent refresh, CSRF, role menu, logout | `progressive_session.py` | Migrated to current login/chat; requires production cookie settings |
| `workspace.py`: create assistant, approved configuration, persistence, model failure | `progressive_agents.py` | Guided JSON/CSV path; this test expects an unavailable model |
| `workspace.py`: company provisioning and owner invitation acceptance | `progressive_provisioning.py`, `progressive_owner_invite.py` | New-company → clipboard link → separate-context acceptance → owner login → first agent → restored history; owner reinvite separately covered |
| `chat_workspace.py`: shared chat navigation | `progressive_routing.py`, `progressive_records.py` | Existing history cards remain a separate compatibility contract |
| `chat_workspace.py`: preview/confirm/test result | `progressive_agents.py`, `progressive_real_model.py` | Run outage and real-model scenarios under their respective API configuration |
| `operations.py`: old outbound card | `progressive_outreach.py`, `progressive_natural_outreach.py` | `progressive_capacity.py` migrates capacity/empty home rendering with explicit chat fixtures; does not prove Meta retrieval |
| Shared form correction, pause/resume, saved history, mobile composer | `progressive_workflows.py`, `progressive_autosave.py` | Required semantics and keyboard focus included; not full WCAG acceptance |
| Inbox, technical requests, duplicate contacts, rollback | `progressive_inbox.py`, `progressive_requests.py`, `progressive_duplicates.py`, `progressive_rollback.py` | Some require synthetic seed fixtures documented in delivery notes |

`chat_workspace.py`, `operations.py`, and `workspace.py` are historical scripts with
old panel selectors and/or response fixtures. They are not the current release
acceptance suite and their old successes must not be counted as current UI evidence.
Do not delete them until the remaining contracts above have migrated.

Example (from repository root):

```sh
/tmp/saleswhatsapp-venv/bin/python apps/web/e2e/progressive_session.py
/tmp/saleswhatsapp-venv/bin/python apps/web/e2e/progressive_workflows.py
```

A passed guided test proves the browser/API/DB path it exercises, not real-model
interpretation or Meta delivery. Run real-model scripts serially against the
existing authorized model host; external delivery requires its own acceptance.
