"""One-off discovery booster.

Runs a curated list of high-yield, real-world queries through the Bing
connector and ingests the results into an existing campaign using the normal
DiscoveryService dedup path. Home market (TR) is weighted heavily; a few DE/GB
queries broaden the demo. This bypasses the auto query-generator (whose
double-quoted phrase queries are too restrictive for the free SERP scraper).
"""

# The query list below is real Turkish sector-keyword text — not typos.
# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

# Make `src` importable regardless of CWD.
API_DIR = Path(__file__).resolve().parents[0]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from src.core.db import get_sessionmaker, set_tenant_context  # noqa: E402
from src.integrations.base import RawLead  # noqa: E402
from src.integrations.bing import BingSearchConnector  # noqa: E402
from src.modules.discovery.service import DiscoveryService  # noqa: E402

TENANT_ID = UUID("f40cfc51-c496-4049-bf3f-8bcbbd98436e")
CAMPAIGN_ID = UUID("23291051-54a0-4ad7-9c0a-9e8453042562")
SECTOR_ID = UUID("a1a6f766-190f-4a1f-bd78-ab8097b17e75")

# (query, country, language)
TR_CITIES = [
    "istanbul", "ankara", "izmir", "bursa", "antalya", "konya",
    "adana", "gaziantep", "kayseri", "kocaeli",
]
QUERIES: list[tuple[str, str, str]] = []
for c in TR_CITIES:
    QUERIES.append((f"asansör firması {c}", "TR", "tr"))
    QUERIES.append((f"asansör servisi {c}", "TR", "tr"))
QUERIES += [
    ("asansör kasnağı imalatı", "TR", "tr"),
    ("asansör kasnağı üretimi", "TR", "tr"),
    ("asansör saptırma kasnağı", "TR", "tr"),
    ("asansör makine motoru üretici", "TR", "tr"),
    ("asansör yedek parça firması", "TR", "tr"),
    ("asansör bakım firması", "TR", "tr"),
    ("asansör montaj firması", "TR", "tr"),
    ("asansör imalatçısı", "TR", "tr"),
    ("traksiyon makinesi asansör üretici", "TR", "tr"),
    # Germany
    ("Aufzug Firma Wartung", "DE", "de"),
    ("Aufzug Service Unternehmen", "DE", "de"),
    ("Treibscheibe Aufzug Hersteller", "DE", "de"),
    ("Aufzugsbau Unternehmen", "DE", "de"),
    # UK
    ("elevator company manufacturer", "GB", "en"),
    ("lift maintenance company", "GB", "en"),
    ("elevator sheave supplier", "GB", "en"),
]


async def main() -> None:
    conn = BingSearchConnector()
    raw: list[RawLead] = []
    for i, (q, country, lang) in enumerate(QUERIES, 1):
        got = 0
        try:
            async for rl in conn.search(q, country, lang):
                raw.append(rl)
                got += 1
        except Exception as e:
            print(f"[{i}/{len(QUERIES)}] FAIL {q!r}: {e}")
            continue
        print(f"[{i}/{len(QUERIES)}] {q!r} ({country}) -> {got}")

    print(f"\nCollected {len(raw)} raw leads; ingesting...")
    sm = get_sessionmaker()
    async with sm() as session:
        await set_tenant_context(session, TENANT_ID)
        svc = DiscoveryService(session)
        inserted = await svc.ingest_raw_leads(TENANT_ID, CAMPAIGN_ID, SECTOR_ID, raw)
    print(f"Inserted {inserted} NEW leads (rest were dedup merges).")


if __name__ == "__main__":
    asyncio.run(main())
