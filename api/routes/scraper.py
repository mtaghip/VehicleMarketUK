"""Manual scraper trigger endpoints."""
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db, AsyncSessionLocal
from scrapers import AutoTraderScraper, CarAndClassicScraper
from scrapers.ingestion import run_ingestion

router = APIRouter(prefix="/scraper", tags=["scraper"])


async def _run_at(source: str):
    if source == "autotrader":
        scraper = AutoTraderScraper()
        gen = scraper.scrape(max_pages=settings.max_pages_per_run)
    else:
        scraper = CarAndClassicScraper()
        gen = scraper.scrape(max_pages=settings.max_pages_per_run)

    async with AsyncSessionLocal() as session:
        run = await run_ingestion(session, gen, source)
    return run


async def _run_bulk(source: str, pages_per_make: int):
    from scrapers.bulk import run_bulk_autotrader, run_bulk_carandclassic, run_full_bulk_scrape
    import logging
    logger = logging.getLogger(__name__)
    try:
        if source == "autotrader":
            result = await run_bulk_autotrader(pages_per_make)
        elif source == "carandclassic":
            result = await run_bulk_carandclassic(pages_per_make)
        else:
            result = await run_full_bulk_scrape(pages_per_make)
        logger.info(f"Bulk scrape finished: {result}")
    except Exception as e:
        logger.error(f"Bulk scrape error: {e}", exc_info=True)


@router.post("/run/{source}")
async def trigger_scrape(source: str, background_tasks: BackgroundTasks):
    """Trigger a normal (recent listings) scrape run in the background."""
    if source not in ("autotrader", "carandclassic", "all"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="source must be autotrader, carandclassic, or all")

    if source in ("autotrader", "all"):
        background_tasks.add_task(_run_at, "autotrader")
    if source in ("carandclassic", "all"):
        background_tasks.add_task(_run_at, "carandclassic")

    return {"status": "started", "source": source}


@router.post("/run/bulk/{source}")
async def trigger_bulk_scrape(
    source: str,
    background_tasks: BackgroundTasks,
    pages: int = Query(default=None, description="Pages per make (overrides BULK_SCRAPE_PAGES setting)"),
):
    """
    Trigger a full-market bulk scrape that covers every major make (up to N pages each).
    source = autotrader | carandclassic | all
    This runs in the background and can take 1-4 hours.
    """
    if source not in ("autotrader", "carandclassic", "all"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="source must be autotrader, carandclassic, or all")

    pages_per_make = pages or settings.bulk_scrape_pages
    background_tasks.add_task(_run_bulk, source, pages_per_make)
    return {
        "status": "started",
        "source": source,
        "pages_per_make": pages_per_make,
        "note": "Bulk scrape runs in background — check /api/scraper/status for progress",
    }


@router.get("/status")
async def scraper_status(session: AsyncSession = Depends(get_db)):
    """Last 20 scraper runs."""
    from sqlalchemy import select
    from database import ScraperRun
    result = await session.execute(
        select(ScraperRun).order_by(ScraperRun.started_at.desc()).limit(20)
    )
    runs = result.scalars().all()
    return [
        {
            "id": r.id,
            "source": r.source.value if r.source else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "listings_found": r.listings_found,
            "listings_new": r.listings_new,
            "listings_updated": r.listings_updated,
            "listings_sold": r.listings_sold,
            "success": r.success,
            "error": r.error,
        }
        for r in runs
    ]
