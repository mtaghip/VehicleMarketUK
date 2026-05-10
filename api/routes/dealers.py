"""Dealer monitoring — add dealers, view their stock, see sold vehicles."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from database import get_db, AsyncSessionLocal, MonitoredDealer, DealerListing
from scrapers.dealer_monitor import scrape_dealer, scrape_all_dealers

router = APIRouter(prefix="/dealers", tags=["dealers"])


class DealerCreate(BaseModel):
    name: str
    autotrader_url: str                 # e.g. https://www.autotrader.co.uk/dealers/.../
    location: Optional[str] = None
    postcode: Optional[str] = None


# ─── DEALER MANAGEMENT ──────────────────────────────────────────────────────

@router.get("/")
async def list_dealers(session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(MonitoredDealer).where(MonitoredDealer.is_active == True)
        .order_by(MonitoredDealer.name)
    )
    dealers = result.scalars().all()
    return [_ser_dealer(d) for d in dealers]


@router.post("/", status_code=201)
async def add_dealer(data: DealerCreate, session: AsyncSession = Depends(get_db)):
    """Add a dealer to monitor. Paste their AutoTrader profile URL."""
    import re
    url = data.autotrader_url.rstrip("/")
    dealer_id_m = re.search(r"-(\d+)$", url)
    dealer_id = dealer_id_m.group(1) if dealer_id_m else None

    dealer = MonitoredDealer(
        name=data.name,
        autotrader_url=url,
        autotrader_dealer_id=dealer_id,
        location=data.location,
        postcode=data.postcode,
    )
    session.add(dealer)
    await session.commit()
    await session.refresh(dealer)
    return _ser_dealer(dealer)


@router.delete("/{dealer_id}")
async def remove_dealer(dealer_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(MonitoredDealer).where(MonitoredDealer.id == dealer_id)
    )
    dealer = result.scalar_one_or_none()
    if not dealer:
        raise HTTPException(status_code=404, detail="Dealer not found")
    dealer.is_active = False
    await session.commit()
    return {"deleted": dealer_id}


# ─── SCRAPING ────────────────────────────────────────────────────────────────

async def _bg_scrape_dealer(dealer_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MonitoredDealer).where(MonitoredDealer.id == dealer_id)
        )
        dealer = result.scalar_one_or_none()
        if dealer:
            await scrape_dealer(session, dealer)


async def _bg_scrape_all():
    async with AsyncSessionLocal() as session:
        await scrape_all_dealers(session)


@router.post("/{dealer_id}/scrape")
async def trigger_dealer_scrape(dealer_id: int, background_tasks: BackgroundTasks):
    """Trigger an immediate scrape of a single dealer."""
    background_tasks.add_task(_bg_scrape_dealer, dealer_id)
    return {"status": "started", "dealer_id": dealer_id}


@router.post("/scrape/all")
async def trigger_all_scrape(background_tasks: BackgroundTasks):
    """Trigger scrape of all monitored dealers."""
    background_tasks.add_task(_bg_scrape_all)
    return {"status": "started"}


# ─── STOCK & SOLD DATA ───────────────────────────────────────────────────────

@router.get("/{dealer_id}/stock")
async def dealer_stock(
    dealer_id: int,
    active_only: bool = True,
    session: AsyncSession = Depends(get_db),
):
    """Current live stock for a dealer."""
    filters = [DealerListing.dealer_id == dealer_id]
    if active_only:
        filters.append(DealerListing.is_active == True)

    result = await session.execute(
        select(DealerListing).where(and_(*filters))
        .order_by(DealerListing.first_seen.desc())
    )
    return [_ser_listing(l) for l in result.scalars().all()]


@router.get("/{dealer_id}/sold")
async def dealer_sold(
    dealer_id: int,
    days: int = Query(7, le=90),
    session: AsyncSession = Depends(get_db),
):
    """Vehicles this dealer has sold (removed) in the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(DealerListing).where(
            and_(
                DealerListing.dealer_id == dealer_id,
                DealerListing.is_active == False,
                DealerListing.sold_at.isnot(None),
                DealerListing.sold_at >= cutoff,
            )
        ).order_by(DealerListing.sold_at.desc())
    )
    return [_ser_listing(l) for l in result.scalars().all()]


@router.get("/sold/all")
async def all_dealers_sold(
    days: int = Query(7, le=90),
    session: AsyncSession = Depends(get_db),
):
    """Sold vehicles across ALL monitored dealers in the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(DealerListing, MonitoredDealer.name.label("dealer_name"))
        .join(MonitoredDealer, DealerListing.dealer_id == MonitoredDealer.id)
        .where(
            and_(
                DealerListing.is_active == False,
                DealerListing.sold_at.isnot(None),
                DealerListing.sold_at >= cutoff,
                MonitoredDealer.is_active == True,
            )
        ).order_by(DealerListing.sold_at.desc())
    )
    rows = result.all()
    return [
        {**_ser_listing(row.DealerListing), "dealer_name": row.dealer_name}
        for row in rows
    ]


@router.get("/summary/sold")
async def sold_summary(
    days: int = Query(7, le=90),
    session: AsyncSession = Depends(get_db),
):
    """Per-dealer sold count summary for the last N days — leaderboard view."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(
            MonitoredDealer.id,
            MonitoredDealer.name,
            MonitoredDealer.location,
            MonitoredDealer.total_stock,
            MonitoredDealer.last_scraped,
            func.count(DealerListing.id).label("sold_count"),
            func.avg(DealerListing.days_to_sell).label("avg_days"),
            func.avg(DealerListing.price).label("avg_price"),
        )
        .outerjoin(
            DealerListing,
            and_(
                DealerListing.dealer_id == MonitoredDealer.id,
                DealerListing.is_active == False,
                DealerListing.sold_at >= cutoff,
            )
        )
        .where(MonitoredDealer.is_active == True)
        .group_by(MonitoredDealer.id)
        .order_by(func.count(DealerListing.id).desc())
    )
    rows = result.all()
    return [
        {
            "dealer_id": r.id,
            "name": r.name,
            "location": r.location,
            "current_stock": r.total_stock,
            "sold_last_7d": r.sold_count,
            "avg_days_to_sell": round(r.avg_days, 1) if r.avg_days else None,
            "avg_sold_price": int(r.avg_price) if r.avg_price else None,
            "last_scraped": r.last_scraped.isoformat() if r.last_scraped else None,
        }
        for r in rows
    ]


def _ser_dealer(d: MonitoredDealer) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "autotrader_url": d.autotrader_url,
        "autotrader_dealer_id": d.autotrader_dealer_id,
        "location": d.location,
        "postcode": d.postcode,
        "total_stock": d.total_stock,
        "last_scraped": d.last_scraped.isoformat() if d.last_scraped else None,
        "added_at": d.added_at.isoformat() if d.added_at else None,
    }


def _ser_listing(l: DealerListing) -> dict:
    return {
        "id": l.id,
        "listing_id": l.listing_id,
        "url": l.url,
        "title": l.title,
        "reg_plate": l.reg_plate,
        "make": l.make,
        "model": l.model,
        "year": l.year,
        "colour": l.colour,
        "fuel_type": l.fuel_type,
        "price": l.price,
        "mileage": l.mileage,
        "first_seen": l.first_seen.isoformat() if l.first_seen else None,
        "sold_at": l.sold_at.isoformat() if l.sold_at else None,
        "days_to_sell": l.days_to_sell,
        "is_active": l.is_active,
    }
