"""Unit tests for generate_queries (no DB required — Sector and friends are
constructed in-memory via SimpleNamespace, never flushed to a session)."""

from types import SimpleNamespace

from src.modules.discovery.query_generator import generate_queries
from src.modules.sectors.models import KeywordType


def _keyword(keyword: str, language: str, keyword_type: KeywordType = KeywordType.POSITIVE):
    return SimpleNamespace(keyword=keyword, language=language, keyword_type=keyword_type)


def _customer(customer_type: str, language: str):
    return SimpleNamespace(customer_type=customer_type, language=language)


def _country(country_code: str):
    return SimpleNamespace(country_code=country_code)


def _sector(
    *,
    default_language: str = "tr",
    keywords: list | None = None,
    target_customers: list | None = None,
    countries: list | None = None,
):
    return SimpleNamespace(
        default_language=default_language,
        keywords=keywords or [],
        target_customers=target_customers or [],
        countries=countries or [],
    )


class TestGenerateQueries:
    def test_no_countries_yields_no_queries(self) -> None:
        sector = _sector(keywords=[_keyword("kasnak", "tr")], countries=[])
        assert generate_queries(sector) == []

    def test_always_emits_one_overpass_query_per_country(self) -> None:
        sector = _sector(
            keywords=[_keyword("kasnak", "tr")],
            countries=[_country("TR"), _country("DE")],
        )
        queries = generate_queries(sector)
        overpass = [q for q in queries if q.source_hint == "overpass"]
        assert {q.country for q in overpass} == {"TR", "DE"}
        assert overpass[0].text == "overpass:TR" or overpass[1].text == "overpass:TR"

    def test_negative_keywords_are_excluded_from_query_generation(self) -> None:
        sector = _sector(
            keywords=[
                _keyword("kasnak", "tr"),
                _keyword("ikinci el", "tr", KeywordType.NEGATIVE),
            ],
            countries=[_country("TR")],
        )
        queries = generate_queries(sector)
        assert not any("ikinci el" in q.text for q in queries)
        assert any("kasnak" in q.text for q in queries)

    def test_keyword_plus_qualifier_combination_uses_serpapi(self) -> None:
        sector = _sector(
            keywords=[_keyword("kasnak", "tr")],
            countries=[_country("TR")],
        )
        queries = generate_queries(sector)
        serpapi = [q for q in queries if q.source_hint == "serpapi"]
        # _CONTACT_QUALIFIERS["tr"] = ["iletişim", "telefon", "whatsapp"]
        assert any(q.text == '"kasnak" "iletişim"' for q in serpapi)
        assert any(q.text == '"kasnak" "whatsapp"' for q in serpapi)

    def test_customer_type_plus_qualifier_combination(self) -> None:
        sector = _sector(
            keywords=[],
            target_customers=[_customer("elevator company", "en")],
            countries=[_country("GB")],
            default_language="en",
        )
        queries = generate_queries(sector)
        assert any(
            q.text == '"elevator company" "contact"' and q.source_hint == "serpapi"
            for q in queries
        )

    def test_raw_keyword_used_for_google_places(self) -> None:
        sector = _sector(keywords=[_keyword("kasnak", "tr")], countries=[_country("TR")])
        queries = generate_queries(sector)
        places = [q for q in queries if q.source_hint == "google_places"]
        assert any(q.text == "kasnak" for q in places)

    def test_country_tld_variant_only_for_known_countries(self) -> None:
        sector_known = _sector(keywords=[_keyword("kasnak", "tr")], countries=[_country("TR")])
        bing_known = [q for q in generate_queries(sector_known) if q.source_hint == "bing"]
        assert any(q.text == "kasnak site:.tr" for q in bing_known)

        sector_unknown = _sector(
            keywords=[_keyword("kasnak", "tr")], countries=[_country("XX")]
        )
        bing_unknown = [q for q in generate_queries(sector_unknown) if q.source_hint == "bing"]
        assert bing_unknown == []

    def test_deduplicates_identical_queries(self) -> None:
        sector = _sector(
            keywords=[_keyword("kasnak", "tr"), _keyword("kasnak", "tr")],
            countries=[_country("TR")],
        )
        queries = generate_queries(sector)
        places = [q for q in queries if q.source_hint == "google_places" and q.text == "kasnak"]
        assert len(places) == 1

    def test_per_country_limit_caps_non_overpass_queries(self) -> None:
        many_keywords = [_keyword(f"kw{i}", "tr") for i in range(20)]
        sector = _sector(keywords=many_keywords, countries=[_country("TR")])
        queries = generate_queries(sector, per_country_limit=5)
        non_overpass = [q for q in queries if q.source_hint != "overpass"]
        assert len(non_overpass) <= 5

    def test_overpass_query_exempt_from_per_country_limit(self) -> None:
        many_keywords = [_keyword(f"kw{i}", "tr") for i in range(20)]
        sector = _sector(keywords=many_keywords, countries=[_country("TR")])
        queries = generate_queries(sector, per_country_limit=1)
        overpass = [q for q in queries if q.source_hint == "overpass"]
        assert len(overpass) == 1

    def test_zero_limit_disables_capping(self) -> None:
        many_keywords = [_keyword(f"kw{i}", "tr") for i in range(10)]
        sector = _sector(keywords=many_keywords, countries=[_country("TR")])
        queries = generate_queries(sector, per_country_limit=0)
        places = [q for q in queries if q.source_hint == "google_places"]
        assert len(places) == 10

    def test_multiple_languages_each_get_their_own_qualifiers(self) -> None:
        sector = _sector(
            default_language="tr",
            keywords=[_keyword("kasnak", "tr"), _keyword("sheave", "en")],
            countries=[_country("TR")],
        )
        queries = generate_queries(sector)
        serpapi_texts = {q.text for q in queries if q.source_hint == "serpapi"}
        assert '"kasnak" "iletişim"' in serpapi_texts
        assert '"sheave" "contact"' in serpapi_texts
