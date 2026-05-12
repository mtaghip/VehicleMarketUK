"""
Monitor specific AutoTrader dealer pages to detect sold stock.

AutoTrader dealer profile pages are public:
  https://www.autotrader.co.uk/dealers/<county>/<town>/<name>-<id>/

We scrape their current listings each cycle and mark any that vanished as sold.
Uses the same link-first approach as the main AutoTrader scraper so it stays
robust against layout changes.
"""
import asyncio
import re
import logging
from datetime import datetime, timedelta
from typing import Optional

from bs4 import BeautifulSoup, Tag
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
    m = re.search(r"-(\d+)/?$", url.rstrip("/"))
    return m.group(1) if m else url.split("/")[-1]


def _parse_listing_from_link(link_tag: Tag) -> Optional[dict]:
    """
    Link-first parser — same approach as AutoTraderScraper._parse_listing_from_link.
    Walk up from a /car-details/ anchor to collect the surrounding card's data.
    """
    try:
        href = link_tag.get("href", "")
        url = BASE_URL + href if href.startswith("/") else href
        m = re.search(r"/car-details/(\d+)", url)
        if not m:
            return None
        listing_id = m.group(1)

        # Walk up to the nearest meaningful container
        container: Tag = link_tag
        for _ in range(8):
            p = container.parent
            if p is None:
                break
            container = p
            if container.name in ("li", "article", "section"):
                break

        full_text = container.get_text(separator=" ", strip=True)

        # Title
        title = ""
        for heading in container.select("h2, h3, h1, [class*='title'], [class*='Title']"):
            t = heading.get_text(strip=True)
            if len(t) > 5:
                title = t
                break
        if not title:
            title = link_tag.get_text(strip=True)

        year = _parse_year(title) or _parse_year(full_text)
        parts = title.split()
        if year:
            parts = [p for p in parts if p != str(year)]
        make  = parts[0] if parts else ""
        model = parts[1] if len(parts) > 1 else ""

        # Price
        price = None
        for price_el in container.select("[data-testid*='price'], [class*='price'], [class*='Price']"):
            p = _parse_price(price_el.get_text())
            if p and 200 < p < 2_000_000:
                price = p
                break

        specs_text = " ".join(
            el.get_text(strip=True)
            for el in container.select("li, [class*='spec'], [class*='Spec']")
        ) or full_text

        mileage = _parse_mileage(specs_text)

        fuel_type = ""
        for ft in ["Plug-in Hybrid", "Hybrid", "Electric", "Diesel", "Petrol"]:
            if ft.lower() in specs_text.lower():
                fuel_type = ft
                break

        colour = ""
        cm = re.search(
            r"\b(black|white|silver|grey|gray|red|blue|green|yellow|orange|"
            r"brown|purple|gold|bronze|pink|cream|burgundy|navy|maroon|beige)\b",
            full_text, re.I,
        )
        if cm:
            colour = cm.group(0).capitalize()

        reg_m = re.search(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3}|[A-Z]\d{1,3}\s?[A-Z]{3})\b",
                          full_text)
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
        logger.debug(f"Dealer card parse error: {e}")
        return None


async def scrape_dealer(
    session: AsyncSession,
    dealer: MonitoredDealer,
) -> dict:
    """Scrape a single dealer's current stock and reconcile with DB."""
    import random
    ua = random.choice(USER_AGENTS)

    found_ids: set[str] = set()
    new_count = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=settings.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=ua,
            viewport={"width": 1366, "height": 768},
            locale="en-GB",
            timezone_id="Europe/London",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        try:
            dealer_id = dealer.autotrader_dealer_id or _extract_dealer_id(dealer.autotrader_url or "")
            if dealer_id:
                base_search = (
                    f"{BASE_URL}/car-search"
                    f"?advertising-location=at_profile_dealer"
                    f"&dealer-id={dealer_id}"
                )
            elif dealer.autotrader_url:
                base_search = dealer.autotrader_url
            else:
                logger.warning(f"No URL or ID for dealer '{dealer.name}'")
                return {"new": 0, "sold": 0, "total": 0}

            page_num = 1
            while page_num <= 20:
                url = f"{base_search}&page={page_num}"
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)

                    # Accept cookies on first page
                    if page_num == 1:
                        try:
                            for sel in [
                                "#onetrust-accept-btn-handler",
                                "button:has-text('Accept all')",
                                "button:has-text('Accept All')",
                            ]:
                                btn = page.locator(sel)
                                if await btn.count() > 0:
                                    await btn.first.click()
                                    await asyncio.sleep(1)
                                    break
                        except Exception:
                            pass

                    # Wait for at least one listing link
                    try:
                        await page.wait_for_selector(
                            "a[href*='/car-details/']", timeout=10000
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                    html = await page.content()
                    soup = BeautifulSoup(html, "lxml")

                    # Link-first: find every /car-details/ anchor, deduplicate by ID
                    all_links = soup.select("a[href*='/car-details/']")
                    seen_on_page: set[str] = set()
                    page_listings = []
                    for link in all_links:
                        href = link.get("href", "")
                        m = re.search(r"/car-details/(\d+)", href)
                        if not m or m.group(1) in seen_on_page:
                            continue
                        seen_on_page.add(m.group(1))
                        listing = _parse_listing_from_link(link)
                        if listing:
                            page_listings.append(listing)

                    if not page_listings:
                        snippet = soup.get_text(separator=" ", strip=True)[:300]
                        logger.warning(
                            f"Dealer '{dealer.name}' page {page_num}: 0 listings found. "
                            f"Snippet: {snippet}"
                        )
                        break

                    logger.info(
                        f"Dealer '{dealer.name}' page {page_num}: "
                        f"{len(page_listings)} listings found"
                    )

                    for listing in page_listings:
                        found_ids.add(listing["listing_id"])
                        n = await _upsert_dealer_listing(session, dealer.id, listing)
                        if n:
                            new_count += 1

                    next_btn = soup.select_one(
                        "a[data-testid='pagination-next'], a[aria-label='Next page'], a[aria-label='next']"
                    )
                    if not next_btn:
                        break
                    page_num += 1
                    await asyncio.sleep(settings.request_delay_seconds)

                finally:
                    await page.close()

        except Exception as e:
            logger.error(f"Dealer scrape error for '{dealer.name}': {e}", exc_info=True)
        finally:
            await ctx.close()
            await browser.close()

    sold_count = await _mark_dealer_sold(session, dealer.id, found_ids)

    dealer.last_scraped = datetime.utcnow()
    dealer.total_stock = len(found_ids)
    await session.commit()

    logger.info(
        f"Dealer '{dealer.name}': {len(found_ids)} live, "
        f"{new_count} new, {sold_count} sold"
    )
    return {"new": new_count, "sold": sold_count, "total": len(found_ids)}


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
        if data.get("price") and data["price"] != existing.price:
            existing.price = data["price"]
        return False

    session.add(DealerListing(
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
    ))
    return True


async def _mark_dealer_sold(
    session: AsyncSession,
    dealer_id: int,
    seen_ids: set[str],
    cutoff_minutes: int = 90,
) -> int:
    cutoff = datetime.utcnow() - timedelta(minutes=cutoff_minutes)
    result = await session.execute(
        select(DealerListing).where(
            and_(
                DealerListing.dealer_id == dealer_id,
                DealerListing.is_active == True,
                DealerListing.last_seen < cutoff,
            )
        )
    )
    sold_count = 0
    now = datetime.utcnow()
    for listing in result.scalars().all():
        if listing.listing_id not in seen_ids:
            listing.is_active = False
            listing.sold_at = now
            if listing.first_seen:
                listing.days_to_sell = (now - listing.first_seen).days
            sold_count += 1
    return sold_count


async def scrape_all_dealers(session: AsyncSession) -> dict:
    """Scrape all active monitored dealers."""
    result = await session.execute(
        select(MonitoredDealer).where(MonitoredDealer.is_active == True)
    )
    dealers = result.scalars().all()
    totals = {"dealers": len(dealers), "new": 0, "sold": 0, "total_stock": 0}

    for dealer in dealers:
        stats = await scrape_dealer(session, dealer)
        totals["new"] += stats["new"]
        totals["sold"] += stats["sold"]
        totals["total_stock"] += stats["total"]
        await asyncio.sleep(5)

    return totals
