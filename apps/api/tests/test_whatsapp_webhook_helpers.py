"""Pure parsing tests for inbound WhatsApp webhook payload variants."""

from src.modules.outreach.webhooks import _message_body, _normalize_wa_number


def test_normalizes_meta_phone_identifier() -> None:
    assert _normalize_wa_number("905465551212") == "+905465551212"
    assert _normalize_wa_number("+90 546 555 12 12") == "+905465551212"
    assert _normalize_wa_number(None) is None


def test_extracts_text_and_interactive_replies() -> None:
    assert _message_body({"type": "text", "text": {"body": " Merhaba "}}) == "Merhaba"
    assert (
        _message_body(
            {
                "type": "interactive",
                "interactive": {"button_reply": {"id": "quote", "title": "Teklif al"}},
            }
        )
        == "Teklif al [quote]"
    )
    assert (
        _message_body(
            {
                "type": "interactive",
                "interactive": {"list_reply": {"id": "cast", "title": "Döküm kasnak"}},
            }
        )
        == "Döküm kasnak [cast]"
    )


def test_extracts_media_caption_but_not_binary_content() -> None:
    assert _message_body({"type": "image", "image": {"caption": "Ölçü çizimi"}}) == "Ölçü çizimi"
    assert _message_body({"type": "audio", "audio": {"id": "media-id"}}) is None
