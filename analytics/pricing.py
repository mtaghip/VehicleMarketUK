"""Price analysis — trends, alerts, market value estimation."""
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import Listing, PriceHistory, PriceAlert


@dataclass
class PriceInsight:
    make: str
    model: str
    year: Optional[int]
    colour: Optional[str]
    fuel_type: Optional[str]
    sample_size: int
    avg_price: int
    median_price: int
    min_price: int
    max_price: int
    price_change_30d_pct: Optional[float]   # % change in avg price over 30 days
    underpriced_threshold: int              # Price below which = potential gem


async def get_price_insights(
    session: AsyncSession,
    make: str = None,
    model: str = None,
    year: int = None,
    colour: str = None,
    fuel_type: str = None,
) -> Optional[PriceInsight]:
    """Calculate price distribution for a vehicle spec."""
    filters = [
        Listing.is_active == True,
        Listing.price.isnot(None),
        Listing.price > 100,
    ]
    if make:
        filters.append(func.lower(Listing.make) == make.lower())
    if model:
        filters.append(func.lower(Listing.model) == model.lower())
    if year:
        filters.append(Listing.year == year)
    if colour:
        filters.append(func.lower(Listing.colour) == colour.lower())
    if fuel_type:
        filters.append(func.lower(Listing.fuel_type) == fuel_type.lower())

    result = await session.execute(
        select(Listing).where(and_(*filters))
    )
    listings = result.scalars().all()

    if len(listings) < 2:
        return None

    prices = sorted(l.price for l in listings if l.price)
    n = len(prices)
    avg = int(sum(prices) / n)
    median = int(_median(prices))

    # 30d price trend
    d30 = datetime.utcnow() - timedelta(days=30)
    old_result = await session.execute(
        select(PriceHistory).where(
            and_(
                PriceHistory.recorded_at <= d30,
                PriceHistory.listing_id.in_([l.id for l in listings]),
            )
        )
    )
    # Compare the same cohort, with one latest price per listing at the cutoff.
    baseline = {}
    for ph in old_result.scalars().all():
        if ph.price and (ph.listing_id not in baseline or
                         (ph.recorded_at, ph.id) > (baseline[ph.listing_id].recorded_at, baseline[ph.listing_id].id)):
            baseline[ph.listing_id] = ph
    paired = [listing for listing in listings if listing.id in baseline]
    old_total = sum(baseline[listing.id].price for listing in paired)
    current_total = sum(listing.price for listing in paired)
    trend_pct = round((current_total - old_total) / old_total * 100, 1) if old_total else None

    # Underpriced = below 15th percentile
    idx_15 = max(0, int(n * 0.15))
    underpriced_threshold = prices[idx_15]

    return PriceInsight(
        make=(make or "").title(),
        model=(model or "").title(),
        year=year,
        colour=(colour or "").title() or None,
        fuel_type=(fuel_type or "").title() or None,
        sample_size=n,
        avg_price=avg,
        median_price=median,
        min_price=prices[0],
        max_price=prices[-1],
        price_change_30d_pct=trend_pct,
        underpriced_threshold=underpriced_threshold,
    )


async def find_underpriced_listings(
    session: AsyncSession,
    make: str = None,
    model: str = None,
    year_min: int = None,
    year_max: int = None,
    discount_pct: float = 15.0,
) -> list[dict]:
    """
    Find active listings priced below market median by `discount_pct`%.
    These are potential sourcing gems.
    """
    filters = [Listing.is_active == True, Listing.price.isnot(None)]
    if make:
        filters.append(func.lower(Listing.make) == make.lower())
    if model:
        filters.append(func.lower(Listing.model) == model.lower())
    if year_min:
        filters.append(Listing.year >= year_min)
    if year_max:
        filters.append(Listing.year <= year_max)

    result = await session.execute(select(Listing).where(and_(*filters)))
    listings = result.scalars().all()

    # Group by make/model/year and find below-median within each group
    from collections import defaultdict
    groups: dict[tuple, list[Listing]] = defaultdict(list)
    for l in listings:
        groups[((l.make or "").lower(), (l.model or "").lower(), l.year)].append(l)

    gems = []
    for (mk, mdl, yr), items in groups.items():
        if len(items) < 3:
            continue
        prices = sorted(i.price for i in items if i.price)
        median = _median(prices)
        threshold = median * (1 - discount_pct / 100)
        for item in items:
            if item.price and item.price <= threshold:
                discount = round((median - item.price) / median * 100, 1)
                gems.append({
                    "id": item.id,
                    "make": item.make,
                    "model": item.model,
                    "year": item.year,
                    "colour": item.colour,
                    "mileage": item.mileage,
                    "price": item.price,
                    "market_median": int(median),
                    "discount_pct": discount,
                    "url": item.url,
                    "source": item.source.value if item.source else None,
                    "days_live": (datetime.utcnow() - item.first_seen).days if item.first_seen else None,
                })

    gems.sort(key=lambda g: g["discount_pct"], reverse=True)
    return gems


async def check_price_alerts(session: AsyncSession) -> list[dict]:
    """Check all active price alerts against current listings."""
    alert_result = await session.execute(
        select(PriceAlert).where(PriceAlert.is_active == True)
    )
    alerts = alert_result.scalars().all()

    triggered = []
    for alert in alerts:
        filters = [Listing.is_active == True]
        if alert.make:
            filters.append(func.lower(Listing.make) == alert.make.lower())
        if alert.model:
            filters.append(func.lower(Listing.model) == alert.model.lower())
        if alert.year_min:
            filters.append(Listing.year >= alert.year_min)
        if alert.year_max:
            filters.append(Listing.year <= alert.year_max)
        if alert.max_price:
            filters.append(Listing.price <= alert.max_price)
        if alert.max_mileage:
            filters.append(Listing.mileage <= alert.max_mileage)
        if alert.colour:
            filters.append(func.lower(Listing.colour) == alert.colour.lower())
        if alert.fuel_type:
            filters.append(func.lower(Listing.fuel_type) == alert.fuel_type.lower())

        result = await session.execute(select(Listing).where(and_(*filters)))
        matches = result.scalars().all()

        if matches:
            triggered.append({
                "alert_id": alert.id,
                "email": alert.email,
                "criteria": {
                    "make": alert.make,
                    "model": alert.model,
                    "year_min": alert.year_min,
                    "year_max": alert.year_max,
                    "max_price": alert.max_price,
                },
                "matches": [
                    {"id": m.id, "url": m.url, "price": m.price, "year": m.year, "colour": m.colour}
                    for m in matches[:10]
                ],
            })

    return triggered


def _median(sorted_list: list) -> float:
    n = len(sorted_list)
    if n == 0:
        return 0.0
    mid = n // 2
    return sorted_list[mid] if n % 2 else (sorted_list[mid - 1] + sorted_list[mid]) / 2
