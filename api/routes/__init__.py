from .listings import router as listings_router
from .analytics import router as analytics_router
from .plate import router as plate_router
from .alerts import router as alerts_router
from .scraper import router as scraper_router

__all__ = ["listings_router", "analytics_router", "plate_router", "alerts_router", "scraper_router"]
