"""Database usage / data-presence verification endpoint."""
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import DATA_DIR
from database import get_db
from database.models import (
    DealerListing, DemandSnapshot, Listing,
    MonitoredDealer, PriceAlert, PriceHistory, ScraperRun, ValuationCache,
)

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/")
async def usage(session: AsyncSession = Depends(get_db)):
    """
    Returns row counts for every table plus the last scraper run,
    so you can verify data is present on Railway at a glance.
    """

    async def count(model) -> int:
        result = await session.execute(select(func.count()).select_from(model))
        return result.scalar_one()

    listings_total = await count(Listing)
    listings_active = (
        await session.execute(
            select(func.count()).select_from(Listing).where(Listing.is_active == True)
        )
    ).scalar_one()
    listings_sold = (
        await session.execute(
            select(func.count()).select_from(Listing).where(Listing.sold_at.isnot(None))
        )
    ).scalar_one()

    last_run_row = (
        await session.execute(
            select(ScraperRun).order_by(ScraperRun.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    last_run = None
    if last_run_row:
        last_run = {
            "source": last_run_row.source.value if last_run_row.source else None,
            "started_at": last_run_row.started_at.isoformat() if last_run_row.started_at else None,
            "finished_at": last_run_row.finished_at.isoformat() if last_run_row.finished_at else None,
            "listings_found": last_run_row.listings_found,
            "listings_new": last_run_row.listings_new,
            "listings_updated": last_run_row.listings_updated,
            "listings_sold": last_run_row.listings_sold,
            "success": last_run_row.success,
            "error": last_run_row.error,
        }

    db_path = DATA_DIR / "vehiclemarket.db"
    db_size_mb = round(db_path.stat().st_size / 1_048_576, 2) if db_path.exists() else None

    return {
        "db_path": str(db_path),
        "db_size_mb": db_size_mb,
        "tables": {
            "listings": {
                "total": listings_total,
                "active": listings_active,
                "sold": listings_sold,
            },
            "price_history": await count(PriceHistory),
            "dealer_listings": await count(DealerListing),
            "monitored_dealers": await count(MonitoredDealer),
            "demand_snapshots": await count(DemandSnapshot),
            "price_alerts": await count(PriceAlert),
            "scraper_runs": await count(ScraperRun),
            "valuation_cache": await count(ValuationCache),
        },
        "last_scraper_run": last_run,
    }
