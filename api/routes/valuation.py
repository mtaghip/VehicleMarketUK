"""Plate + mileage valuation endpoint using AutoTrader's public tool."""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from scrapers.valuation import get_valuation

router = APIRouter(prefix="/valuation", tags=["valuation"])


@router.get("/{reg}")
async def valuation(
    reg: str,
    mileage: int = Query(..., ge=0, le=500000, description="Current mileage"),
    force_refresh: bool = Query(False, description="Bypass 24h cache"),
    session: AsyncSession = Depends(get_db),
):
    """
    Get AutoTrader retail + trade valuation for a UK registration plate.
    Results are cached for 24 hours per reg/mileage combination.
    """
    result = await get_valuation(session, reg, mileage, force_refresh=force_refresh)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Could not retrieve valuation for {reg.upper()}. "
                "AutoTrader may have changed their page layout, or the plate is unrecognised."
            ),
        )

    return {
        "reg": result.reg,
        "mileage": result.mileage,
        "make": result.make,
        "model": result.model,
        "year": result.year,
        "colour": result.colour,
        "fuel_type": result.fuel_type,
        "transmission": result.transmission,
        "retail_price": result.retail_price,
        "trade_price": result.trade_price,
        "retail_rating": result.retail_rating,
        "avg_days_to_sell": result.avg_days_to_sell,
        "market_condition": result.market_condition,
        "price_change_pct": result.price_change_pct,
        "from_cache": result.from_cache,
        "cached_at": result.cached_at.isoformat() if result.cached_at else None,
    }
