from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from database import get_db
from analytics import (
    get_velocity_metrics, get_fast_sellers, get_active_listings_age,
    compute_demand_signals, get_demand_trends,
    get_price_insights, find_underpriced_listings,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/velocity")
async def velocity(
    make: Optional[str] = None,
    model: Optional[str] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    colour: Optional[str] = None,
    fuel_type: Optional[str] = None,
    lookback_days: int = Query(90, le=365),
    min_samples: int = Query(3, ge=1),
    session: AsyncSession = Depends(get_db),
):
    """Sales velocity metrics — how fast cars are selling."""
    metrics = await get_velocity_metrics(
        session, make=make, model=model,
        year_min=year_min, year_max=year_max,
        colour=colour, fuel_type=fuel_type,
        min_samples=min_samples, lookback_days=lookback_days,
    )
    return [_vel_to_dict(m) for m in metrics]


@router.get("/fast-sellers")
async def fast_sellers(
    max_days: int = Query(7, le=30),
    lookback_days: int = Query(30, le=365),
    session: AsyncSession = Depends(get_db),
):
    """Cars that consistently sell within `max_days` — potential sourcing targets."""
    metrics = await get_fast_sellers(session, max_days=max_days, lookback_days=lookback_days)
    return [_vel_to_dict(m) for m in metrics]


@router.get("/gems")
async def gems(
    make: Optional[str] = None,
    model: Optional[str] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    discount_pct: float = Query(15.0, ge=5.0, le=50.0),
    session: AsyncSession = Depends(get_db),
):
    """Underpriced listings vs market median — potential buys."""
    return await find_underpriced_listings(
        session, make=make, model=model,
        year_min=year_min, year_max=year_max,
        discount_pct=discount_pct,
    )


@router.get("/demand")
async def demand_signals(
    session: AsyncSession = Depends(get_db),
):
    """Current demand signals for all make/model combinations."""
    signals = await compute_demand_signals(session)
    return [
        {
            "make": s.make,
            "model": s.model,
            "year_band": s.year_band,
            "active_count": s.active_count,
            "new_last_24h": s.new_last_24h,
            "new_last_7d": s.new_last_7d,
            "sold_last_7d": s.sold_last_7d,
            "sold_last_30d": s.sold_last_30d,
            "avg_price": s.avg_price,
            "demand_score": s.demand_score,
            "spike_detected": s.spike_detected,
            "avg_days_to_sell": s.avg_days_to_sell,
        }
        for s in signals
    ]


@router.get("/demand/spikes")
async def demand_spikes(session: AsyncSession = Depends(get_db)):
    """Models with a sudden surge in new listings this week."""
    signals = await compute_demand_signals(session)
    return [
        {
            "make": s.make,
            "model": s.model,
            "new_last_7d": s.new_last_7d,
            "demand_score": s.demand_score,
            "avg_days_to_sell": s.avg_days_to_sell,
        }
        for s in signals if s.spike_detected
    ]


@router.get("/demand/trend")
async def demand_trend(
    make: str,
    model: str,
    days: int = Query(30, le=180),
    session: AsyncSession = Depends(get_db),
):
    """Historical demand trend for a specific make/model."""
    return await get_demand_trends(session, make=make, model=model, days=days)


@router.get("/price-insights")
async def price_insights(
    make: str,
    model: str,
    year: Optional[int] = None,
    colour: Optional[str] = None,
    fuel_type: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
):
    """Price distribution and 30-day trend for a vehicle spec."""
    insight = await get_price_insights(
        session, make=make, model=model, year=year, colour=colour, fuel_type=fuel_type
    )
    if not insight:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not enough data for this spec")
    return {
        "make": insight.make,
        "model": insight.model,
        "year": insight.year,
        "colour": insight.colour,
        "sample_size": insight.sample_size,
        "avg_price": insight.avg_price,
        "median_price": insight.median_price,
        "min_price": insight.min_price,
        "max_price": insight.max_price,
        "price_change_30d_pct": insight.price_change_30d_pct,
        "underpriced_threshold": insight.underpriced_threshold,
    }


@router.get("/stale-stock")
async def stale_stock(
    min_days: int = Query(30, ge=7),
    session: AsyncSession = Depends(get_db),
):
    """Active listings that have been live for longer than expected."""
    all_active = await get_active_listings_age(session)
    return [l for l in all_active if (l["days_live"] or 0) >= min_days]


def _vel_to_dict(m) -> dict:
    return {
        "make": m.make,
        "model": m.model,
        "year_band": m.year_band,
        "colour": m.colour,
        "fuel_type": m.fuel_type,
        "sample_size": m.sample_size,
        "avg_days_to_sell": round(m.avg_days_to_sell, 1),
        "median_days_to_sell": round(m.median_days_to_sell, 1),
        "min_days_to_sell": m.min_days_to_sell,
        "max_days_to_sell": m.max_days_to_sell,
        "pct_sold_under_7_days": round(m.pct_sold_under_7_days, 1),
        "pct_sold_under_14_days": round(m.pct_sold_under_14_days, 1),
        "avg_price": m.avg_price,
        "median_price": m.median_price,
    }
