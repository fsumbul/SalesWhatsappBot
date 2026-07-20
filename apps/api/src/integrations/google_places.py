"""Google Places (New) Text Search connector."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import structlog

from src.core.config import get_settings

from .base import RawLead
from .rate_limit import TokenBucket

logger = structlog.get_logger(__name__)

_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.internationalPhoneNumber,"
    "places.websiteUri,places.addressComponents,places.id"
)


class GooglePlacesConnector:
    name = "google_places"

    def __init__(self) -> None:
        self.api_key = get_settings().google_places_api_key
        self._bucket = TokenBucket("google_places", capacity=10, refill_per_sec=1.0)

    async def search(
        self, query: str, country: str, language: str
    ) -> AsyncIterator[RawLead]:
        if not self.api_key:
            logger.warning("google_places_no_api_key")
            return
        wait = await self._bucket.acquire()
        if wait > 0:
            await asyncio.sleep(wait)

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        body = {
            "textQuery": query,
            "languageCode": language,
            "regionCode": country,
            "maxResultCount": 20,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(_ENDPOINT, headers=headers, json=body)
                if resp.status_code != 200:
                    logger.warning(
                        "google_places_error", status=resp.status_code, body=resp.text[:200]
                    )
                    return
                data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("google_places_http_error", error=str(e))
            return

        for place in data.get("places", []):
            city = _find_city(place.get("addressComponents", []))
            display = place.get("displayName", {}).get("text") or "Unknown"
            phone = place.get("internationalPhoneNumber")
            yield RawLead(
                company_name=display,
                source=self.name,
                source_url=f"https://www.google.com/maps/place/?q=place_id:{place.get('id', '')}",
                website=place.get("websiteUri"),
                country=country,
                city=city,
                address=place.get("formattedAddress"),
                phones=[phone] if phone else [],
                raw=place,
            )


def _find_city(components: list[dict]) -> str | None:
    for c in components:
        types = c.get("types", [])
        if "locality" in types or "administrative_area_level_1" in types:
            return c.get("longText") or c.get("shortText")
    return None
