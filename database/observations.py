"""Describe evidence without treating unobserved inventory as a confirmed sale."""
from datetime import datetime, timedelta


def observation_status(listing, now=None):
    now = now or datetime.utcnow()
    if not listing.is_active:
        return "inferred_sold" if listing.sold_at else "unverified"
    if not listing.last_seen or listing.last_seen < now - timedelta(hours=24):
        return "unverified"
    return "recently_seen"
