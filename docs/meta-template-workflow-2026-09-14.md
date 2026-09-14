# Meta template workflow — 2026-09-14

Sending now presents the live Meta-approved template selector immediately, with a
read-only message preview. Template body, header, footer and button text are not
accepted as editable outreach fields; only recipient and variable values can be
changed. The company's three approved templates were verified through Meta:
`arti_kasnak_teklif_formu_v1`, `ilk_bilgilendirme`, and `hello_world`.

The catalog now includes text utility templates and approved Flow/static URL/phone
buttons, in addition to quick replies. Flow sends use the approved template and a
stable recipient-specific token. The existing outbox still checks the current Meta
approval/content at queue/send boundaries and performs one external message POST.
Existing draft cards load the catalog when reopened; names or ordinal choices can
be interpreted by the model against the server-supplied Meta choices.

`Başka şablon ekle`, or a natural request such as `yeni bi whatsapp şablonu
oluşturalım`, starts a separate `create_template` workflow. The form collects name,
language, category, text header/body/footer, quick reply labels and example values.
It displays the complete sample message before the explicit `Meta onayına gönder`
action. Creation currently supports text templates with static header/footer and
up to three quick reply buttons; media/authentication template authoring is outside
this form. Pending, rejected, paused and disabled templates are excluded from send
options. The form never edits an existing approved template.

Submission persists a workflow action receipt before the single Meta POST. Repeated
operation IDs return that receipt; ambiguous transport outcomes trigger read-only
status reconciliation rather than another POST. The result distinguishes Meta
review from approval, and review continues while another workflow is active.
Meta rejection exposes its user-facing explanation where available.

A real-model check found the enlarged planner exceeded the live 4096-token context
when reserving 2200 output tokens. Reserving 1024 tokens for its structured intent
restored the tested requests. The production model accepted both natural template
creation examples and a spaced-number outreach request.

Validation: 92 focused workflow/PostgreSQL/RLS tests passed; the follow-up template
and send suite passed 35 tests after Flow-button support, and the existing-card
upgrade regression passed. Ruff, diff checks and the production web build passed.
The frontend network recovery change also passed six Node tests: retrying only reads
or explicitly deduplicated commands, preserving identical operation IDs/bodies,
and not retrying arbitrary writes or HTTP permission/validation failures.

Deployment backup: `C:\sites\ashiraai\backups\meta-templates-20260914-183123`.
228 deployment file hashes verified; web build `SdOC-F_CX15iKZRdBo2y2`.
The existing-card follow-up backup is
`C:\sites\ashiraai\backups\phone-grounding-20260914-183450`.
Private environment hashes stayed unchanged. No database migrations were needed.
Production runtime preflight passed. Live browser QA confirmed three real choices,
immutable approved preview, separate new-template form and preview with samples.
No template was submitted to Meta and no WhatsApp message was sent during live QA.

Primary API references:

- [Meta: create a text template with examples and quick replies](https://www.postman.com/meta/whatsapp-business-platform/request/uzphwqw/create-template-w-text-header-text-body-text-footer-and-2-quick-reply-buttons)
- [Meta: send a Flow template message](https://www.postman.com/meta/whatsapp-business-platform/request/cgamr2u/send-flow-template-message)
