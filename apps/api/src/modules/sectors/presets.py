"""Preset sector: Elevator sheave (asansör kasnağı) — 5 languages."""

from __future__ import annotations

from src.modules.sectors.models import KeywordType
from src.modules.sectors.schemas import (
    SectorCountryIn,
    SectorExport,
    SectorIn,
    SectorKeywordIn,
    SectorMessageAngleIn,
    SectorTargetCustomerIn,
)

ELEVATOR_SHEAVE_PRESET = SectorExport(
    sector=SectorIn(
        name="Asansör Kasnağı",
        slug="elevator-sheave",
        product_name="Asansör kasnağı / traction sheave / lift pulley",
        description=(
            "Asansör bakım firmaları, üreticiler ve yedek parça tedarikçilerine "
            "yönelik özel ölçü kasnak ve makara üretimi."
        ),
        default_language="tr",
    ),
    keywords=[
        # TR
        SectorKeywordIn(keyword="asansör bakım", language="tr"),
        SectorKeywordIn(keyword="asansör servisi", language="tr"),
        SectorKeywordIn(keyword="asansör firması", language="tr"),
        SectorKeywordIn(keyword="asansör yedek parça", language="tr"),
        SectorKeywordIn(keyword="asansör kasnağı", language="tr"),
        SectorKeywordIn(keyword="ev asansörü", language="tr", keyword_type=KeywordType.NEGATIVE),
        SectorKeywordIn(keyword="ikinci el asansör", language="tr", keyword_type=KeywordType.NEGATIVE),
        # EN
        SectorKeywordIn(keyword="elevator maintenance", language="en"),
        SectorKeywordIn(keyword="elevator company", language="en"),
        SectorKeywordIn(keyword="elevator spare parts", language="en"),
        SectorKeywordIn(keyword="traction sheave", language="en"),
        SectorKeywordIn(keyword="lift maintenance", language="en"),
        SectorKeywordIn(keyword="elevator sheave supplier", language="en"),
        # DE
        SectorKeywordIn(keyword="Aufzug Wartung", language="de"),
        SectorKeywordIn(keyword="Aufzug Ersatzteile", language="de"),
        SectorKeywordIn(keyword="Seilrolle Aufzug", language="de"),
        SectorKeywordIn(keyword="Aufzugbau", language="de"),
        # AR
        SectorKeywordIn(keyword="صيانة المصاعد", language="ar"),
        SectorKeywordIn(keyword="قطع غيار المصاعد", language="ar"),
        SectorKeywordIn(keyword="مصنع مصاعد", language="ar"),
        # RU
        SectorKeywordIn(keyword="обслуживание лифтов", language="ru"),
        SectorKeywordIn(keyword="запчасти для лифтов", language="ru"),
        SectorKeywordIn(keyword="лифтовая компания", language="ru"),
    ],
    target_customers=[
        SectorTargetCustomerIn(customer_type="Asansör bakım firması", language="tr"),
        SectorTargetCustomerIn(customer_type="Asansör montaj firması", language="tr"),
        SectorTargetCustomerIn(customer_type="Asansör yedek parça tedarikçisi", language="tr"),
        SectorTargetCustomerIn(customer_type="Asansör üreticisi", language="tr"),
        SectorTargetCustomerIn(customer_type="Elevator maintenance company", language="en"),
        SectorTargetCustomerIn(customer_type="Elevator spare parts supplier", language="en"),
        SectorTargetCustomerIn(customer_type="Elevator manufacturer", language="en"),
        SectorTargetCustomerIn(customer_type="Aufzug-Wartungsunternehmen", language="de"),
        SectorTargetCustomerIn(customer_type="شركة صيانة مصاعد", language="ar"),
        SectorTargetCustomerIn(customer_type="Лифтовая обслуживающая компания", language="ru"),
    ],
    countries=[
        SectorCountryIn(country_code="TR", timezone="Europe/Istanbul", priority=100),
        SectorCountryIn(country_code="DE", timezone="Europe/Berlin", priority=90),
        SectorCountryIn(country_code="GB", timezone="Europe/London", priority=80),
        SectorCountryIn(country_code="AE", timezone="Asia/Dubai", priority=85),
        SectorCountryIn(country_code="SA", timezone="Asia/Riyadh", priority=80),
        SectorCountryIn(country_code="RU", timezone="Europe/Moscow", priority=70),
    ],
    message_angles=[
        SectorMessageAngleIn(angle="Özel ölçü üretim ve hızlı teslim", language="tr"),
        SectorMessageAngleIn(angle="İhracat referansları", language="tr"),
        SectorMessageAngleIn(angle="Custom manufacturing & fast delivery", language="en"),
        SectorMessageAngleIn(angle="Export references worldwide", language="en"),
    ],
)

ALL_PRESETS = {"elevator-sheave": ELEVATOR_SHEAVE_PRESET}
