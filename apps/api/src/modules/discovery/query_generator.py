"""Query generator — produces search queries from a sector profile."""

# Turkish comments below describe Turkish-market query logic; not typos.
# ruff: noqa: RUF003

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from src.modules.sectors.models import KeywordType, Sector

_CONTACT_QUALIFIERS: dict[str, list[str]] = {
    "tr": ["iletişim", "telefon", "whatsapp"],
    "en": ["contact", "phone", "whatsapp"],
    "de": ["Kontakt", "Telefon"],
    "ar": ["اتصل بنا", "هاتف"],
    "ru": ["контакты", "телефон"],
}

_COUNTRY_TLD: dict[str, str] = {
    "TR": ".tr",
    "DE": ".de",
    "GB": ".uk",
    "AE": ".ae",
    "SA": ".sa",
    "RU": ".ru",
}


@dataclass(frozen=True)
class SearchQuery:
    text: str
    language: str
    country: str
    source_hint: str  # "google_places" | "serpapi" | "bing"


def generate_queries(sector: Sector, *, per_country_limit: int = 20) -> list[SearchQuery]:
    """Generate search queries by combining keywords x target customers x contact qualifiers."""
    positive_kw = [k for k in sector.keywords if k.keyword_type == KeywordType.POSITIVE]
    customers = sector.target_customers
    countries = sector.countries

    queries: list[SearchQuery] = []

    for country in countries:
        # Overpass: one country-scoped call — the connector ignores query text
        # and uses its own OSM tag/name-regex filter per country.
        queries.append(
            SearchQuery(
                text=f"overpass:{country.country_code}",
                language=sector.default_language,
                country=country.country_code,
                source_hint="overpass",
            )
        )
        for lang in {sector.default_language} | {k.language for k in positive_kw}:
            lang_kws = [k.keyword for k in positive_kw if k.language == lang]
            lang_customers = [c.customer_type for c in customers if c.language == lang]
            qualifiers = _CONTACT_QUALIFIERS.get(lang, ["contact"])

            # Combination 1: keyword + qualifier
            for kw, q in product(lang_kws, qualifiers):
                queries.append(
                    SearchQuery(
                        text=f'"{kw}" "{q}"',
                        language=lang,
                        country=country.country_code,
                        source_hint="serpapi",
                    )
                )
            # Combination 2: customer_type + qualifier
            for ct, q in product(lang_customers, qualifiers):
                queries.append(
                    SearchQuery(
                        text=f'"{ct}" "{q}"',
                        language=lang,
                        country=country.country_code,
                        source_hint="serpapi",
                    )
                )
            # Places: raw keyword works best
            for kw in lang_kws:
                queries.append(
                    SearchQuery(
                        text=kw,
                        language=lang,
                        country=country.country_code,
                        source_hint="google_places",
                    )
                )
            # Country TLD-restricted variant for Serp/Bing
            tld = _COUNTRY_TLD.get(country.country_code)
            if tld:
                for kw in lang_kws:
                    queries.append(
                        SearchQuery(
                            text=f'{kw} site:{tld}',
                            language=lang,
                            country=country.country_code,
                            source_hint="bing",
                        )
                    )

    # Deduplicate & cap per country
    seen: set[tuple[str, str, str]] = set()
    per_country_count: dict[str, int] = {}
    result: list[SearchQuery] = []
    for q in queries:
        key = (q.text, q.language, q.country)
        if key in seen:
            continue
        # Overpass sorgusunu limitten muaf tut (ülke başına zaten 1 adet)
        if (
            per_country_limit
            and q.source_hint != "overpass"
            and per_country_count.get(q.country, 0) >= per_country_limit
        ):
            continue
        seen.add(key)
        result.append(q)
        per_country_count[q.country] = per_country_count.get(q.country, 0) + 1
    return result
