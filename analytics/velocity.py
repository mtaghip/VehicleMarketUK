"""Sales velocity — how fast cars sell by make/model/year/colour/spec."""
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from database import Listing


@dataclass
class VelocityMetric:
    make: str
    model: str
    year_band: str
    colour: Optional[str]
    fuel_type: Optional[str]
    transmission: Optional[str]
    sample_size: int
    avg_days_to_sell: float
    median_days_to_sell: float
    min_days_to_sell: int
    max_days_to_sell: int
    pct_sold_under_7_days: float    # Gems — sold within a week
    pct_sold_under_14_days: float
    avg_price: int
    median_price: int


async def get_velocity_metrics(
    session: AsyncSession,
    make: str = None,
    model: str = None,
    year_min: int = None,
    year_max: int = None,
    colour: str = None,
    fuel_type: str = None,
    min_samples: int = 3,
    lookback_days: int = 90,
) -> list[VelocityMetric]:
    """
    Aggregate sold listings to compute how fast vehicles are selling.
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    filters = [
        Listing.is_active == False,
        Listing.sold_at.isnot(None),
        Listing.days_to_sell.isnot(None),
        Listing.sold_at >= cutoff,
    ]
    if make:
        filters.append(func.lower(Listing.make) == make.lower())
    if model:
        filters.append(func.lower(Listing.model) == model.lower())
    if year_min:
        filters.append(Listing.year >= year_min)
    if year_max:
        filters.append(Listing.year <= year_max)
    if colour:
        filters.append(func.lower(Listing.colour) == colour.lower())
    if fuel_type:
        filters.append(func.lower(Listing.fuel_type) == fuel_type.lower())

    result = await session.execute(
        select(Listing).where(and_(*filters))
    )
    rows = result.scalars().all()

    # Group in Python for flexibility (SQLite has limited window functions)
    from collections import defaultdict
    groups: dict[tuple, list[Listing]] = defaultdict(list)

    for r in rows:
        year_band = _year_band(r.year)
        key = (
            (r.make or "").lower(),
            (r.model or "").lower(),
            year_band,
            (r.colour or "").lower() if colour else "",
            (r.fuel_type or "").lower() if fuel_type else "",
        )
        groups[key].append(r)

    metrics = []
    for (mk, mdl, yb, col, ft), items in groups.items():
        if len(items) < min_samples:
            continue

        days = sorted(i.days_to_sell for i in items if i.days_to_sell is not None)
        prices = sorted(i.price for i in items if i.price)

        if not days:
            continue

        metrics.append(VelocityMetric(
            make=mk.title(),
            model=mdl.title(),
            year_band=yb,
            colour=col.title() if col else None,
            fuel_type=ft.title() if ft else None,
            transmission=None,
            sample_size=len(days),
            avg_days_to_sell=sum(days) / len(days),
            median_days_to_sell=_median(days),
            min_days_to_sell=days[0],
            max_days_to_sell=days[-1],
            pct_sold_under_7_days=sum(1 for d in days if d <= 7) / len(days) * 100,
            pct_sold_under_14_days=sum(1 for d in days if d <= 14) / len(days) * 100,
            avg_price=int(sum(prices) / len(prices)) if prices else 0,
            median_price=int(_median(prices)) if prices else 0,
        ))

    metrics.sort(key=lambda m: m.avg_days_to_sell)
    return metrics


async def get_fast_sellers(
    session: AsyncSession,
    max_days: int = 7,
    lookback_days: int = 30,
    min_samples: int = 2,
) -> list[VelocityMetric]:
    """Cars that consistently sell within `max_days`."""
    all_metrics = await get_velocity_metrics(
        session, lookback_days=lookback_days, min_samples=min_samples
    )
    return [m for m in all_metrics if m.avg_days_to_sell <= max_days]


async def get_active_listings_age(session: AsyncSession) -> list[dict]:
    """Active listings with how many days they've been live — for stale stock detection."""
    result = await session.execute(
        select(Listing).where(Listing.is_active == True)
    )
    listings = result.scalars().all()
    now = datetime.utcnow()
    return [
        {
            "id": l.id,
            "make": l.make,
            "model": l.model,
            "year": l.year,
            "price": l.price,
            "colour": l.colour,
            "days_live": (now - l.first_seen).days if l.first_seen else None,
            "url": l.url,
            "source": l.source.value if l.source else None,
        }
        for l in listings
    ]


def _year_band(year: Optional[int]) -> str:
    if not year:
        return "Unknown"
    band_start = (year // 3) * 3
    return f"{band_start}–{band_start + 2}"


def _median(sorted_list: list) -> float:
    n = len(sorted_list)
    if n == 0:
        return 0.0
    mid = n // 2
    return sorted_list[mid] if n % 2 else (sorted_list[mid - 1] + sorted_list[mid]) / 2
