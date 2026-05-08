from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from database import get_db, Listing

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("/")
async def list_listings(
    make: Optional[str] = None,
    model: Optional[str] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    colour: Optional[str] = None,
    fuel_type: Optional[str] = None,
    source: Optional[str] = None,
    active_only: bool = True,
    limit: int = Query(50, le=200),
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
):
    filters = []
    if active_only:
        filters.append(Listing.is_active == True)
    if make:
        filters.append(func.lower(Listing.make) == make.lower())
    if model:
        filters.append(func.lower(Listing.model) == model.lower())
    if year_min:
        filters.append(Listing.year >= year_min)
    if year_max:
        filters.append(Listing.year <= year_max)
    if price_min:
        filters.append(Listing.price >= price_min)
    if price_max:
        filters.append(Listing.price <= price_max)
    if colour:
        filters.append(func.lower(Listing.colour) == colour.lower())
    if fuel_type:
        filters.append(func.lower(Listing.fuel_type) == fuel_type.lower())
    if source:
        filters.append(Listing.source == source)

    result = await session.execute(
        select(Listing)
        .where(and_(*filters) if filters else True)
        .order_by(Listing.first_seen.desc())
        .limit(limit)
        .offset(offset)
    )
    listings = result.scalars().all()

    return [_serialize(l) for l in listings]


@router.get("/recent")
async def recent_listings(
    hours: int = Query(24, le=168),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    result = await session.execute(
        select(Listing)
        .where(and_(Listing.is_active == True, Listing.first_seen >= cutoff))
        .order_by(Listing.first_seen.desc())
        .limit(limit)
    )
    return [_serialize(l) for l in result.scalars().all()]


@router.get("/sold")
async def sold_listings(
    make: Optional[str] = None,
    model: Optional[str] = None,
    days: int = Query(30, le=365),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    filters = [Listing.is_active == False, Listing.sold_at.isnot(None), Listing.sold_at >= cutoff]
    if make:
        filters.append(func.lower(Listing.make) == make.lower())
    if model:
        filters.append(func.lower(Listing.model) == model.lower())

    result = await session.execute(
        select(Listing).where(and_(*filters)).order_by(Listing.sold_at.desc()).limit(limit)
    )
    return [_serialize(l) for l in result.scalars().all()]


@router.get("/{listing_id}")
async def get_listing(listing_id: int, session: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException
    from database import PriceHistory
    result = await session.execute(select(Listing).where(Listing.id == listing_id))
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    ph_result = await session.execute(
        select(PriceHistory)
        .where(PriceHistory.listing_id == listing_id)
        .order_by(PriceHistory.recorded_at)
    )
    price_history = [{"price": p.price, "date": p.recorded_at.isoformat()} for p in ph_result.scalars().all()]

    data = _serialize(listing)
    data["price_history"] = price_history
    return data


def _serialize(l: Listing) -> dict:
    from datetime import datetime
    return {
        "id": l.id,
        "listing_id": l.listing_id,
        "source": l.source.value if l.source else None,
        "url": l.url,
        "make": l.make,
        "model": l.model,
        "variant": l.variant,
        "year": l.year,
        "colour": l.colour,
        "mileage": l.mileage,
        "fuel_type": l.fuel_type,
        "transmission": l.transmission,
        "price": l.price,
        "original_price": l.original_price,
        "location": l.location,
        "seller_type": l.seller_type,
        "first_seen": l.first_seen.isoformat() if l.first_seen else None,
        "last_seen": l.last_seen.isoformat() if l.last_seen else None,
        "sold_at": l.sold_at.isoformat() if l.sold_at else None,
        "days_to_sell": l.days_to_sell,
        "is_active": l.is_active,
        "days_live": (datetime.utcnow() - l.first_seen).days if l.first_seen and l.is_active else None,
    }
