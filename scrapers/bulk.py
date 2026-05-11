"""
Full-market bulk scraper.

Strategy:
  AutoTrader  — make × model × year_band  (~800 search tasks, ~16 hrs)
  Car&Classic — make × model              (~500 search tasks,  ~8 hrs)

Each completed task is checkpointed in BulkScrapeProgress so a run can
be interrupted and resumed without re-scraping finished combinations.
"""
import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional

from sqlalchemy import select, and_

from database import AsyncSessionLocal, BulkScrapeProgress
from .autotrader import AutoTraderScraper
from .carandclassic import CarAndClassicScraper
from .base import RawListing
from .ingestion import upsert_listing

logger = logging.getLogger(__name__)

# ── MAKE / MODEL CATALOGUE ────────────────────────────────────────────────────

MAKES_MODELS: dict[str, list[str]] = {
    "Abarth": ["500", "595", "695", "124 Spider", "Grande Punto"],
    "Alfa Romeo": ["147", "156", "159", "Brera", "Giulia", "Giulietta", "GT",
                   "Mito", "Spider", "Stelvio", "Tonale"],
    "Aston Martin": ["DB9", "DB11", "DBS", "DBX", "Rapide", "V8 Vantage",
                     "Vantage", "Vanquish"],
    "Audi": ["A1", "A3", "A4", "A5", "A6", "A7", "A8", "e-tron", "Q2", "Q3",
             "Q4 e-tron", "Q5", "Q7", "Q8", "R8", "RS3", "RS4", "RS5", "RS6",
             "RS7", "S3", "S4", "S5", "SQ5", "SQ7", "TT"],
    "Bentley": ["Bentayga", "Continental GT", "Flying Spur", "Mulsanne"],
    "BMW": ["1 Series", "2 Series", "3 Series", "4 Series", "5 Series",
            "6 Series", "7 Series", "8 Series", "i3", "i4", "i7", "iX",
            "M2", "M3", "M4", "M5", "X1", "X2", "X3", "X4", "X5", "X6",
            "X7", "Z3", "Z4"],
    "Citroen": ["Berlingo", "C1", "C2", "C3", "C3 Aircross", "C4",
                "C4 Cactus", "C5", "C5 Aircross", "C5 X", "Dispatch",
                "Picasso", "SpaceTourer", "Xsara"],
    "Cupra": ["Ateca", "Born", "Formentor", "Leon"],
    "Dacia": ["Dokker", "Duster", "Jogger", "Logan", "Sandero", "Spring"],
    "DS": ["DS3", "DS4", "DS5", "DS7", "DS9"],
    "Ferrari": ["296", "458", "488", "812", "California", "F8",
                "GTC4Lusso", "Portofino", "Roma", "SF90"],
    "Fiat": ["124 Spider", "500", "500C", "500L", "500X", "Bravo", "Doblo",
             "Ducato", "Panda", "Punto", "Tipo"],
    "Ford": ["B-MAX", "C-MAX", "EcoSport", "Edge", "Fiesta", "Focus",
             "Galaxy", "Ka", "Ka+", "Kuga", "Mondeo", "Mustang", "Puma",
             "Ranger", "S-MAX", "Tourneo", "Transit", "Transit Connect"],
    "Honda": ["Accord", "Civic", "CR-V", "CR-Z", "FR-V", "HR-V", "Jazz",
              "Legend", "NSX", "S2000", "ZR-V", "e"],
    "Hyundai": ["Bayon", "Coupe", "Getz", "i10", "i20", "i30", "i40",
                "IONIQ", "IONIQ 5", "IONIQ 6", "Kona", "Santa Fe", "Tucson",
                "Veloster"],
    "Infiniti": ["Q30", "Q50", "Q60", "QX30", "QX50", "QX70"],
    "Jaguar": ["E-Pace", "F-Pace", "F-Type", "I-Pace", "S-Type", "X-Type",
               "XE", "XF", "XJ"],
    "Jeep": ["Avenger", "Cherokee", "Compass", "Grand Cherokee", "Renegade",
             "Wrangler"],
    "Kia": ["Carens", "Ceed", "EV6", "Niro", "Picanto", "ProCeed", "Rio",
            "Sorento", "Soul", "Sportage", "Stinger", "Stonic", "Xceed"],
    "Lamborghini": ["Aventador", "Huracan", "Urus"],
    "Land Rover": ["Defender", "Discovery", "Discovery Sport", "Freelander",
                   "Range Rover", "Range Rover Evoque", "Range Rover Sport",
                   "Range Rover Velar"],
    "Lexus": ["CT", "ES", "GS", "IS", "LC", "LS", "LX", "NX", "RC", "RX", "UX"],
    "Lotus": ["Elise", "Emira", "Evora", "Exige"],
    "Maserati": ["Ghibli", "GranCabrio", "GranTurismo", "Levante", "MC20",
                 "Quattroporte"],
    "Mazda": ["2", "3", "6", "CX-3", "CX-30", "CX-5", "CX-60",
              "MX-30", "MX-5", "RX-8"],
    "McLaren": ["540C", "570S", "600LT", "650S", "675LT", "720S", "765LT",
                "Artura", "GT"],
    "Mercedes-Benz": ["A Class", "AMG GT", "B Class", "C Class", "CLA",
                      "CLS", "E Class", "EQA", "EQB", "EQC", "EQE", "EQS",
                      "G Class", "GLA", "GLB", "GLC", "GLE", "GLS", "S Class",
                      "SL", "SLC", "Sprinter", "V Class", "Vito"],
    "MG": ["3", "4", "5 EV", "GS", "HS", "Marvel R", "ZS"],
    "MINI": ["Cabrio", "Clubman", "Convertible", "Countryman", "Hatch",
             "Paceman", "Roadster"],
    "Mitsubishi": ["ASX", "Colt", "Eclipse Cross", "Galant", "L200",
                   "Outlander", "Shogun"],
    "Nissan": ["370Z", "Ariya", "GT-R", "Juke", "Leaf", "Micra", "Navara",
               "Note", "NV200", "Pulsar", "Qashqai", "X-Trail"],
    "Peugeot": ["107", "108", "2008", "207", "208", "3008", "307", "308",
                "408", "5008", "508", "Boxer", "e-208", "e-2008", "Expert",
                "Partner", "RCZ"],
    "Porsche": ["718 Boxster", "718 Cayman", "911", "Boxster", "Cayenne",
                "Cayman", "Macan", "Panamera", "Taycan"],
    "Renault": ["Arkana", "Austral", "Captur", "Clio", "Espace", "Kadjar",
                "Kangoo", "Koleos", "Laguna", "Master", "Megane", "Scenic",
                "Trafic", "Twingo", "Zoe"],
    "Rolls-Royce": ["Cullinan", "Dawn", "Ghost", "Phantom", "Spectre",
                    "Silver Shadow", "Wraith"],
    "SEAT": ["Alhambra", "Altea", "Arona", "Ateca", "Ibiza", "Leon", "Mii",
             "Tarraco", "Toledo"],
    "Skoda": ["Citigo", "Enyaq", "Fabia", "Kamiq", "Karoq", "Kodiaq",
              "Octavia", "Rapid", "Scala", "Superb", "Yeti"],
    "Smart": ["EQ Fortwo", "Forfour", "Fortwo"],
    "Subaru": ["BRZ", "Forester", "Impreza", "Legacy", "Levorg",
               "Outback", "WRX STI", "XV"],
    "Suzuki": ["Alto", "Baleno", "Celerio", "Ignis", "Jimny", "S-Cross",
               "Swift", "SX4", "Vitara"],
    "Tesla": ["Model 3", "Model S", "Model X", "Model Y"],
    "Toyota": ["Auris", "Avensis", "Aygo", "C-HR", "Camry", "Corolla",
               "GR86", "GT86", "Hilux", "IQ", "Land Cruiser", "Prius",
               "Proace", "RAV4", "Urban Cruiser", "Verso", "Yaris"],
    "Vauxhall": ["Adam", "Agila", "Antara", "Astra", "Cascada", "Combo",
                 "Corsa", "Crossland", "GTC", "Grandland", "Insignia",
                 "Meriva", "Mokka", "Signum", "Vectra", "Vivaro", "Zafira"],
    "Volkswagen": ["Amarok", "Arteon", "Caddy", "Golf", "ID.3", "ID.4",
                   "ID.5", "Passat", "Polo", "Scirocco", "Sharan", "T-Cross",
                   "T-Roc", "Tiguan", "Touareg", "Touran", "Transporter",
                   "Up"],
    "Volvo": ["C30", "C40", "C70", "EX30", "EX90", "S40", "S60", "S80",
              "S90", "V40", "V50", "V60", "V70", "V90", "XC40", "XC60",
              "XC70", "XC90"],
}

# Models popular enough that a single search hits the 100-page cap.
# These get split into year bands only.
HIGH_VOLUME: set[tuple[str, str]] = {
    ("Audi", "A4"), ("Audi", "Q3"), ("Audi", "Q5"),
    ("BMW", "1 Series"), ("BMW", "X3"), ("BMW", "X5"),
    ("Ford", "Mondeo"), ("Ford", "Puma"),
    ("Hyundai", "i20"), ("Hyundai", "i30"), ("Hyundai", "Tucson"),
    ("Kia", "Ceed"), ("Kia", "Niro"),
    ("Mercedes-Benz", "E Class"), ("Mercedes-Benz", "GLC"),
    ("Nissan", "Juke"),
    ("Peugeot", "2008"), ("Peugeot", "308"),
    ("Renault", "Captur"), ("Renault", "Megane"),
    ("SEAT", "Leon"),
    ("Skoda", "Fabia"), ("Skoda", "Kodiaq"),
    ("Toyota", "C-HR"), ("Toyota", "Corolla"), ("Toyota", "RAV4"),
    ("Vauxhall", "Mokka"),
    ("Volkswagen", "Passat"), ("Volkswagen", "T-Roc"),
}

# Top 20 highest-volume UK models — split by BOTH year band AND price band
# to stay under AutoTrader's 1,800-result search cap.
ULTRA_HIGH_VOLUME: set[tuple[str, str]] = {
    ("Audi", "A3"),
    ("BMW", "3 Series"), ("BMW", "5 Series"),
    ("Ford", "Fiesta"), ("Ford", "Focus"), ("Ford", "Kuga"),
    ("Kia", "Sportage"),
    ("Mercedes-Benz", "A Class"), ("Mercedes-Benz", "C Class"),
    ("Nissan", "Qashqai"),
    ("Peugeot", "208"),
    ("Renault", "Clio"),
    ("SEAT", "Ibiza"),
    ("Skoda", "Octavia"),
    ("Toyota", "Yaris"),
    ("Vauxhall", "Astra"), ("Vauxhall", "Corsa"),
    ("Volkswagen", "Golf"), ("Volkswagen", "Polo"), ("Volkswagen", "Tiguan"),
}

# Year bands for HIGH_VOLUME and ULTRA_HIGH_VOLUME
YEAR_BANDS: list[tuple[Optional[int], Optional[int]]] = [
    (None, 2009),
    (2010, 2014),
    (2015, 2018),
    (2019, 2022),
    (2023, None),
]

# Price bands applied on top of year bands for ULTRA_HIGH_VOLUME models
PRICE_BANDS: list[tuple[Optional[int], Optional[int]]] = [
    (None, 4999),
    (5000, 9999),
    (10000, 14999),
    (15000, 24999),
    (25000, None),
]

# AutoTrader hard-caps at 100 pages (~1,800 results) per search
AT_MAX_PAGES = 100
CC_MAX_PAGES = 100

# Task tuple: (make, model, year_from, year_to, price_from, price_to)
Task = tuple[str, str, Optional[int], Optional[int], Optional[int], Optional[int]]


# ── TASK BUILDER ──────────────────────────────────────────────────────────────

def _build_tasks(source: str) -> list[Task]:
    """
    Build all search tasks for a source.

    AutoTrader strategy:
      ULTRA_HIGH_VOLUME → year_band × price_band  (25 tasks each, ~500 total)
      HIGH_VOLUME       → year_band only           (5 tasks each)
      everything else   → single task
    Car&Classic: make+model only (no splits needed — smaller site).
    """
    tasks: list[Task] = []
    for make, models in MAKES_MODELS.items():
        for model in models:
            if source == "autotrader" and (make, model) in ULTRA_HIGH_VOLUME:
                for y_from, y_to in YEAR_BANDS:
                    for p_from, p_to in PRICE_BANDS:
                        tasks.append((make, model, y_from, y_to, p_from, p_to))
            elif source == "autotrader" and (make, model) in HIGH_VOLUME:
                for y_from, y_to in YEAR_BANDS:
                    tasks.append((make, model, y_from, y_to, None, None))
            else:
                tasks.append((make, model, None, None, None, None))
    return tasks


async def _completed_tasks(source: str) -> set[Task]:
    """Return the set of already-completed tasks from the DB."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(
                BulkScrapeProgress.make,
                BulkScrapeProgress.model,
                BulkScrapeProgress.year_from,
                BulkScrapeProgress.year_to,
                BulkScrapeProgress.price_from,
                BulkScrapeProgress.price_to,
            ).where(BulkScrapeProgress.source == source)
        )
        return {(r.make, r.model, r.year_from, r.year_to, r.price_from, r.price_to)
                for r in rows.all()}


async def _mark_done(source: str, make: str, model: str,
                     year_from: Optional[int], year_to: Optional[int],
                     price_from: Optional[int], price_to: Optional[int],
                     count: int) -> None:
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    async with AsyncSessionLocal() as session:
        stmt = sqlite_insert(BulkScrapeProgress).values(
            source=source, make=make, model=model,
            year_from=year_from, year_to=year_to,
            price_from=price_from, price_to=price_to,
            completed_at=datetime.utcnow(), listings_saved=count,
        ).on_conflict_do_update(
            index_elements=[
                "source", "make", "model",
                "year_from", "year_to", "price_from", "price_to",
            ],
            set_={"completed_at": datetime.utcnow(), "listings_saved": count},
        )
        await session.execute(stmt)
        await session.commit()


# ── INGESTOR ──────────────────────────────────────────────────────────────────

async def _ingest_stream(stream: AsyncGenerator[RawListing, None],
                         source: str) -> int:
    count = 0
    async with AsyncSessionLocal() as session:
        async for raw in stream:
            try:
                await upsert_listing(session, raw)
                count += 1
                if count % 100 == 0:
                    await session.commit()
                    logger.info(f"Bulk {source}: {count} listings saved so far…")
            except Exception as e:
                logger.debug(f"Bulk ingest error ({source}): {e}")
        await session.commit()
    return count


# ── MAIN RUNNERS ──────────────────────────────────────────────────────────────

async def run_bulk_autotrader(resume: bool = True) -> dict:
    """
    Scrape every make/model combo on AutoTrader.
    Ultra-high-volume models split by year band × price band (25 tasks each).
    High-volume models split by year band only (5 tasks each).
    Pass resume=False to re-scrape already-completed tasks.
    """
    all_tasks = _build_tasks("autotrader")
    done = await _completed_tasks("autotrader") if resume else set()
    pending = [t for t in all_tasks if t not in done]

    logger.info(
        f"AutoTrader full scrape: {len(all_tasks)} tasks total, "
        f"{len(done)} already done, {len(pending)} remaining"
    )

    total = 0
    errors = []
    scraper = AutoTraderScraper()

    for i, (make, model, y_from, y_to, p_from, p_to) in enumerate(pending, 1):
        year_desc = f"{y_from or '?'}–{y_to or '?'}" if (y_from or y_to) else "all years"
        price_desc = f"£{p_from or 0}–£{p_to or '∞'}" if (p_from or p_to) else "all prices"
        logger.info(f"AT [{i}/{len(pending)}] {make} {model} | {year_desc} | {price_desc}")
        try:
            count = await _ingest_stream(
                scraper.search(
                    make=make, model=model,
                    year_min=y_from, year_max=y_to,
                    price_min=p_from, price_max=p_to,
                    max_pages=AT_MAX_PAGES,
                ),
                "autotrader",
            )
            await _mark_done("autotrader", make, model, y_from, y_to, p_from, p_to, count)
            total += count
            logger.info(f"AT [{i}/{len(pending)}] {make} {model} → {count} (running total: {total})")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"AT error on {make} {model}: {e}")
            errors.append({"make": make, "model": model, "error": str(e)})

    return {"source": "autotrader", "total": total,
            "tasks_done": len(pending), "errors": errors}


async def run_bulk_carandclassic(resume: bool = True) -> dict:
    """Scrape every make/model combo on Car & Classic."""
    all_tasks = _build_tasks("carandclassic")
    done = await _completed_tasks("carandclassic") if resume else set()
    pending = [t for t in all_tasks if t not in done]

    logger.info(
        f"C&C full scrape: {len(all_tasks)} tasks total, "
        f"{len(done)} already done, {len(pending)} remaining"
    )

    total = 0
    errors = []
    scraper = CarAndClassicScraper()

    for i, (make, model, y_from, y_to, p_from, p_to) in enumerate(pending, 1):
        logger.info(f"C&C [{i}/{len(pending)}] {make} {model}")
        try:
            count = await _ingest_stream(
                scraper.search(make=make, model=model, max_pages=CC_MAX_PAGES),
                "carandclassic",
            )
            await _mark_done("carandclassic", make, model, None, None, None, None, count)
            total += count
            logger.info(f"C&C [{i}/{len(pending)}] {make} {model} → {count} (total: {total})")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"C&C error on {make} {model}: {e}")
            errors.append({"make": make, "model": model, "error": str(e)})

    return {"source": "carandclassic", "total": total,
            "tasks_done": len(pending), "errors": errors}


async def run_full_bulk_scrape(resume: bool = True) -> dict:
    """Run both scrapers sequentially. Used by the weekly scheduler job."""
    at_tasks = len(_build_tasks("autotrader"))
    cc_tasks = len(_build_tasks("carandclassic"))
    logger.info(
        f"=== Full bulk scrape starting: "
        f"{at_tasks} AT tasks + {cc_tasks} C&C tasks ==="
    )
    at = await run_bulk_autotrader(resume=resume)
    cc = await run_bulk_carandclassic(resume=resume)
    grand = at["total"] + cc["total"]
    logger.info(f"=== Full bulk scrape complete: {grand} total listings ===")
    return {"autotrader": at, "carandclassic": cc, "grand_total": grand}
