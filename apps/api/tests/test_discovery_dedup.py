"""Unit tests for discovery dedup helpers (no DB required)."""

from src.modules.discovery.service import extract_domain, normalize_company_name


class TestNormalizeCompanyName:
    def test_lowercases_and_sorts_tokens(self) -> None:
        assert normalize_company_name("Acme Elevator Parts") == "acme parts"

    def test_strips_legal_suffixes(self) -> None:
        assert normalize_company_name("Yildiz Asansor Sanayi Ticaret") == "yildiz"

    def test_strips_gmbh_and_ltd(self) -> None:
        assert normalize_company_name("Muster GmbH") == "muster"
        assert normalize_company_name("Acme Ltd") == "acme"

    def test_same_company_different_word_order_matches(self) -> None:
        a = normalize_company_name("Kasnak Yildiz Sanayi")
        b = normalize_company_name("Yildiz Kasnak")
        assert a == b

    def test_non_latin_scripts_preserved(self) -> None:
        # Cyrillic / Arabic ranges must survive the character filter.
        assert normalize_company_name("Лифт Москва") == "лифт москва"

    def test_empty_or_only_stopwords_yields_empty_string(self) -> None:
        assert normalize_company_name("Ltd Gmbh Co") == ""

    def test_punctuation_and_symbols_stripped(self) -> None:
        assert normalize_company_name("A&B, Sales! (Turkey)") == "a b sales turkey"


class TestExtractDomain:
    def test_strips_www_and_scheme(self) -> None:
        assert extract_domain("https://www.example.com/path") == "example.com"

    def test_bare_host_without_scheme(self) -> None:
        assert extract_domain("example.com") == "example.com"

    def test_lowercases_host(self) -> None:
        assert extract_domain("https://Example.COM") == "example.com"

    def test_uppercase_scheme_handled(self) -> None:
        assert extract_domain("HTTPS://Example.COM") == "example.com"

    def test_none_input_returns_none(self) -> None:
        assert extract_domain(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_domain("") is None

    def test_malformed_url_returns_none_not_raises(self) -> None:
        assert extract_domain("http://") is None
