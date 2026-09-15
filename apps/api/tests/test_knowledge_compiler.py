# ruff: noqa: RUF001
"""Pure tests for search-document compilation and query normalization."""

from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.knowledge.compiler import (
    compile_search_document,
    extract_codes,
    fact_codes,
    fts_query,
    index_fingerprint,
    normalize_text,
    query_tokens,
)


def _config() -> CompanyAgentConfig:
    return CompanyAgentConfig.model_validate(
        {
            "lifecycle": "approved",
            "organization": {"display_names": {"tr": "Örnek Şirket"}},
            "offerings": [
                {
                    "id": "premium-plan",
                    "kind": "subscription",
                    "display_names": {"tr": "Premium Plan"},
                    "interaction_labels": {"tr": "Premium"},
                }
            ],
            "facts": [
                {
                    "id": "premium-price",
                    "subject_id": "premium-plan",
                    "category": "commercial_rule",
                    "value": {"internal_price_cents": 99900},
                    "source": "internal-price-sheet-2026",
                    "customer_visible": True,
                    "customer_text": {"tr": "Premium Plan aylık 999 TL'dir. Model kodu TS160092."},
                    "search_terms": ["fiyat", "ücret"],
                    "selection_guidance": {"tr": "Fiyat sorulduğunda seç."},
                },
            ],
            "agent": {
                "purposes": ["information"],
                "supported_locales": ["tr"],
                "default_locale": "tr",
                "unknown_fact_action": "handoff",
            },
        }
    )


def test_normalize_text_handles_turkish_dotted_and_dotless_i() -> None:
    assert normalize_text("IŞIK İstanbul") == "ışık istanbul"


def test_query_tokens_drop_stopwords_and_punctuation() -> None:
    assert query_tokens("Kargolar ne zaman gelir? (acil)") == ["kargolar", "zaman", "gelir", "acil"]


def test_fts_query_is_alphanumeric_union_with_prefixes() -> None:
    query = fts_query('Kargolar "ne" zaman | gelir?')
    assert query == "kargolar|kargolar*|zaman|zaman*|gelir|gelir*"
    assert '"' not in query and "?" not in query


def test_fts_query_is_empty_for_stopwords_only() -> None:
    assert fts_query("ve bir bu") == ""


def test_extract_codes_finds_product_bearing_and_measure_codes() -> None:
    assert extract_codes("6211 rulman hangi mil çapına uygun?") == ("6211",)
    assert extract_codes("ts180118 kaç halat için?") == ("TS180118",)
    assert extract_codes("Döküm kasnakta 320 mm çap var mı? 6,5 mm halat") == ("320MM", "6.5MM")
    assert extract_codes("2,5 m/s hıza kadar") == ("2.5M/SN",)


def test_extract_codes_ignores_years_and_plain_words() -> None:
    assert extract_codes("2005 yılında kuruldu ve 2025 kataloğu") == ()
    assert extract_codes("merhaba nasılsınız") == ()


def test_search_document_contains_only_approved_material() -> None:
    config = _config()
    document = compile_search_document(config, config.facts[0])

    assert "Örnek Şirket" in document
    assert "Premium Plan (Premium)" in document
    assert "999 TL" in document
    assert "fiyat ücret" in document
    assert "Fiyat sorulduğunda seç." in document
    assert "commercial_rule" in document
    assert "internal_price_cents" not in document
    assert "99900" not in document
    assert "internal-price-sheet-2026" not in document


def test_fact_codes_come_from_customer_text_and_terms_only() -> None:
    config = _config()
    assert fact_codes(config, config.facts[0]) == ("TS160092",)


def test_index_fingerprint_is_order_independent_and_model_bound() -> None:
    pairs = [("a", "doc a"), ("b", "doc b")]
    assert index_fingerprint(pairs, "bge-m3") == index_fingerprint(list(reversed(pairs)), "bge-m3")
    assert index_fingerprint(pairs, "bge-m3") != index_fingerprint(pairs, "other-model")
    assert index_fingerprint(pairs, "bge-m3") != index_fingerprint([("a", "doc a")], "bge-m3")
