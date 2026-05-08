"""
DVLA plate lookup + desirability scoring.

DVLA Vehicle Enquiry Service (VES) API:
  POST https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiries/v1/vehicles
  x-api-key: <your key>

Free API key: https://developer-portal.driver-vehicle-licensing.api.gov.uk/
"""
import httpx
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import Listing
from .velocity import get_velocity_metrics

logger = logging.getLogger(__name__)

DVLA_URL = "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiries/v1/vehicles"


@dataclass
class VehicleProfile:
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
    # Market intelligence
    active_listings: int
    avg_days_to_sell: Optional[float]
    median_price: Optional[int]
    desirability_score: float       # 0-100
    estimated_days_to_sell: Optional[float]
    price_trend_30d_pct: Optional[float]
    comparable_listings: list[dict]


async def lookup_plate(session: AsyncSession, reg: str) -> Optional[VehicleProfile]:
    """
    Look up a registration plate via DVLA, then enrich with market data.
    """
    reg_clean = reg.upper().replace(" ", "")

    # 1. DVLA lookup
    dvla_data = await _dvla_lookup(reg_clean)
    if not dvla_data:
        # Fallback: search our own database by plate
        return await _profile_from_db(session, reg_clean)

    make = dvla_data.get("make", "").title()
    colour = dvla_data.get("colour", "").title()
    fuel_type = _map_fuel(dvla_data.get("fuelType", ""))
    year = _extract_year(dvla_data)
    engine_cc = dvla_data.get("engineCapacity")

    # 2. Market intelligence from our database
    return await _build_profile(
        session=session,
        reg=reg_clean,
        make=make,
        model="",       # DVLA doesn't return model
        colour=colour,
        fuel_type=fuel_type,
        year=year,
        engine_cc=engine_cc,
        mot_expiry=dvla_data.get("motExpiryDate"),
        tax_due=dvla_data.get("taxDueDate"),
        transmission=None,
    )


async def _dvla_lookup(reg: str) -> Optional[dict]:
    if not settings.dvla_api_key:
        logger.warning("No DVLA API key set — skipping DVLA lookup")
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                DVLA_URL,
                json={"registrationNumber": reg},
                headers={
                    "x-api-key": settings.dvla_api_key,
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"DVLA returned {resp.status_code} for {reg}: {resp.text}")
    except Exception as e:
        logger.error(f"DVLA lookup error: {e}")
    return None


async def _profile_from_db(session: AsyncSession, reg: str) -> Optional[VehicleProfile]:
    """Fallback: find listing by plate in our database."""
    result = await session.execute(
        select(Listing).where(
            func.upper(func.replace(Listing.reg_plate, " ", "")) == reg
        ).order_by(Listing.first_seen.desc()).limit(1)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        return None

    return await _build_profile(
        session=session,
        reg=reg,
        make=listing.make or "",
        model=listing.model or "",
        colour=listing.colour or "",
        fuel_type=listing.fuel_type or "",
        year=listing.year,
        engine_cc=int(listing.engine_size * 1000) if listing.engine_size else None,
        mot_expiry=None,
        tax_due=None,
        transmission=listing.transmission,
    )


async def _build_profile(
    session: AsyncSession,
    reg: str,
    make: str,
    model: str,
    colour: str,
    fuel_type: str,
    year: Optional[int],
    engine_cc: Optional[int],
    mot_expiry: Optional[str],
    tax_due: Optional[str],
    transmission: Optional[str],
) -> VehicleProfile:

    # Find comparable active listings
    filters = [Listing.is_active == True]
    if make:
        filters.append(func.lower(Listing.make) == make.lower())
    if model:
        filters.append(func.lower(Listing.model) == model.lower())
    if year:
        filters.append(Listing.year.between(year - 2, year + 2))

    result = await session.execute(
        select(Listing).where(and_(*filters)).limit(50)
    )
    comparables = result.scalars().all()

    prices = [l.price for l in comparables if l.price]
    median_price = int(_median(sorted(prices))) if prices else None

    # Velocity metrics
    metrics = await get_velocity_metrics(
        session, make=make, model=model,
        year_min=year - 2 if year else None,
        year_max=year + 2 if year else None,
    )
    avg_dts = metrics[0].avg_days_to_sell if metrics else None
    pct_under_7 = metrics[0].pct_sold_under_7_days if metrics else 0.0

    # Desirability score
    desirability = _calc_desirability(
        avg_days_to_sell=avg_dts,
        pct_sold_under_7=pct_under_7,
        active_count=len(comparables),
        colour=colour,
    )

    # Estimated days to sell for this specific vehicle
    # Adjust avg by colour modifier
    est_days = None
    if avg_dts:
        colour_modifier = _colour_modifier(colour)
        est_days = round(avg_dts * colour_modifier, 1)

    return VehicleProfile(
        reg=reg,
        make=make,
        model=model,
        colour=colour,
        fuel_type=fuel_type,
        year=year,
        engine_cc=engine_cc,
        transmission=transmission,
        mot_expiry=mot_expiry,
        tax_due=tax_due,
        active_listings=len(comparables),
        avg_days_to_sell=round(avg_dts, 1) if avg_dts else None,
        median_price=median_price,
        desirability_score=desirability,
        estimated_days_to_sell=est_days,
        price_trend_30d_pct=None,
        comparable_listings=[
            {
                "id": l.id,
                "year": l.year,
                "colour": l.colour,
                "price": l.price,
                "mileage": l.mileage,
                "url": l.url,
                "source": l.source.value if l.source else None,
                "days_live": None,
            }
            for l in comparables[:8]
        ],
    )


def _calc_desirability(
    avg_days_to_sell: Optional[float],
    pct_sold_under_7: float,
    active_count: int,
    colour: str,
) -> float:
    score = 50.0
    if avg_days_to_sell is not None:
        if avg_days_to_sell <= 7:
            score += 30
        elif avg_days_to_sell <= 14:
            score += 20
        elif avg_days_to_sell <= 30:
            score += 10
        elif avg_days_to_sell > 60:
            score -= 15
    score += min(10, pct_sold_under_7 / 10)
    if active_count >= 10:
        score += 5
    colour_bonus = {
        "Silver": 5, "Black": 5, "White": 5, "Grey": 4, "Blue": 3,
        "Red": 2, "Green": 1, "Orange": -2, "Purple": -3, "Pink": -5,
    }.get(colour.title() if colour else "", 0)
    score += colour_bonus
    return round(max(0, min(100, score)), 1)


def _colour_modifier(colour: str) -> float:
    """Multiply avg days to sell by this factor for colour-adjusted estimate."""
    modifiers = {
        "silver": 0.85, "black": 0.9, "white": 0.9, "grey": 0.9,
        "blue": 0.95, "red": 1.0, "green": 1.1, "orange": 1.2,
        "purple": 1.25, "pink": 1.4, "brown": 1.15, "gold": 1.2,
    }
    return modifiers.get((colour or "").lower(), 1.0)


def _map_fuel(dvla_fuel: str) -> str:
    mapping = {
        "PETROL": "Petrol", "DIESEL": "Diesel",
        "ELECTRIC": "Electric", "HYBRID ELECTRIC": "Hybrid",
        "PLUG-IN HYBRID ELECTRIC": "Plug-in Hybrid",
    }
    return mapping.get(dvla_fuel.upper() if dvla_fuel else "", dvla_fuel.title())


def _extract_year(dvla_data: dict) -> Optional[int]:
    # monthOfFirstRegistration: "2019-03"
    reg_date = dvla_data.get("monthOfFirstRegistration") or dvla_data.get("yearOfManufacture")
    if reg_date:
        try:
            return int(str(reg_date)[:4])
        except (ValueError, TypeError):
            pass
    return None


def _median(sorted_list: list) -> float:
    n = len(sorted_list)
    if n == 0:
        return 0.0
    mid = n // 2
    return sorted_list[mid] if n % 2 else (sorted_list[mid - 1] + sorted_list[mid]) / 2
