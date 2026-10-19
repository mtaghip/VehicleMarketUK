# Vehicle Market UK

UK vehicle-listing discovery and market summaries using AutoTrader and Car & Classic, a FastAPI API, an SQLite database, and a browser dashboard.

## Summary reliability

Search pages cover a sample of inventory. Hourly and dealer discovery scans **never mark missing listings sold**. The old disappearance-based records are retained for compatibility and labelled as inferred, unverified sales. They should not be interpreted as confirmed transactions or realised selling prices.

Listings expose `observation_status`: `recently_seen` (within 24 hours), `unverified` (not recently seen or otherwise unknown), or `inferred_sold` (a historical inactive record with a sale timestamp). This is derived evidence, not a new database status column. The dashboard shows how many tracked active listings have not been seen in 24 hours. Weekly bulk discoveries may become unverified before the next weekly scan; that does not mean they were removed.

Reappearing listings are reactivated and their obsolete sale timestamp and duration are cleared. Empty, HTTP-error and unparseable market pages fail the run. A greater-than-50% count drop from a previous successful run of at least 20 observations also fails the normal ingestion run. Successful means the discovery scan ran successfully, not that all inventory was covered. Valid zero-result searches are conservatively treated as unverified until source-specific empty-state detection is implemented.

Normal ingestion saves its observations atomically; failed runs roll back observations and persist a failure log. Failed logs report observations attempted in `listings_found`, but zero saved new/updated listings. Dealer scans retain their previous stock metadata after failure, an unparseable page, a page-cap overflow, or a large unexpected count drop. Bulk discovery still uses its existing separate ingestion path; per-page exceptions now propagate, but bulk progress/health logging is not unified with normal runs.

## Metrics

- “New” means **first discovered**, regardless of current active/sold status. Bulk imports are discoveries, not proof of newly advertised supply.
- Price snapshots store the actual median separately from the mean.
- The 30-day asking-price change compares the same currently active listings with their latest recorded price at or before the cutoff. Each vehicle contributes once. Vehicles with no baseline are excluded from the change calculation, but remain in the current price distribution. This is a surviving-listing cohort, not a whole-market index.
- Supply spikes no longer increase the demand score. Demand and velocity still depend on historical inferred sales; zero days is preserved instead of being treated as missing.

## Run locally

Use Python 3.12 and install the existing application requirements:

```sh
python -m venv .venv
# Activate the environment for your shell.
python -m pip install -r requirements.txt
python -m playwright install chromium
# Copy .env.example to .env and configure it.
python main.py
```

SQLite data lives under `data/` locally, or `/data` when that directory exists. This reliability change does not require a schema migration and does not rewrite historical inferred sales. Back up the database before deploying application changes.

## Car buyer (hand-pick cars from an export)

Give it a stock, auction or Auto Trader valuation export (CSV or Excel, extension optional) and it scores every car and shortlists the ones to buy:

```sh
python cli.py pick path/to/export.csv --top 10            # defaults from config/buyer_rules.json
python cli.py pick stock.xlsx --budget 12000 --no-vat     # per-car budget, not VAT-registered
```

For each car it works out speed tier A–D from average days to sell, retail rating and demand bar %; net profit at reserve after prep, fees and margin-scheme VAT; the **max bid** that still hits the target profit; and a spec score. Cars are skipped for finance, declared mechanical issues, too many owners, poor grade, high mileage, reserve at or above retail, identity mismatches, and red flags in the seller's notes such as write-off markers or leased batteries. Results go to `data/picks/` as a CSV of every car with reasons, a shortlist CSV and an HTML report.

Change costs, VAT status, target profit and thresholds in `config/buyer_rules.json`. In Claude Code, the `car-buyer` skill (`.claude/skills/car-buyer/SKILL.md`) runs the picker on an uploaded file and then reviews each shortlisted car's seller notes by hand before reporting.

Files without a reserve price (for example the Auto Trader demand CSV) get a max bid only; BUY then means worth buying at or below that bid. Values are snapshots and the picker never replaces an inspection, HPI check or test drive.

## Tests

The focused regression tests use an in-memory SQLite database and mocked browser pages; no website scraping, browser download, credentials, or live database is required.

```sh
python -m unittest discover -s tests -v
```

Remaining work includes source-specific individual-listing verification, explicit confirmed-sale evidence, persisted page-coverage reports and job coordination, historical-data review, and comparable-vehicle matching by trim and mileage. No live-source or deployment validation is implied by the regression tests.
