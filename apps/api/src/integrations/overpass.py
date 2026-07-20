"""OpenStreetMap Overpass API connector — free, no API key.

Returns real businesses tagged as elevator-related in OSM. Country-scoped
via the ISO3166-1 area filter; name-regex covers localized variants.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import structlog

from .base import RawLead
from .rate_limit import TokenBucket

logger = structlog.get_logger(__name__)

_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Country → OSM name regex (case-insensitive). Broad on purpose so we sweep
# maintenance firms, spare-parts suppliers, elevator manufacturers alike.
_NAME_REGEX: dict[str, str] = {
    "TR": "asans[öo]r|elevator|lift",
    "DE": "aufzug|fahrstuhl|elevator|lift",
    "GB": "elevator|lift service|lift company",
    "AE": "elevator|lift|مصعد|مصاعد",
    "SA": "elevator|lift|مصعد|مصاعد",
    "RU": "лифт|elevator|lift",
}


class OverpassConnector:
    name = "overpass"

    def __init__(self) -> None:
        # Very conservative — public shared service.
        self._bucket = TokenBucket("overpass", capacity=2, refill_per_sec=0.2)

    async def search(
        self, query: str, country: str, language: str
    ) -> AsyncIterator[RawLead]:
        wait = await self._bucket.acquire()
        if wait > 0:
            await asyncio.sleep(wait)

        regex = _NAME_REGEX.get(country, "elevator|lift")
        overpass_ql = f"""
        [out:json][timeout:25];
        area["ISO3166-1"="{country}"]->.a;
        (
          nwr["name"~"{regex}",i](area.a);
          nwr["craft"="elevator"](area.a);
          nwr["shop"="elevator"](area.a);
          nwr["office"~"elevator|lift",i](area.a);
        );
        out center tags 300;
        """.strip()

        data: dict | None = None
        # Try at most two mirrors, each tightly bounded, so a slow / over-capacity
        # Overpass server can't stall the whole discovery run.
        for endpoint in _ENDPOINTS[:2]:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(28.0, connect=8.0)
                ) as client:
                    resp = await client.post(
                        endpoint,
                        data={"data": overpass_ql},
                        headers={"User-Agent": "LeadPulseBot/1.0"},
                    )
                    if resp.status_code != 200:
                        logger.warning(
                            "overpass_error",
                            status=resp.status_code,
                            endpoint=endpoint,
                            body=resp.text[:200],
                        )
                        continue
                    data = resp.json()
                    break
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("overpass_http_error", endpoint=endpoint, error=str(e))
                continue

        if data is None:
            return

        seen: set[str] = set()
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            name = tags.get("name") or tags.get("operator") or tags.get("brand")
            if not name:
                continue
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)

            website = (
                tags.get("website")
                or tags.get("contact:website")
                or tags.get("url")
            )
            phone = tags.get("phone") or tags.get("contact:phone")
            city = (
                tags.get("addr:city")
                or tags.get("addr:province")
                or tags.get("addr:suburb")
            )
            street = tags.get("addr:street")
            housenumber = tags.get("addr:housenumber")
            postcode = tags.get("addr:postcode")
            addr_parts = [
                p for p in (street, housenumber, postcode, city) if p
            ]
            address = ", ".join(addr_parts) if addr_parts else None

            osm_type = el.get("type", "node")
            osm_id = el.get("id")
            source_url = (
                f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
                if osm_id
                else None
            )

            yield RawLead(
                company_name=name,
                source=self.name,
                source_url=source_url,
                website=website,
                country=country,
                city=city,
                address=address,
                phones=[phone] if phone else [],
                raw=tags,
            )
