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
            owners_count=raw.owners_count,
            service_history=raw.service_history or None,
            ulez_compliant=raw.ulez_compliant,
            euro_standard=raw.euro_standard or None,
            cat_marker=raw.cat_marker or None,
            vat_qualifying=raw.vat_qualifying,
            at_retail_rating=raw.at_retail_rating,
        )
        session.add(listing)
        await session.flush()
        if raw.price:
            session.add(PriceHistory(listing_id=listing.id, price=raw.price, recorded_at=raw.scraped_at))
        return "new", listing

    # Update existing
    changed = False
    if raw.price and raw.price != existing.price:
        existing.price = raw.price
        session.add(PriceHistory(listing_id=existing.id, price=raw.price, recorded_at=raw.scraped_at))
        changed = True

    existing.last_seen = raw.scraped_at
    existing.is_active = True

    # Fill in missing fields if we now have them
    for attr in ("colour", "mileage", "variant", "location", "postcode", "reg_plate",
                 "owners_count", "service_history", "ulez_compliant", "euro_standard",
                 "cat_marker", "vat_qualifying", "at_retail_rating"):
        raw_val = getattr(raw, attr, None)
        if raw_val is not None and raw_val != "" and getattr(existing, attr) is None:
            setattr(existing, attr, raw_val)
            changed = True

    return ("updated" if changed else "unchanged"), existing


async def mark_sold_listings(
    session: AsyncSession,
    source: str,
    seen_ids: set[str],
    cutoff_minutes: int = 120,
) -> int:
    """
    Any listing from `source` that was NOT in this scrape run and hasn't been
    seen recently is marked as sold. Returns count of newly-sold listings.
    """
    from datetime import timedelta

    source_enum = Source(source)
    cutoff = datetime.utcnow() - timedelta(minutes=cutoff_minutes)

    result = await session.execute(
        select(Listing).where(
            and_(
                Listing.source == source_enum,
                Listing.is_active == True,
                Listing.last_seen < cutoff,
            )
        )
    )
    stale = result.scalars().all()
    sold_count = 0
    now = datetime.utcnow()

    for listing in stale:
        if listing.listing_id not in seen_ids:
            listing.is_active = False
            listing.sold_at = now
            if listing.first_seen:
                delta = now - listing.first_seen
                listing.days_to_sell = delta.days
            sold_count += 1
            logger.info(
                f"Marked sold: {listing.make} {listing.model} {listing.year} "
                f"£{listing.price} (was live {listing.days_to_sell}d)"
            )

    return sold_count


async def run_ingestion(
    session: AsyncSession,
    raw_listings: AsyncGenerator[RawListing, None],
    source: str,
) -> ScraperRun:
    """Full ingestion pipeline for one scraper run."""
    run = ScraperRun(source=Source(source), started_at=datetime.utcnow())
    session.add(run)

    seen_ids: set[str] = set()
    new_count = updated_count = 0

    try:
        async for raw in raw_listings:
            status, _ = await upsert_listing(session, raw)
            seen_ids.add(raw.listing_id)
            if status == "new":
                new_count += 1
            elif status == "updated":
                updated_count += 1
            run.listings_found += 1

            if run.listings_found % 50 == 0:
                await session.commit()
                logger.info(f"{source}: {run.listings_found} listings processed...")

        sold_count = await mark_sold_listings(session, source, seen_ids)

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
        run.error = str(e)
        run.finished_at = datetime.utcnow()
        run.success = False
        await session.commit()
        logger.error(f"{source} ingestion error: {e}", exc_info=True)

    return run
