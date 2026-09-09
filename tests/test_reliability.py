import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, Listing, Source, PriceHistory, DemandSnapshot, ScraperRun, MonitoredDealer, DealerListing
from database.observations import observation_status
from scrapers.base import RawListing
from scrapers.ingestion import run_ingestion, upsert_listing, mark_sold_listings
from scrapers.dealer_monitor import _mark_dealer_sold, _upsert_dealer_listing, scrape_dealer
from scrapers.autotrader import AutoTraderScraper
from scrapers.carandclassic import CarAndClassicScraper
from analytics.demand import compute_demand_signals, save_demand_snapshot
from analytics.pricing import get_price_insights
from api.routes.live import live_stats, _ser_run


async def stream(*items, fail=False):
    for item in items:
        yield item
    if fail:
        raise RuntimeError('page 2 failed')


def raw(identifier='new', **kwargs):
    return RawListing(listing_id=identifier, source='autotrader', url='https://example.test/car/' + identifier,
                      make='Ford', model='Focus', price=10000, **kwargs)


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.now = datetime.utcnow()

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def listing(self, identifier, price=10000, age=0, sold=False):
        item = Listing(listing_id=identifier, source=Source.autotrader, url='https://example.test/' + identifier,
                       make='Ford', model='Focus', year=2020, price=price, is_active=not sold,
                       first_seen=self.now - timedelta(days=age), last_seen=self.now - timedelta(days=age),
                       sold_at=self.now if sold else None, days_to_sell=age if sold else None)
        self.session.add(item)
        await self.session.flush()
        return item

    async def test_limited_discovery_does_not_sell_old_inventory(self):
        old = await self.listing('old', age=10)
        await self.session.commit()
        run = await run_ingestion(self.session, stream(raw()), 'autotrader')
        self.assertTrue(run.success)
        self.assertEqual(run.listings_found, 1)
        self.assertTrue(old.is_active)
        self.assertIsNone(old.sold_at)
        self.assertEqual(await mark_sold_listings(self.session, 'autotrader', set()), 0)

    async def test_empty_run_is_failed_without_mutating_inventory(self):
        old = await self.listing('old', age=10)
        identifier = old.id
        await self.session.commit()
        run = await run_ingestion(self.session, stream(), 'autotrader')
        old = await self.session.get(Listing, identifier)
        self.assertFalse(run.success)
        self.assertIn('No listings', run.error)
        self.assertTrue(old.is_active)
        self.assertEqual(_ser_run(run)['status'], 'failed')

    async def test_partial_failure_rolls_back_even_after_fifty_items(self):
        run = await run_ingestion(self.session, stream(*(raw(str(i)) for i in range(51)), fail=True), 'autotrader')
        self.assertFalse(run.success)
        self.assertEqual(run.listings_found, 51)
        self.assertEqual(run.listings_new, 0)
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(Listing)), 0)
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(ScraperRun)), 1)
        # The session remains usable after failure.
        self.assertTrue((await run_ingestion(self.session, stream(raw()), 'autotrader')).success)

    async def test_duplicate_results_count_once(self):
        run = await run_ingestion(self.session, stream(raw(), raw()), 'autotrader')
        self.assertEqual(run.listings_found, 1)
        self.assertEqual(run.listings_new, 1)

    async def test_abrupt_count_drop_is_unhealthy(self):
        self.session.add(ScraperRun(source=Source.autotrader, started_at=self.now - timedelta(hours=1),
                                   finished_at=self.now, success=True, listings_found=100))
        await self.session.commit()
        run = await run_ingestion(self.session, stream(raw()), 'autotrader')
        self.assertFalse(run.success)
        self.assertIn('50%', run.error)

    async def test_reappearing_listing_clears_sale_metadata(self):
        old = await self.listing('old', age=2, sold=True)
        status, item = await upsert_listing(self.session, raw('old'))
        self.assertEqual(status, 'updated')
        self.assertTrue(item.is_active)
        self.assertIsNone(item.sold_at)
        self.assertIsNone(item.days_to_sell)

    async def test_dealer_absence_is_not_sale_and_reappearance_clears_metadata(self):
        dealer = MonitoredDealer(name='Test')
        self.session.add(dealer)
        await self.session.flush()
        item = DealerListing(dealer_id=dealer.id, listing_id='1', is_active=False,
                             first_seen=self.now - timedelta(days=5), last_seen=self.now - timedelta(days=2),
                             sold_at=self.now, days_to_sell=5)
        self.session.add(item)
        await self.session.flush()
        await _upsert_dealer_listing(self.session, dealer.id, {'listing_id': '1', 'price': 10000})
        self.assertTrue(item.is_active)
        self.assertIsNone(item.sold_at)
        self.assertIsNone(item.days_to_sell)
        self.assertEqual(await _mark_dealer_sold(self.session, dealer.id, set()), 0)
        self.assertTrue(item.is_active)

    async def test_discoveries_include_already_sold_and_snapshot_median_is_real(self):
        await self.listing('a', price=1000)
        await self.listing('b', price=2000)
        await self.listing('c', price=9000)
        await self.listing('d', sold=True)
        signals = await compute_demand_signals(self.session)
        self.assertEqual(signals[0].new_last_24h, 4)
        self.assertEqual(signals[0].avg_days_to_sell, 0)
        await save_demand_snapshot(self.session)
        snapshot = await self.session.scalar(select(DemandSnapshot))
        self.assertEqual(snapshot.avg_price, 4000)
        self.assertEqual(snapshot.median_price, 2000)
        stats = await live_stats(self.session)
        self.assertEqual(stats['new_last_24h'], 4)
        self.assertEqual(stats['avg_days_to_sell'], 0)

    async def test_supply_spike_alone_does_not_imply_demand(self):
        for i in range(5):
            await self.listing(str(i))
        signal = (await compute_demand_signals(self.session))[0]
        self.assertTrue(signal.spike_detected)
        self.assertEqual(signal.demand_score, 0)

    async def test_failed_dealer_navigation_preserves_stock(self):
        dealer = MonitoredDealer(name='Test', autotrader_dealer_id='123', total_stock=50,
                                 last_scraped=self.now - timedelta(days=1))
        self.session.add(dealer)
        await self.session.flush()
        identifier = dealer.id
        await self.session.commit()
        page = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError('blocked')), close=AsyncMock())
        ctx = SimpleNamespace(add_init_script=AsyncMock(), new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=ctx), close=AsyncMock())
        manager = AsyncMock()
        manager.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        with patch('scrapers.dealer_monitor.async_playwright', return_value=manager):
            result = await scrape_dealer(self.session, dealer)
        dealer = await self.session.get(MonitoredDealer, identifier)
        self.assertFalse(result['success'])
        self.assertEqual(dealer.total_stock, 50)
        self.assertEqual(dealer.last_scraped, self.now - timedelta(days=1))
        self.assertEqual(result['sold'], 0)

    async def test_wrong_source_is_rejected(self):
        result = await run_ingestion(self.session, stream(raw()), 'carandclassic')
        self.assertFalse(result.success)
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(Listing)), 0)

    async def test_price_trend_uses_latest_baseline_and_matched_cohort(self):
        a = await self.listing('a', price=9000, age=60)
        b = await self.listing('b', price=18000, age=60)
        await self.listing('new expensive car', price=90000)
        for item, days, price in [(a, 60, 20000), (a, 31, 10000), (b, 31, 20000), (a, 29, 9500)]:
            self.session.add(PriceHistory(listing_id=item.id, price=price, recorded_at=self.now - timedelta(days=days)))
        await self.session.flush()
        insight = await get_price_insights(self.session, make='Ford', model='Focus')
        self.assertEqual(insight.price_change_30d_pct, -10.0)
        self.assertEqual(insight.sample_size, 3)

    async def test_no_baseline_returns_unknown_trend(self):
        await self.listing('a')
        await self.listing('b')
        insight = await get_price_insights(self.session, make='Ford', model='Focus')
        self.assertIsNone(insight.price_change_30d_pct)

    async def test_observation_status_does_not_call_stale_stock_sold(self):
        item = await self.listing('a', age=2)
        self.assertEqual(observation_status(item, self.now), 'unverified')
        stats = await live_stats(self.session)
        self.assertEqual(stats['unverified_listings'], 1)
        self.assertEqual(stats['sold_last_7d'], 0)

    async def test_scrapers_propagate_navigation_errors(self):
        for scraper in (AutoTraderScraper(), CarAndClassicScraper()):
            page = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError('network failed')), close=AsyncMock())
            ctx = SimpleNamespace(new_page=AsyncMock(return_value=page))
            with self.assertRaisesRegex(RuntimeError, 'network failed'):
                await scraper._scrape_page(ctx, 'https://example.test')
            page.close.assert_awaited_once()

    async def test_scrapers_reject_unverifiable_empty_pages(self):
        for scraper in (AutoTraderScraper(), CarAndClassicScraper()):
            page = SimpleNamespace(
                goto=AsyncMock(return_value=SimpleNamespace(status=200)), close=AsyncMock(),
                locator=lambda _: SimpleNamespace(count=AsyncMock(return_value=0)),
                wait_for_selector=AsyncMock(), evaluate=AsyncMock(),
                title=AsyncMock(return_value='Empty'), content=AsyncMock(return_value='<html><body>Unknown page</body></html>'),
            )
            ctx = SimpleNamespace(new_page=AsyncMock(return_value=page))
            with patch('asyncio.sleep', new=AsyncMock()):
                with self.assertRaisesRegex(RuntimeError, 'No valid listings'):
                    await scraper._scrape_page(ctx, 'https://example.test')


if __name__ == '__main__':
    unittest.main()
