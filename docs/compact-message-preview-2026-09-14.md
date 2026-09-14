# Compact WhatsApp workflow preview

Replaced the repeated message summary and full-width review form with a two-column review: recipient/template/consent summary and a separately labelled WhatsApp preview. At narrow widths the preview appears above the summary. Text templates use a recipient-view white bubble, WhatsApp chat background, footer, and message action rows; preview action rows are deliberately not interactive. No fabricated read receipts or delivery timestamps appear in the preview.

Editable fields remain in compose/details. Review shows immutable values with the existing edit action. Existing workflow API, revision, idempotency, consent checks, and send behavior are unchanged. Template creation shares the same preview presentation. Only the existing WhatsApp workflow is covered; no Instagram transport or composer was added.

References inspected through Mobbin:
- https://mobbin.com/screens/0c79d28b-8330-423c-ab01-0c281b07f528 (Mailchimp review and preview separation)
- https://mobbin.com/screens/2bc0dba2-ab10-4620-8d0e-d246e845cb6b (WhatsApp message and action presentation)

Validation: production Next build passed; six request-recovery tests passed; development gallery checked in the in-app browser at desktop and 390px widths. Gallery fixture uses synthetic data and disabled actions. No test message was sent.

Deployed web build `KVzYz5-wP746azGJTeotx`; backup `C:\sites\ashiraai\backups\compact-preview-20260914-185421`. Source/build hashes verified, private environment unchanged, web restart succeeded. Full production runtime preflight passed (exit 0). Live approved Flow template preview visually checked; test draft cancelled before recipient entry or send.
