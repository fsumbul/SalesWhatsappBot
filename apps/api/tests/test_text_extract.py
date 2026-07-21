"""Unit tests for the shared text-extraction helpers (used by both the
enrichment worker and the web-crawl connector)."""

from src.core.text_extract import DATE_RE, EMAIL_RE, PHONE_RE, strip_html


class TestStripHtml:
    def test_removes_script_and_style_blocks(self) -> None:
        html = "<html><script>evil()</script><style>.x{}</style><p>Hello world</p></html>"
        assert "evil" not in strip_html(html)
        assert "Hello world" in strip_html(html)

    def test_collapses_whitespace(self) -> None:
        html = "<p>Hello</p>\n\n<p>World</p>"
        assert strip_html(html) == " Hello World "


class TestEmailRe:
    def test_finds_simple_email(self) -> None:
        assert EMAIL_RE.findall("Contact us at info@acme.com today") == ["info@acme.com"]

    def test_finds_multiple_emails(self) -> None:
        text = "sales@acme.com or support@acme.co.uk"
        assert EMAIL_RE.findall(text) == ["sales@acme.com", "support@acme.co.uk"]

    def test_no_match_in_plain_text(self) -> None:
        assert EMAIL_RE.findall("no email here") == []


class TestPhoneRe:
    def test_finds_turkish_mobile(self) -> None:
        assert PHONE_RE.findall("Call us: 0532 123 45 67 anytime") == ["0532 123 45 67"]

    def test_finds_e164_format(self) -> None:
        assert PHONE_RE.findall("+905321234567") == ["+905321234567"]

    def test_no_match_for_short_digit_sequence(self) -> None:
        assert PHONE_RE.findall("order #123") == []


class TestDateRe:
    def test_matches_dotted_date(self) -> None:
        assert DATE_RE.match("01.02.2024")

    def test_matches_slashed_date(self) -> None:
        assert DATE_RE.match("1/2/24")

    def test_does_not_match_phone_number(self) -> None:
        assert DATE_RE.match("+905321234567") is None
