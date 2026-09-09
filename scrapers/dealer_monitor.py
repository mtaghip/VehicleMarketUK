"""
Monitor specific AutoTrader dealer pages to detect sold stock.

AutoTrader dealer profile pages are public:
  https://www.autotrader.co.uk/dealers/<county>/<town>/<name>-<id>/

We scrape observed stock; disappearance from search does not establish a sale.
"""
import asyncio
import re
import logging
from datetime import datetime, timedelta
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import MonitoredDealer, DealerListing
from config import settings
from .base import USER_AGENTS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.autotrader.co.uk"


def _parse_price(text: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _parse_mileage(text: str) -> Optional[int]:
    m = re.search(r"([\d,]+)\s*miles?", text or "", re.I)
    return int(m.group(1).replace(",", "")) if m else None


def _parse_year(text: str) -> Optional[int]:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group()) if m else None


def _extract_dealer_id(url: str) -> str:
    """Pull the numeric dealer ID from the end of an AutoTrader dealer URL."""
    m = re.search(r"-(\d+)/?$", url.rstrip("/"))
    return m.group(1) if m else url.split("/")[-1]


async def scrape_dealer(
    session: AsyncSession,
    dealer: MonitoredDealer,
) -> dict:
    """Scrape a single dealer's current stock and reconcile with DB."""
    import random
    ua = random.choice(USER_AGENTS)

    found_ids: set[str] = set()
    new_count = sold_count = 0

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

        try:
            # Build search URL for this dealer's stock
            dealer_id = dealer.autotrader_dealer_id or _extract_dealer_id(dealer.autotrader_url or "")
            if dealer_id:
                search_url = f"{BASE_URL}/car-search?advertising-location=at_profile_dealer&dealer-id={dealer_id}&page=1"
            elif dealer.autotrader_url:
                search_url = dealer.autotrader_url
            else:
                logger.warning(f"No URL or ID for dealer {dealer.name}")
                return {"new": 0, "sold": 0, "total": 0}

            page_num = 1
            while page_num <= 20:  # cap at 20 pages per dealer
                url = search_url.replace("page=1", f"page={page_num}") if page_num > 1 else search_url
                page = await ctx.new_page()

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2)

                    # Cookie consent on first page
                    if page_num == 1:
                        try:
                            for sel in ["#onetrust-accept-btn-handler", "button:has-text('Accept all')"]:
                                btn = page.locator(sel)
                                if await btn.count() > 0:
                                    await btn.first.click()
                                    await asyncio.sleep(1)
                                    break
                        except Exception:
                            pass

                    html = await page.content()
                    soup = BeautifulSoup(html, "lxml")

                    cards = soup.select(
                        "li.search-page__result, "
                        "article[data-testid='search-listing-card'], "
                        "section.product-card"
                    )

                    if not cards:
                        raise RuntimeError("Dealer page has no verifiable listings")

                    parsed_count = 0
                    for card in cards:
                        listing = _parse_card(card)
                        if not listing:
                            continue

                        parsed_count += 1
                        found_ids.add(listing["listing_id"])
                        n = await _upsert_dealer_listing(session, dealer.id, listing)
                        if n:
                            new_count += 1

                    if not parsed_count:
                        raise RuntimeError("Dealer cards could not be parsed")
                    # Next page?
                    next_btn = soup.select_one("a[data-testid='pagination-next'], a[aria-label='Next page']")
                    if not next_btn:
                        break
                    if page_num == 20:
                        raise RuntimeError("Dealer page limit reached; inventory coverage is incomplete")
                    page_num += 1
                    await asyncio.sleep(settings.request_delay_seconds)

                finally:
                    await page.close()

        except Exception as e:
            logger.error(f"Dealer scrape error for {dealer.name}: {e}", exc_info=True)
            await session.rollback()
            return {"new": 0, "sold": 0, "total": 0, "success": False, "error": str(e)}
        finally:
            await ctx.close()
            await browser.close()

    # A large unexpected drop may indicate a changed page or missing pagination.
    if dealer.total_stock >= 20 and len(found_ids) < dealer.total_stock * 0.5:
        await session.rollback()
        return {"new": 0, "sold": 0, "total": 0, "success": False,
                "error": "Dealer stock dropped by more than 50%; coverage requires verification"}
    # Discovery only: absent listings remain unverified.
    sold_count = await _mark_dealer_sold(session, dealer.id, found_ids)

    # Update dealer metadata
    dealer.last_scraped = datetime.utcnow()
    dealer.total_stock = len(found_ids)
    await session.commit()

    logger.info(f"Dealer '{dealer.name}': {len(found_ids)} live, {new_count} new, {sold_count} sold")
    return {"new": new_count, "sold": sold_count, "total": len(found_ids), "success": True}


def _parse_card(card) -> Optional[dict]:
    try:
        link = card.select_one("a[href*='/car-details/']")
        if not link:
            return None
        href = link["href"]
        url = BASE_URL + href if href.startswith("/") else href
        m = re.search(r"/car-details/(\d+)", url)
        if not m:
            return None
        listing_id = m.group(1)

        title_el = card.select_one("h3, h2, .listing-title, [data-testid='search-listing-title']")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = card.select_one("[data-testid='search-listing-price'], .product-card-pricing__price")
        price = _parse_price(price_el.get_text() if price_el else "")

        specs = " ".join(el.get_text() for el in card.select("li, .product-card-details__spec-item"))
        mileage = _parse_mileage(specs)
        year = _parse_year(title)

        parts = title.split()
        make = parts[0] if parts else ""
        model = parts[1] if len(parts) > 1 else ""

        colour = ""
        cm = re.search(
            r"\b(black|white|silver|grey|red|blue|green|yellow|orange|brown|purple|gold)\b",
            title + " " + specs, re.I,
        )
        if cm:
            colour = cm.group(0).capitalize()

        fuel_type = ""
        for ft in ["Petrol", "Diesel", "Electric", "Hybrid"]:
            if ft.lower() in specs.lower():
                fuel_type = ft
                break

        reg_m = re.search(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3}|[A-Z]\d{1,3}\s?[A-Z]{3})\b", title + " " + specs)
        reg_plate = reg_m.group(0).replace(" ", "") if reg_m else ""

        return {
            "listing_id": listing_id,
            "url": url,
            "title": title,
            "price": price,
            "mileage": mileage,
            "year": year,
            "make": make,
            "model": model,
            "colour": colour,
            "fuel_type": fuel_type,
            "reg_plate": reg_plate,
        }
    except Exception as e:
        logger.debug(f"Card parse error: {e}")
        return None


async def _upsert_dealer_listing(
    session: AsyncSession,
    dealer_id: int,
    data: dict,
) -> bool:
    """Returns True if this is a new listing."""
    result = await session.execute(
        select(DealerListing).where(
            and_(
                DealerListing.dealer_id == dealer_id,
                DealerListing.listing_id == data["listing_id"],
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.last_seen = datetime.utcnow()
        existing.is_active = True
        existing.sold_at = None
        existing.days_to_sell = None
        if data.get("price") and data["price"] != existing.price:
            existing.price = data["price"]
        return False

    listing = DealerListing(
        dealer_id=dealer_id,
        listing_id=data["listing_id"],
        url=data["url"],
        title=data["title"],
        reg_plate=data.get("reg_plate", ""),
        price=data.get("price"),
        mileage=data.get("mileage"),
        year=data.get("year"),
        make=data.get("make", ""),
        model=data.get("model", ""),
        colour=data.get("colour", ""),
        fuel_type=data.get("fuel_type", ""),
        first_seen=datetime.utcnow(),
        last_seen=datetime.utcnow(),
        is_active=True,
    )
    session.add(listing)
    return True


async def _mark_dealer_sold(
    session: AsyncSession,
    dealer_id: int,
    seen_ids: set[str],
    cutoff_minutes: int = 90,
) -> int:
    """Compatibility guard: search absence is not evidence of a sale."""
    return 0


async def scrape_all_dealers(session: AsyncSession) -> dict:
    """Scrape all active monitored dealers."""
    result = await session.execute(
        select(MonitoredDealer.id).where(MonitoredDealer.is_active == True)
    )
    dealer_ids = result.scalars().all()
    totals = {"dealers": len(dealer_ids), "new": 0, "sold": 0, "total_stock": 0, "errors": []}

    for dealer_id in dealer_ids:
        dealer = await session.get(MonitoredDealer, dealer_id)
        if dealer is None:
            continue
        stats = await scrape_dealer(session, dealer)
        if not stats.get("success", True):
            totals["errors"].append({"dealer_id": dealer_id, "error": stats["error"]})
        totals["new"] += stats["new"]
        totals["sold"] += stats["sold"]
        totals["total_stock"] += stats["total"]
        await asyncio.sleep(5)  # polite gap between dealers

    return totals
