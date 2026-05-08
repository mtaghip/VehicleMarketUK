"""Car and Classic scraper using Playwright."""
import asyncio
import re
import logging
from typing import AsyncGenerator, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from .base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.carandclassic.com"
SEARCH_URL = f"{BASE_URL}/search"


def _parse_price(text: str) -> Optional[int]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_mileage(text: str) -> Optional[int]:
    m = re.search(r"([\d,]+)\s*miles?", text or "", re.I)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _parse_year(text: str) -> Optional[int]:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group()) if m else None


class CarAndClassicScraper(BaseScraper):
    source_name = "carandclassic"

    def _build_search_url(
        self,
        make: str = "",
        model: str = "",
        year_min: int = None,
        year_max: int = None,
        price_max: int = None,
        page: int = 1,
    ) -> str:
        params: dict = {
            "page": page,
            "sort": "date_listed_desc",
            "country_code": "GB",
        }
        if make:
            params["make"] = make
        if model:
            params["model"] = model
        if year_min:
            params["year_from"] = year_min
        if year_max:
            params["year_to"] = year_max
        if price_max:
            params["price_to"] = price_max
        return f"{SEARCH_URL}?{urlencode(params)}"

    async def _parse_card(self, card) -> Optional[RawListing]:
        try:
            link = card.select_one("a[href]")
            if not link:
                return None
            href = link["href"]
            url = BASE_URL + href if href.startswith("/") else href

            # ID from URL slug
            listing_id = re.sub(r"[^a-zA-Z0-9\-]", "", href.rstrip("/").split("/")[-1])[:64]

            title_el = card.select_one("h2, h3, .car-title, .listing-title, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""

            year = _parse_year(title)
            parts = title.split()
            make = model = variant = ""
            if parts:
                make = parts[0]
            if len(parts) > 1:
                model = parts[1]
            if len(parts) > 2:
                variant = " ".join(parts[2:])

            price_el = card.select_one("[class*='price'], .price")
            price = _parse_price(price_el.get_text(strip=True) if price_el else "")

            # Specs
            specs_text = card.get_text(separator=" ")
            mileage = _parse_mileage(specs_text)

            transmission = ""
            if re.search(r"\bmanual\b", specs_text, re.I):
                transmission = "Manual"
            elif re.search(r"\bautomatic\b", specs_text, re.I):
                transmission = "Automatic"

            fuel_type = ""
            for ft in ["Petrol", "Diesel", "Electric", "Hybrid"]:
                if ft.lower() in specs_text.lower():
                    fuel_type = ft
                    break

            colour = ""
            colour_match = re.search(
                r"\b(black|white|silver|grey|gray|red|blue|green|yellow|orange|brown|purple|gold|bronze|pink|cream|burgundy|navy|maroon|beige)\b",
                specs_text, re.I,
            )
            if colour_match:
                colour = colour_match.group(0).capitalize()

            location_el = card.select_one("[class*='location'], [class*='seller']")
            location = location_el.get_text(strip=True) if location_el else ""

            return RawListing(
                listing_id=listing_id,
                source="carandclassic",
                url=url,
                make=make,
                model=model,
                variant=variant,
                year=year,
                colour=colour,
                mileage=mileage,
                fuel_type=fuel_type,
                transmission=transmission,
                price=price,
                location=location,
                seller_type="private",
            )
        except Exception as e:
            logger.debug(f"C&C parse error: {e}")
            return None

    async def _scrape_page(self, ctx, url: str) -> tuple[list[RawListing], bool]:
        page = await ctx.new_page()
        listings = []
        has_next = False
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Cookie consent
            try:
                for selector in ["button:has-text('Accept all')", "button:has-text('Accept All')", "#accept-cookies"]:
                    btn = page.locator(selector)
                    if await btn.count() > 0:
                        await btn.first.click()
                        await asyncio.sleep(1)
                        break
            except Exception:
                pass

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            cards = soup.select(
                "article.listing-card, "
                "div.car-listing, "
                "li.search-result, "
                "[class*='listing-card'], "
                "[data-testid='listing-card']"
            )
            logger.info(f"C&C: found {len(cards)} cards on {url}")

            for card in cards:
                listing = await self._parse_card(card)
                if listing:
                    listings.append(listing)

            next_btn = soup.select_one("a[aria-label='Next'], a[rel='next'], .pagination-next a")
            has_next = next_btn is not None

        except Exception as e:
            logger.warning(f"C&C page error ({url}): {e}")
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
