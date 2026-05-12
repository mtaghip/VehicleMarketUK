"""Live dashboard stats endpoint — polled every 30s by the frontend."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, Listing, ScraperRun, DealerListing, MonitoredDealer

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/stats")
async def live_stats(session: AsyncSession = Depends(get_db)):
    """Single endpoint returning all headline numbers for the dashboard."""
    now = datetime.utcnow()
    h24 = now - timedelta(hours=24)
    d7 = now - timedelta(days=7)

    # Active listings total
    active_count = await session.scalar(
        select(func.count()).where(Listing.is_active == True)
    ) or 0

    # New in last 24h
    new_24h = await session.scalar(
        select(func.count()).where(
            and_(Listing.is_active == True, Listing.first_seen >= h24)
        )
    ) or 0

    # Sold in last 7 days
    sold_7d = await session.scalar(
        select(func.count()).where(
            and_(Listing.is_active == False, Listing.sold_at >= d7)
        )
    ) or 0

    # Average days to sell (from sold listings, last 30 days)
    d30 = now - timedelta(days=30)
    avg_dts_result = await session.execute(
        select(func.avg(Listing.days_to_sell)).where(
            and_(
                Listing.days_to_sell.isnot(None),
                Listing.sold_at >= d30,
            )
        )
    )
    avg_dts = avg_dts_result.scalar()

    # Last scraper run per source
    at_run = await session.execute(
        select(ScraperRun).where(ScraperRun.source == "autotrader")
        .order_by(ScraperRun.started_at.desc()).limit(1)
    )
    at_run = at_run.scalar_one_or_none()

    cc_run = await session.execute(
        select(ScraperRun).where(ScraperRun.source == "carandclassic")
        .order_by(ScraperRun.started_at.desc()).limit(1)
    )
    cc_run = cc_run.scalar_one_or_none()

    # Dealer summary
    dealer_count = await session.scalar(
        select(func.count()).where(MonitoredDealer.is_active == True)
    ) or 0

    dealer_sold_7d = await session.scalar(
        select(func.count()).where(
            and_(
                DealerListing.is_active == False,
                DealerListing.sold_at >= d7,
            )
        )
    ) or 0

    return {
        "timestamp": now.isoformat(),
        "active_listings": active_count,
        "new_last_24h": new_24h,
        "sold_last_7d": sold_7d,
        "avg_days_to_sell": round(avg_dts, 1) if avg_dts else None,
        "scrapers": {
            "autotrader": _ser_run(at_run),
            "carandclassic": _ser_run(cc_run),
        },
        "dealers": {
            "monitored": dealer_count,
            "sold_last_7d": dealer_sold_7d,
        },
    }


@router.get("/activity")
async def recent_activity(session: AsyncSession = Depends(get_db)):
    """Last 20 events: new listings, sold listings, price drops."""
    now = datetime.utcnow()
    h48 = now - timedelta(hours=48)

    # Recently added
    new_result = await session.execute(
        select(Listing).where(
            and_(Listing.is_active == True, Listing.first_seen >= h48)
        ).order_by(Listing.first_seen.desc()).limit(10)
    )
    new_listings = new_result.scalars().all()

    # Recently sold
    sold_result = await session.execute(
        select(Listing).where(
            and_(Listing.is_active == False, Listing.sold_at >= h48)
        ).order_by(Listing.sold_at.desc()).limit(10)
    )
    sold_listings = sold_result.scalars().all()

    events = []
    for l in new_listings:
        events.append({
            "type": "new",
            "time": l.first_seen.isoformat(),
            "make": l.make,
            "model": l.model,
            "variant": l.variant,
            "year": l.year,
            "price": l.price,
            "colour": l.colour,
            "mileage": l.mileage,
            "fuel_type": l.fuel_type,
            "transmission": l.transmission,
            "reg_plate": l.reg_plate,
            "seller_type": l.seller_type,
            "location": l.location,
            "source": l.source.value if l.source else None,
            "url": l.url,
        })
    for l in sold_listings:
        events.append({
            "type": "sold",
            "time": l.sold_at.isoformat(),
            "make": l.make,
            "model": l.model,
            "variant": l.variant,
            "year": l.year,
            "price": l.price,
            "colour": l.colour,
            "mileage": l.mileage,
            "fuel_type": l.fuel_type,
            "transmission": l.transmission,
            "reg_plate": l.reg_plate,
            "seller_type": l.seller_type,
            "location": l.location,
            "days_to_sell": l.days_to_sell,
            "source": l.source.value if l.source else None,
            "url": l.url,
        })

    events.sort(key=lambda e: e["time"], reverse=True)
    return events[:30]


def _ser_run(r) -> dict:
    if not r:
        return {"last_run": None, "status": "never"}
    return {
        "last_run": r.started_at.isoformat() if r.started_at else None,
        "listings_new": r.listings_new,
        "listings_sold": r.listings_sold,
        "success": r.success,
    }
