"""Demand analysis — track listing volumes and detect spikes."""
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import Listing, DemandSnapshot


@dataclass
class DemandSignal:
    make: str
    model: str
    year_band: str
    active_count: int
    new_last_24h: int
    new_last_7d: int
    sold_last_7d: int
    sold_last_30d: int
    avg_price: Optional[int]
    demand_score: float         # 0-100 composite
    spike_detected: bool        # True if new listings jumped > 50% vs prior week
    avg_days_to_sell: Optional[float]


async def compute_demand_signals(
    session: AsyncSession,
    min_active: int = 3,
) -> list[DemandSignal]:
    """
    Compute demand signals for all make/model combinations.
    """
    now = datetime.utcnow()
    h24 = now - timedelta(hours=24)
    d7 = now - timedelta(days=7)
    d14 = now - timedelta(days=14)
    d30 = now - timedelta(days=30)

    # Fetch all active listings
    active_result = await session.execute(
        select(Listing).where(Listing.is_active == True)
    )
    active = active_result.scalars().all()

    # Fetch sold in last 30d
    sold_result = await session.execute(
        select(Listing).where(
            and_(
                Listing.is_active == False,
                Listing.sold_at.isnot(None),
                Listing.sold_at >= d30,
            )
        )
    )
    sold_30d = sold_result.scalars().all()

    from collections import defaultdict

    # Group active by make/model
    active_by_mm: dict[tuple, list[Listing]] = defaultdict(list)
    for l in active:
        key = ((l.make or "").lower(), (l.model or "").lower())
        active_by_mm[key].append(l)

    sold_by_mm: dict[tuple, list[Listing]] = defaultdict(list)
    for l in sold_30d:
        key = ((l.make or "").lower(), (l.model or "").lower())
        sold_by_mm[key].append(l)

    all_keys = set(active_by_mm.keys()) | set(sold_by_mm.keys())
    signals = []

    for (mk, mdl) in all_keys:
        active_items = active_by_mm.get((mk, mdl), [])
        sold_items = sold_by_mm.get((mk, mdl), [])

        if len(active_items) < min_active and not sold_items:
            continue

        new_24h = sum(1 for l in active_items if l.first_seen and l.first_seen >= h24)
        new_7d = sum(1 for l in active_items if l.first_seen and l.first_seen >= d7)
        new_prev_7d = sum(1 for l in active_items if l.first_seen and d14 <= l.first_seen < d7)
        sold_7d = sum(1 for l in sold_items if l.sold_at and l.sold_at >= d7)
        sold_30d_count = len(sold_items)

        prices = [l.price for l in active_items if l.price]
        avg_price = int(sum(prices) / len(prices)) if prices else None

        days_to_sell = [l.days_to_sell for l in sold_items if l.days_to_sell is not None]
        avg_dts = sum(days_to_sell) / len(days_to_sell) if days_to_sell else None

        # Spike: new listings this week > 1.5x last week AND at least 2 new
        spike = (
            new_7d > 1 and
            new_prev_7d > 0 and
            new_7d >= new_prev_7d * 1.5
        ) or (new_7d >= 5 and new_prev_7d == 0)

        # Demand score: weighted composite
        velocity_score = max(0, 50 - (avg_dts or 50)) if avg_dts else 25
        volume_score = min(30, sold_30d_count * 3)
        spike_bonus = 20 if spike else 0
        demand_score = min(100, velocity_score + volume_score + spike_bonus)

        year_band = _modal_year_band([l.year for l in active_items if l.year])

        signals.append(DemandSignal(
            make=mk.title(),
            model=mdl.title(),
            year_band=year_band,
            active_count=len(active_items),
            new_last_24h=new_24h,
            new_last_7d=new_7d,
            sold_last_7d=sold_7d,
            sold_last_30d=sold_30d_count,
            avg_price=avg_price,
            demand_score=round(demand_score, 1),
            spike_detected=spike,
            avg_days_to_sell=round(avg_dts, 1) if avg_dts else None,
        ))

    signals.sort(key=lambda s: s.demand_score, reverse=True)
    return signals


async def save_demand_snapshot(session: AsyncSession):
    """Persist current demand signals to demand_snapshots table."""
    signals = await compute_demand_signals(session)
    now = datetime.utcnow()

    for s in signals:
        snap = DemandSnapshot(
            snapshot_at=now,
            make=s.make,
            model=s.model,
            year_band=s.year_band,
            active_count=s.active_count,
            new_today=s.new_last_24h,
            sold_this_week=s.sold_last_7d,
            avg_days_to_sell=s.avg_days_to_sell,
            avg_price=s.avg_price,
            median_price=s.avg_price,
        )
        session.add(snap)

    await session.commit()


async def get_demand_trends(
    session: AsyncSession,
    make: str,
    model: str,
    days: int = 30,
) -> list[dict]:
    """Historical demand snapshots for a make/model."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(DemandSnapshot).where(
            and_(
                func.lower(DemandSnapshot.make) == make.lower(),
                func.lower(DemandSnapshot.model) == model.lower(),
                DemandSnapshot.snapshot_at >= cutoff,
            )
        ).order_by(DemandSnapshot.snapshot_at)
    )
    snaps = result.scalars().all()
    return [
        {
            "date": s.snapshot_at.isoformat(),
            "active_count": s.active_count,
            "new_today": s.new_today,
            "sold_this_week": s.sold_this_week,
            "avg_days_to_sell": s.avg_days_to_sell,
            "avg_price": s.avg_price,
        }
        for s in snaps
    ]


def _modal_year_band(years: list[int]) -> str:
    if not years:
        return "Unknown"
    from collections import Counter
    modal_year = Counter(years).most_common(1)[0][0]
    band_start = (modal_year // 3) * 3
    return f"{band_start}–{band_start + 2}"
