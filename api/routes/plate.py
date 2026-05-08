from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from database import get_db
from analytics import lookup_plate

router = APIRouter(prefix="/plate", tags=["plate lookup"])


class PlateLookupResponse(BaseModel):
    reg: str
    make: str
    model: str
    colour: str
    fuel_type: str
    year: Optional[int]
    engine_cc: Optional[int]
    transmission: Optional[str]
    mot_expiry: Optional[str]
    tax_due: Optional[str]
    active_listings: int
    avg_days_to_sell: Optional[float]
    median_price: Optional[int]
    desirability_score: float
    estimated_days_to_sell: Optional[float]
    price_trend_30d_pct: Optional[float]
    comparable_listings: list[dict]

    class Config:
        from_attributes = True


@router.get("/{reg}", response_model=PlateLookupResponse)
async def plate_lookup(reg: str, session: AsyncSession = Depends(get_db)):
    """
    Look up a UK registration plate.
    Returns vehicle details from DVLA + market desirability from our data.
    """
    profile = await lookup_plate(session, reg)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for plate {reg.upper()}. Check plate is valid and data has been collected."
        )
    return profile
