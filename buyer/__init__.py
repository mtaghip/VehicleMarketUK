"""Rules-based car buyer: load a stock/valuation export, score every car and hand-pick a buy list."""
from .loader import load_stock_file, normalise
from .picker import Rules, pick

__all__ = ["load_stock_file", "normalise", "Rules", "pick"]
