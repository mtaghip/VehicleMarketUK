"""Persist RawListings into the database, track sold vehicles."""
import logging
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import Listing, PriceHistory, ScraperRun, Source
from .base import RawListing

logger = logging.getLogger(__name__)


async def upsert_listing(session: AsyncSession, raw: RawListing) -> tuple[str, Listing]:
    """
    Insert or update a listing. Returns ('new'|'updated'|'unchanged', listing).
    """
    source_enum = Source(raw.source)
    result = await session.execute(
        select(Listing).where(
            and_(
                Listing.source == source_enum,
                Listing.listing_id == raw.listing_id,
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        listing = Listing(
            listing_id=raw.listing_id,
            source=source_enum,
            url=raw.url,
            make=raw.make,
            model=raw.model,
            variant=raw.variant,
            year=raw.year,
            colour=raw.colour,
            mileage=raw.mileage,
            fuel_type=raw.fuel_type,
            transmission=raw.transmission,
            body_type=raw.body_type,
            engine_size=raw.engine_size,
            doors=raw.doors,
            reg_plate=raw.reg_plate,
            price=raw.price,
            original_price=raw.price,
            location=raw.location,
            postcode=raw.postcode,
            seller_type=raw.seller_type,
            images_count=raw.images_count,
            description=raw.description,
            first_seen=raw.scraped_at,
            last_seen=raw.scraped_at,
            is_active=True,
        )
        session.add(listing)
        await session.flush()
        if raw.price:
            session.add(PriceHistory(listing_id=listing.id, price=raw.price, recorded_at=raw.scraped_at))
        return "new", listing

    # Update existing
    changed = not existing.is_active or existing.sold_at is not None
    if raw.price and raw.price != existing.price:
        existing.price = raw.price
        session.add(PriceHistory(listing_id=existing.id, price=raw.price, recorded_at=raw.scraped_at))
        changed = True

    existing.last_seen = raw.scraped_at
    existing.is_active = True
    existing.sold_at = None
    existing.days_to_sell = None

    # Fill in missing fields if we now have them
    for attr in ("colour", "mileage", "variant", "location", "postcode", "reg_plate"):
        if getattr(raw, attr) and not getattr(existing, attr):
            setattr(existing, attr, getattr(raw, attr))
            changed = True

    return ("updated" if changed else "unchanged"), existing


async def mark_sold_listings(
    session: AsyncSession,
    source: str,
    seen_ids: set[str],
    cutoff_minutes: int = 120,
) -> int:
    """Compatibility guard: search absence is not evidence of a sale."""
    return 0


async def run_ingestion(
    session: AsyncSession,
    raw_listings: AsyncGenerator[RawListing, None],
    source: str,
) -> ScraperRun:
    """Full ingestion pipeline for one scraper run."""
    started_at = datetime.utcnow()
    run = ScraperRun(source=Source(source), started_at=started_at)
    session.add(run)
    await session.flush()  # Initialise SQLAlchemy counter defaults.

    seen_ids: set[str] = set()
    new_count = updated_count = 0

    try:
        async for raw in raw_listings:
            if raw.source != source:
                raise ValueError("Listing source does not match ingestion source")
            if raw.listing_id in seen_ids:
                continue
            status, _ = await upsert_listing(session, raw)
            seen_ids.add(raw.listing_id)
            if status == "new":
                new_count += 1
            elif status == "updated":
                updated_count += 1
            run.listings_found += 1

            if run.listings_found % 50 == 0:
                await session.flush()
                logger.info(f"{source}: {run.listings_found} listings processed...")

        if not seen_ids:
            raise ValueError("No listings collected; coverage could not be verified")
        previous = await session.scalar(
            select(ScraperRun).where(
                ScraperRun.source == Source(source),
                ScraperRun.success == True,
                ScraperRun.id != run.id,
            ).order_by(ScraperRun.started_at.desc()).limit(1)
        )
        if previous and previous.listings_found >= 20 and len(seen_ids) < previous.listings_found * 0.5:
            raise ValueError("Listing count dropped by more than 50%; coverage requires verification")
        sold_count = 0  # Partial discovery runs cannot establish sales.

        run.listings_new = new_count
        run.listings_updated = updated_count
        run.listings_sold = sold_count
        run.finished_at = datetime.utcnow()
        run.success = True
        await session.commit()
        logger.info(
            f"{source} run complete: {new_count} new, {updated_count} updated, {sold_count} sold"
        )

    except Exception as e:
        await session.rollback()
        # Rollback may expire the run; merge a fresh log without lazy IO.
        run = await session.merge(ScraperRun(
            source=Source(source), started_at=started_at,
            listings_found=len(seen_ids), listings_new=0,
            listings_updated=0, listings_sold=0,
        ))
        run.error = str(e)
        run.finished_at = datetime.utcnow()
        run.success = False
        await session.commit()
        logger.error(f"{source} ingestion error: {e}", exc_info=True)

    return run
