"""Unit tests for enrichment worker helpers: phone normalization and sector
fit scoring (no DB required — Sector/SectorKeyword are constructed in-memory,
never flushed to a session)."""

# Turkish sector keywords are the actual thing under test here, so this file
# is exempt from ruff's ambiguous-unicode check (RUF001/RUF003).
# ruff: noqa: RUF001, RUF003

from types import SimpleNamespace

from src.modules.sectors.models import KeywordType
from src.workers.enrichment import _normalize_phone, _score_fit


def _kw(keyword: str, keyword_type: KeywordType = KeywordType.POSITIVE) -> SimpleNamespace:
    return SimpleNamespace(keyword=keyword, keyword_type=keyword_type)


def _sector(*keywords: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(keywords=list(keywords))


class TestNormalizePhone:
    def test_valid_turkish_mobile_returns_e164(self) -> None:
        e164, country = _normalize_phone("0532 123 45 67", "TR")
        assert e164 == "+905321234567"
        assert country == "TR"

    def test_already_e164_formatted(self) -> None:
        e164, _ = _normalize_phone("+905321234567", None)
        assert e164 == "+905321234567"

    def test_invalid_number_returns_none(self) -> None:
        e164, country = _normalize_phone("123", "TR")
        assert e164 is None
        assert country is None

    def test_unparseable_garbage_returns_none_not_raises(self) -> None:
        e164, country = _normalize_phone("not-a-phone-number-at-all", None)
        assert e164 is None
        assert country is None

    def test_german_number_with_country_hint(self) -> None:
        e164, country = _normalize_phone("030 12345678", "DE")
        assert e164 is not None
        assert country == "DE"


class TestScoreFit:
    def test_no_sector_scores_zero(self) -> None:
        assert _score_fit("asansör kasnağı satışı", None) == 0

    def test_no_text_scores_zero(self) -> None:
        assert _score_fit("", _sector(_kw("kasnak"))) == 0

    def test_text_without_anchor_stem_scores_zero(self) -> None:
        # "kasnak" alone (no anchor like asans/kasna/elevator/...) never matches.
        sector = _sector(_kw("pulley"))
        assert _score_fit("we sell pulleys and gears", sector) == 0

    def test_matching_positive_keyword_with_anchor_scores_positive(self) -> None:
        sector = _sector(_kw("asansör kasnağı"))
        score = _score_fit("firmamiz asansör kasnağı üretmektedir", sector)
        assert score == 20

    def test_multiple_positive_matches_accumulate(self) -> None:
        sector = _sector(_kw("asansör kasnağı"), _kw("elevator sheave"))
        text = "we manufacture elevator sheave and asansör kasnağı products"
        score = _score_fit(text, sector)
        assert score == 40

    def test_negative_keyword_penalizes(self) -> None:
        sector = _sector(
            _kw("asansör kasnağı"),
            _kw("ikinci el asansör", KeywordType.NEGATIVE),
        )
        text = "ikinci el asansör kasnağı satılık"
        score = _score_fit(text, sector)
        # matched=1 (+20), negative=1 (-25) -> raw -5, clamped to 0
        assert score == 0

    def test_score_never_exceeds_100(self) -> None:
        keywords = [_kw(f"asansör kasnağı {i}") for i in range(10)]
        sector = _sector(*keywords)
        text = " ".join(kw.keyword for kw in keywords)
        assert _score_fit(text, sector) == 100

    def test_noncommercial_host_scores_zero_even_with_matches(self) -> None:
        sector = _sector(_kw("asansör kasnağı"))
        text = "asansör kasnağı definition and history"
        assert _score_fit(text, sector, website="tureng.com") == 0

    def test_case_insensitive_matching(self) -> None:
        sector = _sector(_kw("Elevator Sheave"))
        score = _score_fit("We sell ELEVATOR SHEAVE products", sector)
        assert score == 20
