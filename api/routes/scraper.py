"""Manual scraper trigger endpoints."""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, AsyncSessionLocal
from scrapers import AutoTraderScraper, CarAndClassicScraper
from scrapers.ingestion import run_ingestion

router = APIRouter(prefix="/scraper", tags=["scraper"])


async def _run_at(source: str):
    if source == "autotrader":
        scraper = AutoTraderScraper()
        gen = scraper.scrape(max_pages=5)
    else:
        scraper = CarAndClassicScraper()
        gen = scraper.scrape(max_pages=5)

    async with AsyncSessionLocal() as session:
        run = await run_ingestion(session, gen, source)
    return run


@router.post("/run/{source}")
async def trigger_scrape(source: str, background_tasks: BackgroundTasks):
    """Trigger a scrape run in the background."""
    if source not in ("autotrader", "carandclassic", "all"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="source must be autotrader, carandclassic, or all")

    if source in ("autotrader", "all"):
        background_tasks.add_task(_run_at, "autotrader")
    if source in ("carandclassic", "all"):
        background_tasks.add_task(_run_at, "carandclassic")

    return {"status": "started", "source": source}


@router.get("/status")
async def scraper_status(session: AsyncSession = Depends(get_db)):
    """Last 10 scraper runs."""
    from sqlalchemy import select
    from database import ScraperRun
    result = await session.execute(
        select(ScraperRun).order_by(ScraperRun.started_at.desc()).limit(10)
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
