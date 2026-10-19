"""
CLI for manual operations.

Usage:
  python cli.py scrape autotrader --pages 5
  python cli.py scrape carandclassic --pages 5
  python cli.py scrape all --pages 5
  python cli.py demand
  python cli.py gems
  python cli.py plate AB12CDE
"""
import asyncio
import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def cmd_scrape(source: str, pages: int):
    from database import init_db, AsyncSessionLocal
    from scrapers import AutoTraderScraper, CarAndClassicScraper
    from scrapers.ingestion import run_ingestion

    await init_db()

    scrapers = []
    if source in ("autotrader", "all"):
        scrapers.append(("autotrader", AutoTraderScraper()))
    if source in ("carandclassic", "all"):
        scrapers.append(("carandclassic", CarAndClassicScraper()))

    for src_name, scraper in scrapers:
        print(f"\n→ Scraping {src_name} ({pages} pages)...")
        async with AsyncSessionLocal() as session:
            run = await run_ingestion(session, scraper.scrape(max_pages=pages), src_name)
        print(f"  {run.listings_new} new, {run.listings_updated} updated, {run.listings_sold} sold")


async def cmd_demand():
    from database import init_db, AsyncSessionLocal
    from analytics import compute_demand_signals

    await init_db()
    async with AsyncSessionLocal() as session:
        signals = await compute_demand_signals(session)

    print(f"\n{'Make':<12} {'Model':<20} {'Score':>6} {'DTS':>8} {'Active':>7} {'Spike':>6}")
    print("-" * 65)
    for s in signals[:25]:
        spike = "🔥" if s.spike_detected else ""
        dts = f"{s.avg_days_to_sell:.1f}d" if s.avg_days_to_sell else "—"
        print(f"{s.make:<12} {s.model:<20} {s.demand_score:>6.1f} {dts:>8} {s.active_count:>7} {spike:>6}")


async def cmd_gems():
    from database import init_db, AsyncSessionLocal
    from analytics import find_underpriced_listings

    await init_db()
    async with AsyncSessionLocal() as session:
        gems = await find_underpriced_listings(session, discount_pct=15.0)

    print(f"\n{'Make':<10} {'Model':<16} {'Year':>5} {'Price':>8} {'Median':>8} {'Disc%':>6} {'Source':<14}")
    print("-" * 75)
    for g in gems[:20]:
        print(f"{g['make'] or '':<10} {g['model'] or '':<16} {g['year'] or '':>5} "
              f"£{g['price']:>7,} £{g['market_median']:>7,} {g['discount_pct']:>5.1f}% {g['source'] or '':<14}")
        print(f"  {g['url']}")


async def cmd_plate(reg: str):
    from database import init_db, AsyncSessionLocal
    from analytics import lookup_plate

    await init_db()
    async with AsyncSessionLocal() as session:
        profile = await lookup_plate(session, reg)

    if not profile:
        print(f"No data found for {reg.upper()}")
        return

    print(f"\n{'='*50}")
    print(f"  {profile.reg}  —  {profile.make} {profile.model}")
    print(f"{'='*50}")
    print(f"  Year:       {profile.year or '—'}")
    print(f"  Colour:     {profile.colour or '—'}")
    print(f"  Fuel:       {profile.fuel_type or '—'}")
    print(f"  Engine:     {profile.engine_cc/1000:.1f}L" if profile.engine_cc else "  Engine:     —")
    print(f"  MOT Expiry: {profile.mot_expiry or '—'}")
    print(f"  Tax Due:    {profile.tax_due or '—'}")
    print()
    print(f"  Desirability Score:   {profile.desirability_score}/100")
    print(f"  Est. Days to Sell:    {profile.estimated_days_to_sell or '—'}")
    print(f"  Market Median Price:  £{profile.median_price:,}" if profile.median_price else "  Market Median:  —")
    print(f"  Active Similar:       {profile.active_listings}")


def cmd_pick(args):
    from buyer import Rules, load_stock_file, pick
    from buyer.report import write_reports
    import pandas as pd

    rules = Rules.load(args.rules, target_profit=args.min_profit, budget_max_buy=args.budget,
                       vat_registered=False if args.no_vat else None)
    cars = load_stock_file(args.file)
    scored, shortlist = pick(cars, rules, top=args.top)

    c, t = rules.costs, rules.targets
    summary = (f"target £{t['target_profit']:,} profit · "
               f"{'VAT-registered (margin scheme)' if c['vat_registered'] else 'no VAT'} · "
               f"fixed costs £{c['fixed_costs']:,} + prep by grade"
               + (f" · budget £{t['budget_max_buy']:,}" if t.get("budget_max_buy") else ""))
    print(f"\n{len(cars)} cars loaded · {summary}")
    if cars["reserve"].isna().all():
        print("No reserve/buy price in this file: BUY means worth buying at or below the max bid shown.")
    print(scored["verdict"].value_counts().to_string())
    print(f"\n{'#':>2} {'Verdict':<8} {'VRM':<9} {'Car':<38} {'Reserve':>9} {'Max bid':>9} {'Net':>7} "
          f"{'Days':>5} {'Rate':>5} {'Bar':>4} Tier")
    for i, r in shortlist.iterrows():
        car = f"{r['make']} {r['model']}"[:38]
        money = lambda v: "—" if pd.isna(v) else f"£{v:,.0f}"
        num = lambda v: "—" if pd.isna(v) else f"{v:.0f}"
        print(f"{i + 1:>2} {r['verdict']:<8} {r['vrm']:<9} {car:<38} {money(r['reserve']):>9} "
              f"{money(r['max_bid']):>9} {money(r['net_at_reserve']):>7} {num(r['avg_days']):>5} "
              f"{num(r['rating']):>5} {num(r['demand_bar']):>4} {r['tier']}")
        for w in r["warnings"]:
            print(f"{'':>12}⚠ {w}")
    paths = write_reports(scored, shortlist, args.out, source_name=args.file, rules_summary=summary)
    print("\nReports:")
    for p in paths.values():
        print(f"  {p}")


def main():
    parser = argparse.ArgumentParser(description="Vehicle Market UK CLI")
    sub = parser.add_subparsers(dest="cmd")

    sc = sub.add_parser("scrape")
    sc.add_argument("source", choices=["autotrader", "carandclassic", "all"])
    sc.add_argument("--pages", type=int, default=5)

    sub.add_parser("demand")
    sub.add_parser("gems")

    pl = sub.add_parser("plate")
    pl.add_argument("reg")

    pk = sub.add_parser("pick", help="Hand-pick cars to buy from a stock/valuation export")
    pk.add_argument("file", help="CSV or Excel export (Auto Trader check, stock check or Motorway)")
    pk.add_argument("--top", type=int, default=10, help="How many cars to shortlist")
    pk.add_argument("--min-profit", type=int, help="Target net profit per car (overrides rules file)")
    pk.add_argument("--budget", type=int, help="Maximum buy price per car")
    pk.add_argument("--no-vat", action="store_true", help="Not VAT-registered: skip margin-scheme VAT")
    pk.add_argument("--rules", help="Path to a rules JSON (default config/buyer_rules.json)")
    pk.add_argument("--out", default="data/picks", help="Folder for the CSV and HTML reports")

    args = parser.parse_args()

    if args.cmd == "scrape":
        asyncio.run(cmd_scrape(args.source, args.pages))
    elif args.cmd == "demand":
        asyncio.run(cmd_demand())
    elif args.cmd == "gems":
        asyncio.run(cmd_gems())
    elif args.cmd == "plate":
        asyncio.run(cmd_plate(args.reg))
    elif args.cmd == "pick":
        cmd_pick(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
