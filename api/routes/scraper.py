"""Manual scraper trigger endpoints."""
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from config import settings
from database import get_db, AsyncSessionLocal, BulkScrapeProgress
from scrapers import AutoTraderScraper, CarAndClassicScraper
from scrapers.ingestion import run_ingestion
from scrapers.bulk import _build_tasks

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


async def _run_bulk(source: str, resume: bool = True):
    from scrapers.bulk import run_bulk_autotrader, run_bulk_carandclassic, run_full_bulk_scrape
    import logging
    logger = logging.getLogger(__name__)
    try:
        if source == "autotrader":
            result = await run_bulk_autotrader(resume=resume)
        elif source == "carandclassic":
            result = await run_bulk_carandclassic(resume=resume)
        else:
            result = await run_full_bulk_scrape(resume=resume)
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
    resume: bool = Query(default=True, description="Skip already-completed make/model tasks"),
):
    """
    Trigger a full-market bulk scrape covering every make × model combination.
    source = autotrader | carandclassic | all
    High-volume models are split by year band to bypass AutoTrader's 100-page cap.
    Runs in the background — can take 12-20 hours for a full first run.
    Use resume=false to re-scrape everything from scratch.
    """
    if source not in ("autotrader", "carandclassic", "all"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="source must be autotrader, carandclassic, or all")

    background_tasks.add_task(_run_bulk, source, resume)
    return {
        "status": "started",
        "source": source,
        "resume": resume,
        "note": "Full-market scrape running in background — check /api/scraper/bulk/progress",
    }


@router.get("/bulk/progress")
async def bulk_progress(session: AsyncSession = Depends(get_db)):
    """How far through the full-market bulk scrape we are."""
    at_total = len(_build_tasks("autotrader"))
    cc_total = len(_build_tasks("carandclassic"))

    at_done_row = await session.execute(
        select(func.count(), func.sum(BulkScrapeProgress.listings_saved))
        .where(BulkScrapeProgress.source == "autotrader")
    )
    at_done, at_saved = at_done_row.one()

    cc_done_row = await session.execute(
        select(func.count(), func.sum(BulkScrapeProgress.listings_saved))
        .where(BulkScrapeProgress.source == "carandclassic")
    )
    cc_done, cc_saved = cc_done_row.one()

    last_row = await session.execute(
        select(BulkScrapeProgress)
        .order_by(BulkScrapeProgress.completed_at.desc())
        .limit(1)
    )
    last = last_row.scalar_one_or_none()

    return {
        "autotrader": {
            "tasks_total": at_total,
            "tasks_done": at_done or 0,
            "tasks_remaining": at_total - (at_done or 0),
            "pct_complete": round((at_done or 0) / at_total * 100, 1),
            "listings_saved": at_saved or 0,
        },
        "carandclassic": {
            "tasks_total": cc_total,
            "tasks_done": cc_done or 0,
            "tasks_remaining": cc_total - (cc_done or 0),
            "pct_complete": round((cc_done or 0) / cc_total * 100, 1),
            "listings_saved": cc_saved or 0,
        },
        "last_completed_task": {
            "source": last.source,
            "make": last.make,
            "model": last.model,
            "year_from": last.year_from,
            "year_to": last.year_to,
            "completed_at": last.completed_at.isoformat(),
            "listings_saved": last.listings_saved,
        } if last else None,
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
