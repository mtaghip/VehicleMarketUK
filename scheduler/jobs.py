"""APScheduler jobs for periodic scraping and analytics."""
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import AsyncSessionLocal, init_db
from scrapers import AutoTraderScraper, CarAndClassicScraper
from scrapers.ingestion import run_ingestion
from analytics import save_demand_snapshot

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/London")


async def scrape_autotrader():
    logger.info("Starting AutoTrader scrape...")
    scraper = AutoTraderScraper()
    async with AsyncSessionLocal() as session:
        await run_ingestion(session, scraper.scrape(max_pages=settings.max_pages_per_run), "autotrader")
    logger.info("AutoTrader scrape complete.")


async def scrape_carandclassic():
    logger.info("Starting Car & Classic scrape...")
    scraper = CarAndClassicScraper()
    async with AsyncSessionLocal() as session:
        await run_ingestion(session, scraper.scrape(max_pages=settings.max_pages_per_run), "carandclassic")
    logger.info("Car & Classic scrape complete.")


async def update_demand_metrics():
    logger.info("Computing demand snapshots...")
    async with AsyncSessionLocal() as session:
        await save_demand_snapshot(session)
    logger.info("Demand snapshots saved.")


async def scrape_dealers():
    logger.info("Scraping monitored dealers...")
    from scrapers.dealer_monitor import scrape_all_dealers
    async with AsyncSessionLocal() as session:
        totals = await scrape_all_dealers(session)
    logger.info(f"Dealer scrape complete: {totals}")


def setup_scheduler():
    interval = settings.scrape_interval_minutes

    scheduler.add_job(
        scrape_autotrader,
        trigger=IntervalTrigger(minutes=interval),
        id="scrape_autotrader",
        name="AutoTrader scraper",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        scrape_carandclassic,
        trigger=IntervalTrigger(minutes=interval, jitter=600),
        id="scrape_carandclassic",
        name="Car & Classic scraper",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        update_demand_metrics,
        trigger=CronTrigger(hour="*/6"),
        id="demand_metrics",
        name="Demand metrics snapshot",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        scrape_dealers,
        trigger=IntervalTrigger(minutes=max(interval, 120), jitter=300),
        id="scrape_dealers",
        name="Dealer stock monitor",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    return scheduler
