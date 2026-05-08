"""AutoTrader UK scraper using Playwright."""
import asyncio
import re
import logging
from typing import AsyncGenerator, Optional
from urllib.parse import urlencode, urlparse, parse_qs

from bs4 import BeautifulSoup
from .base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.autotrader.co.uk"
SEARCH_URL = f"{BASE_URL}/car-search"


def _parse_price(text: str) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_mileage(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"([\d,]+)\s*miles?", text, re.I)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _parse_year(text: str) -> Optional[int]:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group()) if m else None


def _parse_engine(text: str) -> Optional[float]:
    m = re.search(r"(\d+\.\d+|\d+)\s*[Ll]", text or "")
    return float(m.group(1)) if m else None


class AutoTraderScraper(BaseScraper):
    source_name = "autotrader"

    def _build_search_url(
        self,
        make: str = "",
        model: str = "",
        year_min: int = None,
        year_max: int = None,
        price_max: int = None,
        page: int = 1,
        postcode: str = "SW1A1AA",
        radius: int = 1500,
    ) -> str:
        params = {
            "postcode": postcode,
            "radius": radius,
            "sort": "date-desc",
            "page": page,
        }
        if make:
            params["make"] = make.upper()
        if model:
            params["model"] = model.upper()
        if year_min:
            params["year-from"] = year_min
        if year_max:
            params["year-to"] = year_max
        if price_max:
            params["price-to"] = price_max
        return f"{SEARCH_URL}?{urlencode(params)}"

    async def _parse_listing_card(self, card) -> Optional[RawListing]:
        try:
            # Extract listing URL and ID
            link = card.select_one("a[href*='/car-details/']")
            if not link:
                return None
            url = BASE_URL + link["href"] if link["href"].startswith("/") else link["href"]
            listing_id_match = re.search(r"/car-details/(\d+)", url)
            if not listing_id_match:
                return None
            listing_id = listing_id_match.group(1)

            # Title — "2019 BMW 3 Series 320d M Sport"
            title_el = card.select_one("h3[data-testid='search-listing-title'], h2.listing-title, .product-card-details__title")
            title = title_el.get_text(strip=True) if title_el else ""

            year = _parse_year(title)
            parts = title.split()
            make = ""
            model = ""
            variant = ""
            if year and len(parts) >= 3:
                # Remove year token
                year_str = str(year)
                remaining = [p for p in parts if p != year_str]
                if remaining:
                    make = remaining[0]
                if len(remaining) > 1:
                    model = remaining[1]
                if len(remaining) > 2:
                    variant = " ".join(remaining[2:])

            # Price
            price_el = card.select_one("[data-testid='search-listing-price'], .product-card-pricing__price")
            price = _parse_price(price_el.get_text(strip=True) if price_el else "")

            # Key specs: year, mileage, engine, transmission, fuel
            specs_text = " ".join(
                el.get_text(strip=True)
                for el in card.select(".product-card-details__spec-item, [data-testid='search-listing-specs'] li")
            )
            mileage = _parse_mileage(specs_text)
            engine_size = _parse_engine(specs_text)

            transmission = ""
            if re.search(r"\bmanual\b", specs_text, re.I):
                transmission = "Manual"
            elif re.search(r"\bautomatic\b|\bauto\b", specs_text, re.I):
                transmission = "Automatic"

            fuel_type = ""
            for ft in ["Petrol", "Diesel", "Electric", "Hybrid", "Plug-in Hybrid"]:
                if ft.lower() in specs_text.lower():
                    fuel_type = ft
                    break

            # Location
            location_el = card.select_one(".product-card-seller-info__name, [data-testid='search-listing-seller']")
            location = location_el.get_text(strip=True) if location_el else ""

            # Colour (often in specs or title)
            colour = ""
            colour_match = re.search(
                r"\b(black|white|silver|grey|gray|red|blue|green|yellow|orange|brown|purple|gold|bronze|pink|cream|burgundy|navy|maroon|beige)\b",
                title + " " + specs_text,
                re.I,
            )
            if colour_match:
                colour = colour_match.group(0).capitalize()

            # Seller type
            seller_el = card.select_one(".product-card-seller-info__private-badge, [data-testid='seller-type']")
            seller_type = "private" if seller_el and "private" in seller_el.get_text(strip=True).lower() else "dealer"

            # Images
            img_els = card.select("img[src*='images.autotrader']")
            images_count = len(img_els)

            return RawListing(
                listing_id=listing_id,
                source="autotrader",
                url=url,
                make=make,
                model=model,
                variant=variant,
                year=year,
                colour=colour,
                mileage=mileage,
                fuel_type=fuel_type,
                transmission=transmission,
                engine_size=engine_size,
                price=price,
                location=location,
                seller_type=seller_type,
                images_count=images_count,
            )
        except Exception as e:
            logger.debug(f"Error parsing card: {e}")
            return None

    async def _scrape_page(self, ctx, url: str) -> tuple[list[RawListing], bool]:
        page = await ctx.new_page()
        listings = []
        has_next = False
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Handle cookie consent
            try:
                consent_btn = page.locator("button:has-text('Accept all'), button:has-text('Accept All'), #onetrust-accept-btn-handler")
                if await consent_btn.count() > 0:
                    await consent_btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            cards = soup.select(
                "li.search-page__result, "
                "article[data-testid='search-listing-card'], "
                "li[data-testid='search-listing'], "
                "section.product-card"
            )
            logger.info(f"AutoTrader: found {len(cards)} cards on {url}")

            for card in cards:
                listing = await self._parse_listing_card(card)
                if listing:
                    listings.append(listing)

            # Check for next page
            next_btn = soup.select_one("a[data-testid='pagination-next'], a.pagination--right__active, a[aria-label='Next page']")
            has_next = next_btn is not None

        except Exception as e:
            logger.warning(f"AutoTrader page error ({url}): {e}")
        finally:
            await page.close()
        return listings, has_next

    async def scrape(self, max_pages: int = 10) -> AsyncGenerator[RawListing, None]:
        async with self:
            ctx = await self._new_context()
            try:
                for page_num in range(1, max_pages + 1):
                    url = self._build_search_url(page=page_num)
                    listings, has_next = await self._scrape_page(ctx, url)
                    for listing in listings:
                        yield listing
                    if not has_next:
                        break
                    await self._polite_delay()
            finally:
                await ctx.close()

    async def search(
        self,
        make: str = "",
        model: str = "",
        year_min: int = None,
        year_max: int = None,
        price_max: int = None,
        max_pages: int = 3,
    ) -> AsyncGenerator[RawListing, None]:
        async with self:
            ctx = await self._new_context()
            try:
                for page_num in range(1, max_pages + 1):
                    url = self._build_search_url(
                        make=make, model=model,
                        year_min=year_min, year_max=year_max,
                        price_max=price_max, page=page_num,
                    )
                    listings, has_next = await self._scrape_page(ctx, url)
                    for listing in listings:
                        yield listing
                    if not has_next:
                        break
                    await self._polite_delay()
            finally:
                await ctx.close()
