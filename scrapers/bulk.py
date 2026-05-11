"""
Bulk scraper — iterates through every major UK car make to collect all live
listings rather than just the most-recently-added ones.

Regular hourly scrape: sort=date-desc, ~10 pages → catches new arrivals.
Bulk scrape (weekly):  iterates ~50 makes × up to 50 pages each → full market.
"""
import asyncio
import logging
from typing import AsyncGenerator

from database import AsyncSessionLocal
from .autotrader import AutoTraderScraper
from .carandclassic import CarAndClassicScraper
from .base import RawListing
from .ingestion import upsert_listing

logger = logging.getLogger(__name__)

# All makes worth covering for a UK market overview.
UK_MAKES = [
    "Abarth", "Alfa Romeo", "Aston Martin", "Audi", "Bentley", "BMW",
    "Citroen", "Cupra", "Dacia", "DS", "Ferrari", "Fiat", "Ford",
    "Honda", "Hyundai", "Jaguar", "Jeep", "Kia", "Lamborghini",
    "Land Rover", "Lexus", "Lotus", "Maserati", "Mazda", "McLaren",
    "Mercedes-Benz", "MG", "MINI", "Mitsubishi", "Nissan", "Peugeot",
    "Porsche", "Renault", "Rolls-Royce", "SEAT", "Skoda", "Smart",
    "Subaru", "Suzuki", "Tesla", "Toyota", "Vauxhall", "Volkswagen",
    "Volvo", "Alfa Romeo",
]
UK_MAKES = list(dict.fromkeys(UK_MAKES))  # deduplicate while preserving order


async def _ingest_stream(
    stream: AsyncGenerator[RawListing, None],
    source: str,
) -> int:
    """Upsert every listing from the stream. Returns total count saved."""
    count = 0
    async with AsyncSessionLocal() as session:
        async for raw in stream:
            try:
                await upsert_listing(session, raw)
                count += 1
                if count % 100 == 0:
                    await session.commit()
                    logger.info(f"Bulk {source}: {count} listings saved so far...")
            except Exception as e:
                logger.debug(f"Bulk ingest error ({source}): {e}")
        await session.commit()
    return count


async def run_bulk_autotrader(pages_per_make: int = 50) -> dict:
    """Scrape all makes on AutoTrader. Returns summary dict."""
    total = 0
    errors = []
    scraper = AutoTraderScraper()
    for make in UK_MAKES:
        try:
            logger.info(f"Bulk AT: scraping make '{make}'...")
            count = await _ingest_stream(
                scraper.search(make=make, max_pages=pages_per_make),
                "autotrader",
            )
            total += count
            logger.info(f"Bulk AT: '{make}' → {count} listings (running total: {total})")
            await asyncio.sleep(5)  # polite pause between makes
        except Exception as e:
            logger.error(f"Bulk AT: error on '{make}': {e}")
            errors.append({"make": make, "error": str(e)})

    return {"source": "autotrader", "total": total, "errors": errors}


async def run_bulk_carandclassic(pages_per_make: int = 50) -> dict:
    """Scrape all makes on Car & Classic. Returns summary dict."""
    total = 0
    errors = []
    scraper = CarAndClassicScraper()
    for make in UK_MAKES:
        try:
            logger.info(f"Bulk C&C: scraping make '{make}'...")
            count = await _ingest_stream(
                scraper.search(make=make, max_pages=pages_per_make),
                "carandclassic",
            )
            total += count
            logger.info(f"Bulk C&C: '{make}' → {count} listings (running total: {total})")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Bulk C&C: error on '{make}': {e}")
            errors.append({"make": make, "error": str(e)})

    return {"source": "carandclassic", "total": total, "errors": errors}


async def run_full_bulk_scrape(pages_per_make: int = 50) -> dict:
    """Run both scrapers sequentially. Used by the weekly scheduler job."""
    logger.info(f"=== Full bulk scrape starting: {len(UK_MAKES)} makes × up to {pages_per_make} pages ===")
    at_result = await run_bulk_autotrader(pages_per_make)
    cc_result = await run_bulk_carandclassic(pages_per_make)
    total = at_result["total"] + cc_result["total"]
    logger.info(f"=== Full bulk scrape complete: {total} total listings ===")
    return {"autotrader": at_result, "carandclassic": cc_result, "grand_total": total}
