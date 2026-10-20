"""Score every car, apply the buying rules and return a ranked, explained buy list.

Each car gets:
  * speed tier A-D from average days to sell, Auto Trader retail rating and demand bar %,
  * economics: expected resale, costs, margin-scheme VAT, net profit at reserve and the
    highest bid that still hits the target profit,
  * a spec score from equipment, extras and trim,
  * hard rejections and warnings, including red flags found in the seller's notes,
  * a verdict (BUY / CONSIDER / SKIP) with plain-English reasons.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "buyer_rules.json"


@dataclass
class Rules:
    costs: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)
    speed: dict = field(default_factory=dict)
    hard_rules: dict = field(default_factory=dict)
    ranking_weights: dict = field(default_factory=dict)
    max_per_fuel_in_top: float = 0.5

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides) -> "Rules":
        data = json.loads(Path(path or DEFAULT_RULES_PATH).read_text())
        data.pop("_comment", None)
        rules = cls(**data)
        for key, value in overrides.items():
            if value is None:
                continue
            for section in (rules.targets, rules.costs, rules.hard_rules):
                if key in section:
                    section[key] = value
                    break
            else:
                raise KeyError(f"Unknown rule override: {key}")
        return rules


# --- seller-note red flags --------------------------------------------------
# (pattern, message, hard reject?)
NOTE_FLAGS = [
    (r"\bcat(egory)?\s*[sncd]\b|salvage|write[- ]?off|written off", "Insurance write-off / category marker in notes", True),
    (r"battery (is )?lease|leased battery|battery hire", "Battery appears to be leased", True),
    (r"warning light|engine light|eml\b|limp mode", "Warning light mentioned", False),
    (r"gearbox|clutch slip|judder", "Gearbox/clutch mentioned", False),
    (r"smoke|knock|rattle|noise", "Noise/smoke mentioned", False),
    (r"key remote not working|one key|single key|no spare key", "Key issue", False),
    (r"rips, tears or burns|(?<!un)damaged steering wheel", "Interior damage", False),
    (r"#name\?", "Seller notes contain a spreadsheet error; check the original listing", False),
]
STOCKING_LOAN = re.compile(r"stocking loan which will be cleared", re.I)

# Older Renault EVs were commonly sold with leased batteries.
LEASE_RISK_MODELS = re.compile(r"\b(zoe|kangoo z\.?e|fluence)\b", re.I)

# --- spec scoring -------------------------------------------------------------
EQUIPMENT_POINTS = {"sat nav": 1, "heated seats": 1, "parking cam": 1, "panoramic roof": 2, "sound system": 1.5}
EXTRA_POINTS = {"keyless": 1, "blind spot": 1, "sunroof": 1, "adaptive cruise": 1, "360": 1, "heads up": 1,
                "head-up": 1, "heated steering": 0.5, "led headlights": 0.5, "lane assist": 0.5, "massage": 1}
TOP_TRIM = re.compile(r"premium ?\+|premium plus|pre?m ?\+|vorsprung|autobio|first ed|launch ed|black ed|blk ed|"
                      r"ultimate|signature|inscription|vignale|tekna ?\+|excel|exclusive|platinum|takumi|luxury", re.I)
GOOD_TRIM = re.compile(r"amg line|m sport|s line|r-?line|hse|tekna|gt line|titanium x|st-line x|xline|premium|"
                       r"n line|fr sport|r-?dynamic|sport x", re.I)


def _s(value) -> str:
    """Text value, with missing values as an empty string."""
    return "" if pd.isna(value) else str(value).strip()


def _text(row: pd.Series, *cols: str) -> str:
    return " ".join(str(row[c]) for c in cols if pd.notna(row[c])).lower()


def spec_score(row: pd.Series) -> float:
    equipment, extras = _text(row, "equipment"), _text(row, "extras")
    score = sum(p for k, p in EQUIPMENT_POINTS.items() if k in equipment)
    score += sum(p for k, p in EXTRA_POINTS.items() if k in extras)
    score += 0.5 if ("carplay" in extras or "android" in extras) else 0
    trim = _text(row, "model", "portal_vehicle")
    score += 2 if TOP_TRIM.search(trim) else (1 if GOOD_TRIM.search(trim) else 0)
    return score


def speed_tier(days, rating, bar, speed: dict) -> str:
    if pd.isna(days) or pd.isna(rating):
        return "?"
    bar = 0 if pd.isna(bar) else bar
    for tier in ("A", "B", "C"):
        t = speed[f"tier_{tier.lower()}"]
        if days <= t["max_days"] and rating >= t["min_rating"] and bar >= t["min_demand_bar"]:
            return tier
    return "D"


def quick_sell_score(days, rating, bar) -> float:
    """0-100. Days weighted 40%, rating 35%, demand bar 25%."""
    if pd.isna(days) or pd.isna(rating):
        return np.nan
    s_days = (1 - (min(max(days, 20), 60) - 20) / 40) * 100
    s_bar = ((min(max(0 if pd.isna(bar) else bar, 30), 90) - 30) / 60) * 100
    return round(0.40 * s_days + 0.35 * rating + 0.25 * s_bar, 1)


def _economics(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    c, t = rules.costs, rules.targets
    vat_factor = 1 - (1 / 6 if c["vat_registered"] else 0)
    prep = df["grade"].map(lambda g: c["prep_by_grade"].get(str(int(g)), c["prep_by_grade"]["5"]) if pd.notna(g)
                           else c["prep_by_grade"]["3"])
    # Only charge for a service when the seller says there is no history; a blank field just means unknown.
    no_history = df["service_history"].str.strip().str.lower().eq("none").fillna(False).astype(bool)
    df["resale"] = (df["retail"] * c["resale_pct_of_retail"]).round(-1)
    df["costs"] = c["fixed_costs"] + prep + np.where(no_history, c["no_history_service"], 0)
    margin = (df["resale"] - df["reserve"]).clip(lower=0)
    df["vat"] = (margin / 6).round() if c["vat_registered"] else 0
    df["net_at_reserve"] = (df["resale"] - df["reserve"] - df["vat"] - df["costs"]).round()
    # Highest purchase price that still leaves the target profit after costs and VAT.
    df["max_bid"] = (df["resale"] - (t["target_profit"] + df["costs"]) / vat_factor).round(-1)
    df["bid_headroom"] = df["max_bid"] - df["reserve"]
    df["capital"] = df["reserve"].fillna(df["max_bid"]) + df["costs"]
    profit = df["net_at_reserve"].fillna(t["target_profit"])
    df["roi_pct"] = (profit / df["capital"] * 100).round(1)
    return df


def _assess(row: pd.Series, rules: Rules) -> tuple[list[str], list[str]]:
    hard, warn = [], []
    h, t = rules.hard_rules, rules.targets
    status, error = _s(row["status"]).lower(), _s(row["error"])
    if "mismatch" in status or "conflict" in error.lower():
        hard.append("Portal vehicle doesn't match the stock details")
    elif pd.isna(row["retail"]):
        hard.append("No retail valuation" + (f" ({error})" if error else ""))
    elif pd.isna(row["avg_days"]) or pd.isna(row["rating"]):
        warn.append("No days-to-sell / rating from the portal")

    notes = _text(row, "condition_notes", "mech_notes", "extras")
    if row["on_finance"]:
        if STOCKING_LOAN.search(notes):
            warn.append("Finance is a dealer stocking loan cleared on collection")
        elif h["reject_on_finance"]:
            hard.append("On finance")
        else:
            warn.append("On finance - confirm settlement")
    if row["mech_issues"]:
        (hard if h["reject_mechanical_issues"] else warn).append("Seller declared mechanical issues")
    if pd.notna(row["owners"]) and row["owners"] > h["max_owners"]:
        hard.append(f"{int(row['owners'])} owners")
    if pd.notna(row["grade"]) and row["grade"] > h["max_grade"]:
        hard.append(f"Grade {int(row['grade'])}")
    if pd.notna(row["mileage"]) and row["mileage"] > h["max_mileage"]:
        hard.append(f"{int(row['mileage']):,} miles")

    for pattern, message, is_hard in NOTE_FLAGS:
        if re.search(pattern, notes):
            (hard if is_hard else warn).append(message)
    model_text = _text(row, "model", "portal_vehicle")
    if LEASE_RISK_MODELS.search(model_text) and pd.notna(row["year"]) and row["year"] <= 2020:
        warn.append("Older Renault EV - confirm the battery is owned, not leased")
    if _s(row["fuel"]).lower() == "electric":
        warn.append("EV - get a battery health report")
    if _s(row["service_history"]).lower() == "none":
        warn.append("No service history")

    if pd.notna(row["reserve"]) and pd.notna(row["retail"]) and row["reserve"] >= row["retail"]:
        hard.append("Reserve at or above retail")
    if pd.notna(row["max_bid"]) and pd.notna(row["retail"]) and pd.isna(row["reserve"]):
        if row["max_bid"] < row["retail"] * t["min_realistic_bid_pct_of_retail"]:
            hard.append(f"Would need to buy below {t['min_realistic_bid_pct_of_retail']:.0%} of retail")
    budget = t.get("budget_max_buy")
    if budget:
        buy = row["reserve"] if pd.notna(row["reserve"]) else row["max_bid"]
        if pd.notna(buy) and buy > budget:
            hard.append(f"Over budget (£{buy:,.0f})")
    return hard, warn


def _verdict(row: pd.Series, rules: Rules) -> str:
    if row["hard_fails"]:
        return "SKIP"
    t = rules.targets
    profit = row["net_at_reserve"] if pd.notna(row["reserve"]) else t["target_profit"]
    fast_enough = row["tier"] in rules.speed["buy_requires_tier"]
    if fast_enough and profit >= t["target_profit"]:
        return "BUY"
    if profit >= t["consider_profit"] or (fast_enough and profit > 0):
        return "CONSIDER"
    return "SKIP"


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True).fillna(0)


def pick(cars: pd.DataFrame, rules: Rules | None = None, top: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (all cars scored and ranked, hand-picked shortlist)."""
    rules = rules or Rules.load()
    df = _economics(cars.copy(), rules)
    df["tier"] = [speed_tier(d, r, b, rules.speed) for d, r, b in zip(df["avg_days"], df["rating"], df["demand_bar"])]
    df["quick_sell"] = [quick_sell_score(d, r, b) for d, r, b in zip(df["avg_days"], df["rating"], df["demand_bar"])]
    df["spec"] = df.apply(spec_score, axis=1)
    assessed = df.apply(lambda r: _assess(r, rules), axis=1)
    df["hard_fails"] = [a[0] for a in assessed]
    df["warnings"] = [a[1] for a in assessed]
    df["verdict"] = df.apply(lambda r: _verdict(r, rules), axis=1)

    w = rules.ranking_weights
    # Rank on £ profit when the file has buy prices, otherwise on return at the max bid.
    profit_basis = df["roi_pct"] if df["reserve"].isna().all() else df["net_at_reserve"]
    condition = (6 - df["grade"].fillna(3)) - df["warnings"].str.len() * 0.5
    df["score"] = (w["speed"] * _pct_rank(df["quick_sell"]) + w["profit"] * _pct_rank(profit_basis)
                   + w["spec"] * _pct_rank(df["spec"]) + w["condition"] * _pct_rank(condition)) * 100
    order = {"BUY": 0, "CONSIDER": 1, "SKIP": 2}
    df = df.sort_values(["verdict", "score"], key=lambda s: s.map(order) if s.name == "verdict" else -s)
    df = df.reset_index(drop=True)

    shortlist = _diversified(df[df["verdict"] != "SKIP"], top, rules.max_per_fuel_in_top)
    return df, shortlist


def _diversified(candidates: pd.DataFrame, top: int, max_share: float) -> pd.DataFrame:
    """Stop one fuel type (usually EVs) taking over the shortlist."""
    cap = max(1, int(np.ceil(top * max_share)))
    picked, per_fuel = [], {}
    for idx, row in candidates.iterrows():
        fuel = _s(row["fuel"])
        if fuel and per_fuel.get(fuel, 0) >= cap:
            continue
        picked.append(idx)
        per_fuel[fuel] = per_fuel.get(fuel, 0) + 1
        if len(picked) == top:
            break
    return candidates.loc[picked].reset_index(drop=True)
