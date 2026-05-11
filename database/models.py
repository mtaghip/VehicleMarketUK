from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Boolean, Text, Index,
    ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


class Source(str, enum.Enum):
    autotrader = "autotrader"
    carandclassic = "carandclassic"


class Listing(Base):
    """A car listing — one row per unique listing, updated on each scrape."""
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(String(64), nullable=False)          # Source's own ID
    source = Column(SAEnum(Source), nullable=False)
    url = Column(String(512), nullable=False)

    # Vehicle attributes
    make = Column(String(64), index=True)
    model = Column(String(128), index=True)
    variant = Column(String(256))                             # Trim/spec
    year = Column(Integer, index=True)
    colour = Column(String(64), index=True)
    mileage = Column(Integer)
    fuel_type = Column(String(32))
    transmission = Column(String(32))
    body_type = Column(String(32))
    engine_size = Column(Float)                               # Litres
    doors = Column(Integer)
    reg_plate = Column(String(16), index=True)

    # Pricing
    price = Column(Integer)                                   # GBP pence-free, whole £
    original_price = Column(Integer)                          # First-seen price

    # Location
    location = Column(String(128))
    postcode = Column(String(8))
    latitude = Column(Float)
    longitude = Column(Float)

    # Lifecycle
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    sold_at = Column(DateTime, nullable=True)
    days_to_sell = Column(Integer, nullable=True)             # Populated when sold
    is_active = Column(Boolean, default=True, index=True)

    # Metadata
    seller_type = Column(String(16))                          # private / dealer
    images_count = Column(Integer)
    description = Column(Text)

    # Dealer-intelligence fields (DealerAuction-style)
    owners_count = Column(Integer)                            # Number of previous owners
    service_history = Column(String(32))                      # Full / Partial / None
    ulez_compliant = Column(Boolean)
    euro_standard = Column(String(8))                         # Euro 6, Euro 5, etc.
    cat_marker = Column(String(4))                            # S / N / C / D (write-off cat)
    vat_qualifying = Column(Boolean)
    at_retail_rating = Column(Integer)                        # AutoTrader score 0-100

    price_history = relationship("PriceHistory", back_populates="listing", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_listings_source_id", "source", "listing_id", unique=True),
        Index("ix_listings_make_model_year", "make", "model", "year"),
        Index("ix_listings_active_sold", "is_active", "sold_at"),
    )

    @property
    def age_days(self) -> Optional[int]:
        if self.first_seen:
            return (datetime.utcnow() - self.first_seen).days
        return None


class PriceHistory(Base):
    """Price changes for a listing over time."""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False, index=True)
    price = Column(Integer, nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    listing = relationship("Listing", back_populates="price_history")


class DemandSnapshot(Base):
    """Hourly/daily aggregated demand metrics per make/model/year."""
    __tablename__ = "demand_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    make = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    year_band = Column(String(16))                            # e.g. "2018-2020"

    active_count = Column(Integer, default=0)                 # Listings live right now
    new_today = Column(Integer, default=0)                    # Listed in last 24h
    sold_this_week = Column(Integer, default=0)               # Sold in last 7 days
    avg_days_to_sell = Column(Float)
    median_price = Column(Integer)
    avg_price = Column(Integer)
    price_trend_pct = Column(Float)                          # % change vs last snapshot

    __table_args__ = (
        Index("ix_demand_make_model_snap", "make", "model", "snapshot_at"),
    )


class PriceAlert(Base):
    """User-defined price alerts."""
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    make = Column(String(64))
    model = Column(String(128))
    year_min = Column(Integer)
    year_max = Column(Integer)
    max_price = Column(Integer)
    max_mileage = Column(Integer)
    colour = Column(String(64))
    fuel_type = Column(String(32))
    email = Column(String(256))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_triggered = Column(DateTime, nullable=True)


class ScraperRun(Base):
    """Log of each scraper execution."""
    __tablename__ = "scraper_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(SAEnum(Source), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    listings_found = Column(Integer, default=0)
    listings_new = Column(Integer, default=0)
    listings_updated = Column(Integer, default=0)
    listings_sold = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    success = Column(Boolean, default=False)


class MonitoredDealer(Base):
    """An AutoTrader dealer whose stock we track to detect sold vehicles."""
    __tablename__ = "monitored_dealers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    autotrader_dealer_id = Column(String(64), index=True)   # Numeric ID from AT URL
    autotrader_url = Column(String(512))                    # Full dealer profile URL
    location = Column(String(128))
    postcode = Column(String(8))
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    last_scraped = Column(DateTime, nullable=True)
    total_stock = Column(Integer, default=0)

    stock = relationship("DealerListing", back_populates="dealer", cascade="all, delete-orphan")


class DealerListing(Base):
    """
    A vehicle in a specific dealer's inventory.
    When it disappears it is marked sold — giving per-dealer sold intel.
    """
    __tablename__ = "dealer_listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dealer_id = Column(Integer, ForeignKey("monitored_dealers.id"), nullable=False, index=True)
    listing_id = Column(String(64), nullable=False)         # AutoTrader listing ID
    url = Column(String(512))
    title = Column(String(512))
    reg_plate = Column(String(16))
    price = Column(Integer)
    mileage = Column(Integer)
    year = Column(Integer)
    make = Column(String(64))
    model = Column(String(128))
    colour = Column(String(64))
    fuel_type = Column(String(32))

    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    sold_at = Column(DateTime, nullable=True)
    days_to_sell = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, index=True)

    dealer = relationship("MonitoredDealer", back_populates="stock")

    __table_args__ = (
        Index("ix_dealer_listing_dealer_id", "dealer_id", "listing_id", unique=True),
    )


class BulkScrapeProgress(Base):
    """Checkpoint record for each completed search task (source, make, model, year_band, price_band)."""
    __tablename__ = "bulk_scrape_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(32), nullable=False)
    make = Column(String(64), nullable=False)
    model = Column(String(128), nullable=True)
    year_from = Column(Integer, nullable=True)
    year_to = Column(Integer, nullable=True)
    price_from = Column(Integer, nullable=True)
    price_to = Column(Integer, nullable=True)
    completed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    listings_saved = Column(Integer, default=0)

    __table_args__ = (
        Index(
            "ix_bulk_progress_task",
            "source", "make", "model", "year_from", "year_to", "price_from", "price_to",
            unique=True,
        ),
    )


class ValuationCache(Base):
    """Cached AutoTrader retail valuations (reg + mileage → valuation data)."""
    __tablename__ = "valuation_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reg = Column(String(16), nullable=False, index=True)
    mileage = Column(Integer, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Vehicle info from AutoTrader
    make = Column(String(64))
    model = Column(String(128))
    year = Column(Integer)
    colour = Column(String(64))
    fuel_type = Column(String(32))
    transmission = Column(String(32))

    # Valuations
    retail_price = Column(Integer)
    trade_price = Column(Integer)
    retail_rating = Column(Integer)         # AutoTrader score out of 100
    avg_days_to_sell = Column(Integer)
    market_condition = Column(String(64))   # e.g. "Lower demand than normal"
    price_change_pct = Column(Float)        # 30-day trend %

    __table_args__ = (
        Index("ix_valuation_reg_mileage", "reg", "mileage"),
    )
