"""
Scrape AutoTrader's public car valuation tool.
URL: https://www.autotrader.co.uk/car-valuation

Returns retail price, market condition, and any visible rating data.
Results are cached in ValuationCache for 24 hours.
"""
import asyncio
import re
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import ValuationCache
from config import settings

logger = logging.getLogger(__name__)

VALUATION_URL = "https://www.autotrader.co.uk/car-valuation"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


@dataclass
class ValuationResult:
    reg: str
    mileage: int
    make: str
    model: str
    year: Optional[int]
    colour: str
    fuel_type: str
    transmission: str
    retail_price: Optional[int]
    trade_price: Optional[int]
    retail_rating: Optional[int]
    avg_days_to_sell: Optional[int]
    market_condition: str
    price_change_pct: Optional[float]
    from_cache: bool = False
    cached_at: Optional[datetime] = None


def _parse_price(text: str) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_rating(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*/\s*100", text or "")
    return int(m.group(1)) if m else None


def _parse_days(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*days?", text or "", re.I)
    return int(m.group(1)) if m else None


def _parse_pct(text: str) -> Optional[float]:
    m = re.search(r"([+-]?\d+\.?\d*)\s*%", text or "")
    return float(m.group(1)) if m else None


async def get_valuation(
    session: AsyncSession,
    reg: str,
    mileage: int,
    force_refresh: bool = False,
) -> Optional[ValuationResult]:
    """
    Return valuation for reg+mileage. Checks cache first (24h TTL).
    """
    reg_clean = reg.upper().replace(" ", "")
    cache_ttl = timedelta(hours=24)

    if not force_refresh:
        cached = await _get_cached(session, reg_clean, mileage, cache_ttl)
        if cached:
            return cached

    result = await _scrape_valuation(reg_clean, mileage)
    if result:
        await _save_cache(session, result)
    return result


async def _get_cached(
    session: AsyncSession,
    reg: str,
    mileage: int,
    ttl: timedelta,
) -> Optional[ValuationResult]:
    cutoff = datetime.utcnow() - ttl
    # Allow ±1000 miles for cache hit
    result = await session.execute(
        select(ValuationCache).where(
            and_(
                ValuationCache.reg == reg,
                ValuationCache.mileage.between(mileage - 1000, mileage + 1000),
                ValuationCache.fetched_at >= cutoff,
            )
        ).order_by(ValuationCache.fetched_at.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return ValuationResult(
        reg=row.reg,
        mileage=row.mileage,
        make=row.make or "",
        model=row.model or "",
        year=row.year,
        colour=row.colour or "",
        fuel_type=row.fuel_type or "",
        transmission=row.transmission or "",
        retail_price=row.retail_price,
        trade_price=row.trade_price,
        retail_rating=row.retail_rating,
        avg_days_to_sell=row.avg_days_to_sell,
        market_condition=row.market_condition or "",
        price_change_pct=row.price_change_pct,
        from_cache=True,
        cached_at=row.fetched_at,
    )


async def _scrape_valuation(reg: str, mileage: int) -> Optional[ValuationResult]:
    import random
    ua = random.choice(USER_AGENTS)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=settings.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=ua,
            viewport={"width": 1366, "height": 768},
            locale="en-GB",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await ctx.new_page()

        try:
            logger.info(f"Fetching AutoTrader valuation for {reg} / {mileage}mi")
            await page.goto(VALUATION_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Accept cookies if prompted
            try:
                for sel in ["#onetrust-accept-btn-handler", "button:has-text('Accept all')"]:
                    btn = page.locator(sel)
                    if await btn.count() > 0:
                        await btn.first.click()
                        await asyncio.sleep(1)
                        break
            except Exception:
                pass

            # Fill in registration
            reg_input = page.locator("input[name='vrm'], input[placeholder*='reg'], input[id*='reg'], input[data-testid*='vrm']")
            if await reg_input.count() == 0:
                reg_input = page.locator("input").first
            await reg_input.fill(reg)
            await asyncio.sleep(0.5)

            # Fill in mileage if field exists
            mileage_input = page.locator("input[name='mileage'], input[placeholder*='mileage'], input[id*='mileage']")
            if await mileage_input.count() > 0:
                await mileage_input.fill(str(mileage))
                await asyncio.sleep(0.5)

            # Submit
            submit = page.locator("button[type='submit'], button:has-text('Get valuation'), button:has-text('Value my car')")
            if await submit.count() > 0:
                await submit.first.click()
            else:
                await page.keyboard.press("Enter")

            await asyncio.sleep(4)

            # If mileage was on next page
            mileage_input2 = page.locator("input[name='mileage'], input[placeholder*='mileage'], input[id*='mileage']")
            if await mileage_input2.count() > 0:
                await mileage_input2.fill(str(mileage))
                submit2 = page.locator("button[type='submit'], button:has-text('Get valuation')")
                if await submit2.count() > 0:
                    await submit2.first.click()
                    await asyncio.sleep(4)

            html = await page.content()
            return _parse_valuation_page(html, reg, mileage)

        except Exception as e:
            logger.error(f"Valuation scrape error for {reg}: {e}", exc_info=True)
            return None
        finally:
            await page.close()
            await ctx.close()
            await browser.close()


def _parse_valuation_page(html: str, reg: str, mileage: int) -> Optional[ValuationResult]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")

    # Retail price — look for "£X,XXX" near "retail" or "private"
    retail_price = None
    trade_price = None

    price_els = soup.select("[data-testid*='retail-price'], [class*='retail-price'], [class*='RetailPrice']")
    if price_els:
        retail_price = _parse_price(price_els[0].get_text())

    trade_els = soup.select("[data-testid*='trade-price'], [class*='trade-price'], [class*='TradePrice'], [class*='part-ex']")
    if trade_els:
        trade_price = _parse_price(trade_els[0].get_text())

    # Fallback: scan all price-looking elements
    if not retail_price:
        for el in soup.select("[class*='price'], [class*='Price'], [class*='valuation'], [class*='Valuation']"):
            p = _parse_price(el.get_text())
            if p and 500 < p < 500000:
                if not retail_price:
                    retail_price = p
                elif not trade_price and p < retail_price:
                    trade_price = p
                    break

    # Rating
    rating_el = soup.select_one("[class*='retail-rating'], [class*='RetailRating'], [data-testid*='rating']")
    retail_rating = _parse_rating(rating_el.get_text() if rating_el else text)

    # Days to sell
    days_el = soup.select_one("[class*='days-to-sell'], [data-testid*='days']")
    avg_days = _parse_days(days_el.get_text() if days_el else "")
    if not avg_days:
        m = re.search(r"average[^.]*?(\d+)\s*days?", text, re.I)
        if m:
            avg_days = int(m.group(1))

    # Market condition
    condition = ""
    for phrase in ["Higher demand", "Lower demand", "Normal demand", "Strong demand", "Weak demand", "In high demand"]:
        if phrase.lower() in text.lower():
            condition = phrase
            break

    # Price trend
    price_change_pct = None
    trend_el = soup.select_one("[class*='trend'], [class*='Trend'], [data-testid*='trend']")
    if trend_el:
        price_change_pct = _parse_pct(trend_el.get_text())

    # Vehicle info from page
    make = model = colour = fuel_type = transmission = ""
    year = None

    vehicle_el = soup.select_one("[class*='vehicle-title'], [class*='VehicleTitle'], h1, h2")
    if vehicle_el:
        vtitle = vehicle_el.get_text(strip=True)
        year_m = re.search(r"\b(19|20)\d{2}\b", vtitle)
        if year_m:
            year = int(year_m.group())
        parts = vtitle.split()
        if parts:
            make = parts[0]
        if len(parts) > 1:
            model = parts[1]

    colour_m = re.search(
        r"\b(black|white|silver|grey|gray|red|blue|green|yellow|orange|brown|purple|gold|bronze|pink|cream|burgundy|navy|maroon|beige)\b",
        text, re.I,
    )
    if colour_m:
        colour = colour_m.group(0).capitalize()

    for ft in ["Petrol", "Diesel", "Electric", "Hybrid"]:
        if ft.lower() in text.lower():
            fuel_type = ft
            break

    if re.search(r"\bmanual\b", text, re.I):
        transmission = "Manual"
    elif re.search(r"\bautomatic\b", text, re.I):
        transmission = "Automatic"

    if not retail_price:
        logger.warning(f"Could not extract retail price for {reg} — page may have changed")
        return None

    return ValuationResult(
        reg=reg,
        mileage=mileage,
        make=make,
        model=model,
        year=year,
        colour=colour,
        fuel_type=fuel_type,
        transmission=transmission,
        retail_price=retail_price,
        trade_price=trade_price,
        retail_rating=retail_rating,
        avg_days_to_sell=avg_days,
        market_condition=condition,
        price_change_pct=price_change_pct,
        from_cache=False,
    )


async def _save_cache(session: AsyncSession, result: ValuationResult):
    row = ValuationCache(
        reg=result.reg,
        mileage=result.mileage,
        make=result.make,
        model=result.model,
        year=result.year,
        colour=result.colour,
        fuel_type=result.fuel_type,
        transmission=result.transmission,
        retail_price=result.retail_price,
        trade_price=result.trade_price,
        retail_rating=result.retail_rating,
        avg_days_to_sell=result.avg_days_to_sell,
        market_condition=result.market_condition,
        price_change_pct=result.price_change_pct,
        fetched_at=datetime.utcnow(),
    )
    session.add(row)
    await session.commit()
