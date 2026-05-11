"""AutoTrader UK scraper using Playwright."""
import asyncio
import re
import logging
from typing import AsyncGenerator, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag
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
    m = re.search(r"([\d,]+)\s*miles?", text, re.I)
    return int(m.group(1).replace(",", "")) if m else None


def _parse_year(text: str) -> Optional[int]:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group()) if m else None


def _parse_engine(text: str) -> Optional[float]:
    m = re.search(r"(\d+\.\d+|\d+)\s*[Ll]", text or "")
    return float(m.group(1)) if m else None


def _extract_colour(text: str) -> str:
    m = re.search(
        r"\b(black|white|silver|grey|gray|red|blue|green|yellow|orange|brown|"
        r"purple|gold|bronze|pink|cream|burgundy|navy|maroon|beige)\b",
        text, re.I,
    )
    return m.group(0).capitalize() if m else ""


def _extract_reg_plate(text: str) -> str:
    """Extract UK registration plate (current or older format)."""
    m = re.search(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3})\b", text)
    if m:
        return m.group(1).replace(" ", "").upper()
    m = re.search(r"\b([A-Z]\d{1,3}\s?[A-Z]{3})\b", text)
    return m.group(1).replace(" ", "").upper() if m else ""


def _extract_owners(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s+previous\s+owner", text, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"\b1\s+owner\b|\bone\s+owner\b|\bonly\s+owner\b", text, re.I):
        return 1
    return None


def _extract_service_history(text: str) -> str:
    t = text.lower()
    if "full service history" in t or "full s/h" in t:
        return "Full"
    if "partial service history" in t or "part service history" in t:
        return "Partial"
    if "no service history" in t or "no s/h" in t:
        return "None"
    return ""


def _extract_ulez(text: str) -> Optional[bool]:
    t = text.lower()
    if "ulez compliant" in t or "ulez exempt" in t:
        return True
    if "non-ulez" in t or "ulez non-compliant" in t or "not ulez" in t:
        return False
    return None


def _extract_euro_standard(text: str) -> str:
    m = re.search(r"\beuro\s*(\d+[a-z]?)\b", text, re.I)
    return f"Euro {m.group(1).upper()}" if m else ""


def _extract_cat_marker(text: str) -> str:
    m = re.search(r"\bcat(?:egory)?\s+([cdsn])\b", text, re.I)
    return m.group(1).upper() if m else ""


def _extract_vat(text: str) -> Optional[bool]:
    t = text.lower()
    if "vat qualifying" in t or "inc. vat" in t:
        return True
    if "no vat" in t or "vat exempt" in t or "vat n/a" in t:
        return False
    return None


class AutoTraderScraper(BaseScraper):
    source_name = "autotrader"

    def _build_search_url(
        self,
        make: str = "",
        model: str = "",
        year_min: int = None,
        year_max: int = None,
        price_min: int = None,
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
        if price_min:
            params["price-from"] = price_min
        if price_max:
            params["price-to"] = price_max
        return f"{SEARCH_URL}?{urlencode(params)}"

    def _parse_listing_from_link(self, link_tag: Tag) -> Optional[RawListing]:
        """
        Build a RawListing starting from a known /car-details/ anchor,
        then walking up to collect the surrounding card's data.
        This is more robust than trying to guess the card container selector.
        """
        try:
            href = link_tag.get("href", "")
            url = BASE_URL + href if href.startswith("/") else href
            m = re.search(r"/car-details/(\d+)", url)
            if not m:
                return None
            listing_id = m.group(1)

            # Walk up to the nearest meaningful container (li, article, section)
            container: Tag = link_tag
            for _ in range(8):
                p = container.parent
                if p is None:
                    break
                container = p
                if container.name in ("li", "article", "section"):
                    break

            full_text = container.get_text(separator=" ", strip=True)

            # Title — prefer heading elements inside the container
            title = ""
            for heading in container.select("h2, h3, h1, [class*='title'], [class*='Title']"):
                t = heading.get_text(strip=True)
                if len(t) > 5:
                    title = t
                    break
            if not title:
                title = link_tag.get_text(strip=True)

            year = _parse_year(title) or _parse_year(full_text)

            # Make / model from title
            parts = title.split()
            make = model = variant = ""
            if year:
                parts = [p for p in parts if p != str(year)]
            if parts:
                make = parts[0]
            if len(parts) > 1:
                model = parts[1]
            if len(parts) > 2:
                variant = " ".join(parts[2:])

            # Price
            price = None
            for price_el in container.select(
                "[data-testid*='price'], [class*='price'], [class*='Price']"
            ):
                p = _parse_price(price_el.get_text())
                if p and 200 < p < 2_000_000:
                    price = p
                    break

            # Specs text from list items
            specs_text = " ".join(
                el.get_text(strip=True)
                for el in container.select("li, [class*='spec'], [class*='Spec']")
            ) or full_text

            mileage = _parse_mileage(specs_text)
            engine_size = _parse_engine(specs_text)

            transmission = ""
            if re.search(r"\bmanual\b", specs_text, re.I):
                transmission = "Manual"
            elif re.search(r"\bautomatic\b|\bauto\b", specs_text, re.I):
                transmission = "Automatic"

            fuel_type = ""
            for ft in ["Plug-in Hybrid", "Hybrid", "Electric", "Diesel", "Petrol"]:
                if ft.lower() in specs_text.lower():
                    fuel_type = ft
                    break

            colour = _extract_colour(full_text)

            location_el = container.select_one(
                "[class*='location'], [class*='Location'], "
                "[class*='seller'], [class*='Seller'], [data-testid*='seller']"
            )
            location = location_el.get_text(strip=True) if location_el else ""

            seller_type = "private" if re.search(r"\bprivate\b", full_text, re.I) else "dealer"
            images_count = len(container.select("img"))

            # Dealer-intelligence fields
            reg_plate = _extract_reg_plate(full_text)
            owners_count = _extract_owners(full_text)
            service_history = _extract_service_history(full_text)
            ulez_compliant = _extract_ulez(full_text)
            euro_standard = _extract_euro_standard(full_text)
            cat_marker = _extract_cat_marker(full_text)
            vat_qualifying = _extract_vat(full_text)

            # AT Retail Rating (e.g. "97/100" near a rating element)
            at_retail_rating = None
            rating_m = re.search(r"\b(\d{1,3})/100\b", full_text)
            if rating_m:
                val = int(rating_m.group(1))
                if 1 <= val <= 100:
                    at_retail_rating = val

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
                reg_plate=reg_plate,
                owners_count=owners_count,
                service_history=service_history,
                ulez_compliant=ulez_compliant,
                euro_standard=euro_standard,
                cat_marker=cat_marker,
                vat_qualifying=vat_qualifying,
                at_retail_rating=at_retail_rating,
            )
        except Exception as e:
            logger.debug(f"AT parse error: {e}")
            return None

    async def _scrape_page(self, ctx, url: str) -> tuple[list[RawListing], bool]:
        page = await ctx.new_page()
        listings = []
        has_next = False
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Accept cookies first
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

            # Wait for at least one car-details link to appear
            try:
                await page.wait_for_selector("a[href*='/car-details/']", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1)

            title = await page.title()
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # Link-first approach: find every /car-details/ anchor, deduplicate by listing ID
            all_links = soup.select("a[href*='/car-details/']")
            seen_ids: set[str] = set()
            for link in all_links:
                href = link.get("href", "")
                m = re.search(r"/car-details/(\d+)", href)
                if not m:
                    continue
                lid = m.group(1)
                if lid in seen_ids:
                    continue
                seen_ids.add(lid)
                raw = self._parse_listing_from_link(link)
                if raw:
                    listings.append(raw)

            logger.info(
                f"AutoTrader: '{title}' — {len(all_links)} links → "
                f"{len(listings)} valid listings on {url}"
            )

            next_btn = soup.select_one(
                "a[data-testid='pagination-next'], "
                "a[aria-label='Next page'], "
                "a[aria-label='next']"
            )
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
        price_min: int = None,
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
                        price_min=price_min, price_max=price_max,
                        page=page_num,
                    )
                    listings, has_next = await self._scrape_page(ctx, url)
                    for listing in listings:
                        yield listing
                    if not has_next:
                        break
                    await self._polite_delay()
            finally:
                await ctx.close()
