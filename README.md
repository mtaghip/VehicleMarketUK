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

## Tests

The focused regression tests use an in-memory SQLite database and mocked browser pages; no website scraping, browser download, credentials, or live database is required.

```sh
python -m unittest discover -s tests -v
```

Remaining work includes source-specific individual-listing verification, explicit confirmed-sale evidence, persisted page-coverage reports and job coordination, historical-data review, and comparable-vehicle matching by trim and mileage. No live-source or deployment validation is implied by the regression tests.
