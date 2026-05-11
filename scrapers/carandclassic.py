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

    async def _parse_card_from_link(self, link_tag) -> Optional[RawListing]:
        """Link-first parser — walk up from an anchor to collect card data."""
        try:
            href = link_tag.get("href", "")
            url = BASE_URL + href if href.startswith("/") else href
            slug = re.sub(r"[^a-zA-Z0-9\-]", "", href.rstrip("/").split("/")[-1])[:64]
            if not slug:
                return None

            # Walk up to a meaningful container
            container = link_tag
            for _ in range(8):
                p = container.parent
                if p is None:
                    break
                container = p
                if container.name in ("li", "article", "section", "div") and len(
                    container.get_text(strip=True)
                ) > 20:
                    break

            full_text = container.get_text(separator=" ", strip=True)

            title = ""
            for h in container.select("h2, h3, h1, [class*='title'], [class*='Title']"):
                t = h.get_text(strip=True)
                if len(t) > 5:
                    title = t
                    break
            if not title:
                title = link_tag.get_text(strip=True)

            year = _parse_year(title) or _parse_year(full_text)
            parts = [p for p in title.split() if p != str(year)] if year else title.split()
            make = parts[0] if parts else ""
            model = parts[1] if len(parts) > 1 else ""
            variant = " ".join(parts[2:]) if len(parts) > 2 else ""

            price = None
            for el in container.select("[class*='price'], [class*='Price']"):
                p = _parse_price(el.get_text())
                if p and 100 < p < 5_000_000:
                    price = p
                    break

            mileage = _parse_mileage(full_text)
            colour = ""
            cm = re.search(
                r"\b(black|white|silver|grey|red|blue|green|yellow|orange|brown|purple|gold)\b",
                full_text, re.I,
            )
            if cm:
                colour = cm.group(0).capitalize()

            fuel_type = ""
            for ft in ["Petrol", "Diesel", "Electric", "Hybrid"]:
                if ft.lower() in full_text.lower():
                    fuel_type = ft
                    break

            transmission = ""
            if re.search(r"\bmanual\b", full_text, re.I):
                transmission = "Manual"
            elif re.search(r"\bautomatic\b", full_text, re.I):
                transmission = "Automatic"

            location_el = container.select_one("[class*='location'], [class*='seller']")
            location = location_el.get_text(strip=True) if location_el else ""

            if not (make or price):
                return None

            return RawListing(
                listing_id=slug,
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
            logger.debug(f"C&C link parse error: {e}")
            return None

    async def _scrape_page(self, ctx, url: str) -> tuple[list[RawListing], bool]:
        page = await ctx.new_page()
        listings = []
        has_next = False
        try:
            # networkidle waits for the JS SPA to finish rendering listings
            await page.goto(url, wait_until="networkidle", timeout=45000)

            # Cookie consent
            try:
                for selector in [
                    "button:has-text('Accept all')",
                    "button:has-text('Accept All')",
                    "button:has-text('I Accept')",
                    "#accept-cookies",
                    "[id*='cookie'] button",
                ]:
                    btn = page.locator(selector)
                    if await btn.count() > 0:
                        await btn.first.click()
                        await asyncio.sleep(2)
                        break
            except Exception:
                pass

            # Wait for listing content or settle
            try:
                await page.wait_for_selector(
                    "article, [class*='listing'], [class*='vehicle'], [class*='car-card']",
                    timeout=10000,
                )
            except Exception:
                pass

            # Scroll to trigger any lazy-loaded content
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(2)

            title = await page.title()
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # Link-first: find all car detail links, deduplicate by slug
            all_links = soup.select("a[href*='/car/'], a[href*='/listing/'], a[href*='/classic-cars/']")
            # Also try any link that leads to a detail page (contains slug pattern)
            if not all_links:
                all_links = soup.select("a[href]")
                all_links = [
                    l for l in all_links
                    if re.search(r"/[a-z0-9\-]+/\d+", l.get("href", ""))
                    and "search" not in l.get("href", "")
                ]

            seen_slugs: set[str] = set()
            for link in all_links:
                href = link.get("href", "")
                slug = href.rstrip("/").split("/")[-1]
                if not slug or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                listing = await self._parse_card_from_link(link)
                if listing:
                    listings.append(listing)

            # Fallback: container-based approach
            if not listings:
                cards = soup.select(
                    "article, [class*='listing-card'], [class*='ListingCard'], "
                    "[class*='car-card'], [data-testid='listing-card']"
                )
                for card in cards:
                    listing = await self._parse_card(card)
                    if listing:
                        listings.append(listing)

            logger.info(f"C&C: '{title}' — {len(listings)} listings on {url}")
            if not listings:
                snippet = soup.get_text(separator=" ", strip=True)[:300]
                logger.warning(f"C&C: 0 listings — snippet: {snippet}")

            next_btn = soup.select_one(
                "a[aria-label='Next'], a[rel='next'], "
                "[class*='pagination'] a[href*='page='], a[aria-label='next page']"
            )
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
