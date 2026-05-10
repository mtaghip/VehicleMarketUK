"""FastAPI application entry point."""
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from config import settings
from database import init_db
from scheduler import setup_scheduler
from api.routes import (
    listings_router, analytics_router,
    plate_router, alerts_router, scraper_router,
    valuation_router, dealers_router, live_router,
)

security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    """Reject any request that doesn't supply the correct username + password."""
    if not settings.dashboard_user or not settings.dashboard_password:
        return  # Auth not configured — allow through (local dev)
    ok_user = secrets.compare_digest(credentials.username, settings.dashboard_user)
    ok_pass = secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initialising database...")
    await init_db()
    logger.info("Starting scheduler...")
    sched = setup_scheduler()
    sched.start()
    yield
    logger.info("Shutting down scheduler...")
    sched.shutdown(wait=False)


app = FastAPI(
    title="Vehicle Market UK",
    description="Car market intelligence — AutoTrader & Car&Classic scraping, demand analytics, plate lookup.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(listings_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(analytics_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(plate_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(alerts_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(scraper_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(valuation_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(dealers_router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(live_router, prefix="/api", dependencies=[Depends(require_auth)])

# Serve dashboard
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
if (DASHBOARD_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR / "static")), name="static")


@app.get("/", include_in_schema=False, dependencies=[Depends(require_auth)])
async def serve_dashboard():
    return FileResponse(str(DASHBOARD_DIR / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}
