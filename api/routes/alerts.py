from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from database import get_db, PriceAlert

router = APIRouter(prefix="/alerts", tags=["price alerts"])


class AlertCreate(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    max_price: Optional[int] = None
    max_mileage: Optional[int] = None
    colour: Optional[str] = None
    fuel_type: Optional[str] = None
    email: Optional[str] = None


@router.get("/")
async def list_alerts(session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(PriceAlert).where(PriceAlert.is_active == True))
    alerts = result.scalars().all()
    return [_ser(a) for a in alerts]


@router.post("/", status_code=201)
async def create_alert(data: AlertCreate, session: AsyncSession = Depends(get_db)):
    alert = PriceAlert(**data.model_dump())
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return _ser(alert)


@router.delete("/{alert_id}")
async def delete_alert(alert_id: int, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(PriceAlert).where(PriceAlert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_active = False
    await session.commit()
    return {"deleted": alert_id}


@router.get("/check")
async def check_alerts(session: AsyncSession = Depends(get_db)):
    """Manually trigger alert checking — returns matched listings."""
    from analytics.pricing import check_price_alerts
    return await check_price_alerts(session)


def _ser(a: PriceAlert) -> dict:
    return {
        "id": a.id,
        "make": a.make,
        "model": a.model,
        "year_min": a.year_min,
        "year_max": a.year_max,
        "max_price": a.max_price,
        "max_mileage": a.max_mileage,
        "colour": a.colour,
        "fuel_type": a.fuel_type,
        "email": a.email,
        "is_active": a.is_active,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
