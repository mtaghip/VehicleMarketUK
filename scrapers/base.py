"""Base scraper with shared Playwright browser management and retry logic."""
import asyncio
import random
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncGenerator, Optional
from dataclasses import dataclass, field

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import settings

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


@dataclass
class RawListing:
    """Normalised listing data returned from any scraper."""
    listing_id: str
    source: str
    url: str
    make: str = ""
    model: str = ""
    variant: str = ""
    year: Optional[int] = None
    colour: str = ""
    mileage: Optional[int] = None
    fuel_type: str = ""
    transmission: str = ""
    body_type: str = ""
    engine_size: Optional[float] = None
    doors: Optional[int] = None
    reg_plate: str = ""
    price: Optional[int] = None
    location: str = ""
    postcode: str = ""
    seller_type: str = ""
    images_count: int = 0
    description: str = ""
    scraped_at: datetime = field(default_factory=datetime.utcnow)
    # Dealer-intelligence fields
    owners_count: Optional[int] = None
    service_history: str = ""          # Full / Partial / None
    ulez_compliant: Optional[bool] = None
    euro_standard: str = ""            # Euro 6, Euro 5, etc.
    cat_marker: str = ""               # S / N / C / D
    vat_qualifying: Optional[bool] = None
    at_retail_rating: Optional[int] = None


class BaseScraper(ABC):
    source_name: str = "base"

    def __init__(self):
        self._browser: Optional[Browser] = None
        self._playwright = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1366,768",
                "--disable-extensions",
                "--disable-gpu",
                "--lang=en-GB",
            ],
        )
        return self

    async def __aexit__(self, *_):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _new_context(self) -> BrowserContext:
        ua = random.choice(USER_AGENTS)
        ctx = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1366, "height": 768},
            locale="en-GB",
            timezone_id="Europe/London",
            extra_http_headers={
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        # Mask automation signals
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'permissions', {
                get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
            });
        """)
        return ctx

    async def _polite_delay(self, base: float = None):
        delay = base or settings.request_delay_seconds
        jitter = random.uniform(0.5, 1.5)
        await asyncio.sleep(delay * jitter)

    @abstractmethod
    async def scrape(self, max_pages: int = 10) -> AsyncGenerator[RawListing, None]:
        """Yield RawListing objects from the source."""
        ...

    @abstractmethod
    async def search(
        self,
        make: str = "",
        model: str = "",
        year_min: int = None,
        year_max: int = None,
        price_max: int = None,
        max_pages: int = 3,
    ) -> AsyncGenerator[RawListing, None]:
        """Targeted search yielding RawListing objects."""
        ...
