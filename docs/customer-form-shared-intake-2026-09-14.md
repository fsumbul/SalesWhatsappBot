# Ashiraai customer form and WhatsApp shared intake

Customers can choose “Formu doldur” or “Sohbetle ilerle” when starting a quote. Both edit the same tenant-scoped SelectionRequest. Presentation changes leave existing workflow definitions and draft answers intact.

The hosted form lives at `/tr/forms/selection`. Its seven-day request-scoped JWT is carried in the URL fragment, removed after loading and retained in tab session storage. It grants no team access. The customer projection omits internal notes, assignments and contact metadata. Files remain in the existing selection_files database table; web uploads have no WhatsApp inbound message reference.

Both writers lock the request. Form writes require the current revision and a unique operation ID; retries return the original receipt, conflicts refresh the customer view, and confirmed requests cannot be edited. Form confirmation stores the same immutable snapshot shape and assigns the conversation for team review with an internal-only audit entry. No WhatsApp message is sent by the form endpoint.

Validation:
- Full API suite: 502 passed; focused tests rerun after final changes.
- Ruff passed; mypy passed for all 120 source files; Next production build passed.
- DB integration: form-to-chat and chat-to-form state, chooser and hosted CTA, token scope/expiry, tenant/request isolation, stale revisions, idempotent save/upload, private projection, immutable confirmation and assignment.
- Mobile browser: conditional fields, upload while retaining unsaved inputs, save/reload, no horizontal overflow, complete form and confirm.

Production release evidence will be appended after deployment verification. Actual Meta delivery requires a fresh customer message; local browser or planner tests do not establish delivery.

## Production deployment

- Active company version: 16; version 15 archived. Config fingerprint `04c937047e8854a66ad223990aab9ab0f2c0ef35918a7f6895a32eff852616fd`.
- Migration head: `f67fa89bc01d`.
- Release: `C:/sites/ashiraai/releases/customer-forms-20260914-r1/web`; 414 manifest members verified byte-for-byte. Archive SHA-256 `dd099876bf2fbc8d98f3bf9e7c2d3139cde15f7a9372f616e4a91100aed16e81`.
- Fresh server-only backup: `C:/sites/ashiraai/backups/customer-forms-20260914-144602`, 419 source files and 394626-byte database dump. Dump SHA-256 `40d5c879d626605250c323992734108e803649954f1a8e6b9062645c2b0eac55`.
- Production `.env` hash unchanged. Four application tasks restarted; model tunnel preserved.
- Public form and panel HTTP 200; tokenless form API HTTP 401; dev workflow route remains HTTP 404.
- Read-only live customer-token request through the public web proxy returned HTTP 200 with the existing 22-field draft; private fields excluded. Restricted runtime DB role can access the new receipt table.
- Production preflight passed including migration head, database, Meta, Qwen, Redis, worker, tasks and host gates.
- Live Qwen regression: 25 checks passed on qwen3.8-27b.
- Fresh version-16 WhatsApp delivery not yet observed at final check; user asked to send a new quote-start message. No inbound replay performed.
