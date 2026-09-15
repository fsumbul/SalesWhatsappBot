# ruff: noqa: RUF001
"""Deterministic search documents, code extraction and query normalization.

Only approved, customer-facing material may enter a search document: company
and offering display names, the fact category, ``customer_text``, approved
``search_terms`` and ``selection_guidance``. Internal ``Fact.value``, sources
and admin notes never do (docs/multi-tenant-vector-retrieval-architecture.md §6.3).
"""

from __future__ import annotations

import hashlib
import re

from src.modules.agents.company_config import CompanyAgentConfig, Fact

_TURKISH_CASEFOLD = str.maketrans({"İ": "i", "I": "ı"})
_TOKEN_RE = re.compile(r"[0-9a-zçğıöşüâîû]+")
# Product/model codes such as TS160092, EC240068, DK240, BP110092 (typed in
# any case, no separators) and 62xx/63xx bearing designations (6208, 6311).
_MODEL_CODE_RE = re.compile(r"\b([a-z]{2,3})(\d{3,6})\b", re.IGNORECASE)
_BEARING_CODE_RE = re.compile(r"\b(6[23]\d{2})\b")
_MEASURE_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(mm|cm|m/sn|m/s|kg|mt)\b",
    re.IGNORECASE,
)
_MEASURE_UNITS = {"m/s": "m/sn", "mt": "m"}

_FTS_STOPWORDS = {
    "acaba",
    "ama",
    "ancak",
    "bana",
    "ben",
    "benim",
    "bir",
    "biraz",
    "bu",
    "bunu",
    "bunun",
    "çok",
    "da",
    "daha",
    "de",
    "değil",
    "diye",
    "en",
    "gibi",
    "hakkında",
    "hangi",
    "hem",
    "için",
    "ile",
    "ise",
    "istiyorum",
    "isterim",
    "kaç",
    "ki",
    "lütfen",
    "mi",
    "mı",
    "mu",
    "mü",
    "misin",
    "mısın",
    "musun",
    "müsün",
    "ne",
    "neden",
    "nedir",
    "nelerdir",
    "o",
    "olan",
    "olarak",
    "olur",
    "onu",
    "onun",
    "sen",
    "siz",
    "şu",
    "var",
    "ve",
    "veya",
    "ya",
    "yok",
    "zaten",
    "rica",
    "ederim",
    "merhaba",
    "selam",
}

_CATEGORY_GLOSS = {
    "capability": "yetenek kapasite özellik",
    "specification": "teknik özellik ölçü spesifikasyon",
    "commercial_rule": "ticari kural sipariş koşul",
    "availability": "stok mevcut bulunabilirlik",
    "eligibility": "uygunluk şart",
    "delivery": "teslimat kargo sevkiyat",
    "support": "destek iletişim yardım",
    "social": "sohbet davranış",
    "other": "",
}


def normalize_text(text: str) -> str:
    """Turkish-aware casefold (``I`` -> ``ı``, ``İ`` -> ``i``)."""

    return text.translate(_TURKISH_CASEFOLD).lower()


def query_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(normalize_text(text)):
        if len(token) < 2 or token in _FTS_STOPWORDS or token in tokens:
            continue
        tokens.append(token)
    return tokens


def fts_query(text: str, *, prefix_min_length: int = 5, max_terms: int = 12) -> str:
    """Build a RediSearch union query (``a|a*|b``) from a customer message.

    Tokens are alphanumeric only, so no RediSearch syntax can be injected.
    Prefix variants help Turkish agglutination beyond what stemming covers.
    """

    parts: list[str] = []
    for token in query_tokens(text)[:max_terms]:
        parts.append(token)
        if len(token) >= prefix_min_length and not token.isdigit():
            parts.append(f"{token}*")
    return "|".join(parts)


def extract_codes(text: str) -> tuple[str, ...]:
    """Exact-match keys: product codes, bearing numbers and unit measures."""

    codes: list[str] = []

    def _add(code: str) -> None:
        if code and code not in codes:
            codes.append(code)

    for match in _MODEL_CODE_RE.finditer(text):
        _add(f"{match.group(1).upper()}{match.group(2)}")
    for match in _BEARING_CODE_RE.finditer(text):
        _add(match.group(1))
    for match in _MEASURE_RE.finditer(text):
        value = match.group(1).replace(",", ".")
        unit = match.group(2).lower()
        unit = _MEASURE_UNITS.get(unit, unit)
        _add(f"{value}{unit}".upper())
    return tuple(codes)


def _localized(texts: dict[str, str] | None, locale: str) -> str:
    if not texts:
        return ""
    if locale in texts:
        return texts[locale]
    short = locale.split("-")[0]
    for key, value in texts.items():
        if key.split("-")[0] == short:
            return value
    return next(iter(texts.values()), "")


def subject_label(config: CompanyAgentConfig, subject_id: str, locale: str) -> str:
    if config.organization is not None and subject_id == config.organization.id:
        return _localized(config.organization.display_names, locale)
    for offering in config.offerings:
        if offering.id == subject_id:
            label = _localized(offering.display_names, locale)
            if offering.interaction_labels:
                interaction = _localized(offering.interaction_labels, locale)
                if interaction and interaction != label:
                    label = f"{label} ({interaction})"
            return label
    for party in config.parties:
        if party.id == subject_id:
            return _localized(party.display_names, locale)
    return subject_id.replace("_", " ")


def compile_search_document(config: CompanyAgentConfig, fact: Fact) -> str:
    """The only text that is embedded, full-text indexed and reranked."""

    assert config.agent is not None
    locale = config.agent.default_locale
    company = _localized(config.organization.display_names, locale) if config.organization else ""
    lines = [
        company,
        subject_label(config, fact.subject_id, locale),
        f"{fact.category.value} {_CATEGORY_GLOSS.get(fact.category.value, '')}".strip(),
        _localized(fact.customer_text, locale),
        " ".join(fact.search_terms),
        _localized(fact.selection_guidance, locale) if fact.selection_guidance else "",
    ]
    return "\n".join(line.strip() for line in lines if line and line.strip())


def fact_codes(config: CompanyAgentConfig, fact: Fact) -> tuple[str, ...]:
    assert config.agent is not None
    locale = config.agent.default_locale
    haystack = " ".join(
        [
            fact.id.replace("_", " ").replace("-", " "),
            _localized(fact.customer_text, locale),
            " ".join(fact.search_terms),
        ]
    )
    return extract_codes(haystack)


def content_hash(document: str, embedding_model: str) -> str:
    digest = hashlib.sha256()
    digest.update(embedding_model.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(document.encode("utf-8"))
    return digest.hexdigest()


def index_fingerprint(documents: list[tuple[str, str]], embedding_model: str) -> str:
    """Stable hash of (fact_id, search_document) pairs for idempotent reindex."""

    digest = hashlib.sha256()
    digest.update(embedding_model.encode("utf-8"))
    for fact_id, document in sorted(documents):
        digest.update(b"\x00")
        digest.update(fact_id.encode("utf-8"))
        digest.update(b"\x01")
        digest.update(document.encode("utf-8"))
    return digest.hexdigest()
