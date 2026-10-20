"""Read the different export formats we receive and map them to one set of columns.

Known formats:
  * Auto Trader demand CSV (VRM, retail, avgDays, retailRating, demandBarPct 0-100, error) - no buy price.
  * Stock-check workbook, "All cars" sheet (Reserve price, AT retail GBP, Demand bar position % as 0-1).
  * Motorway check CSV (reserve, retail, avgDays, retailRating, demandBarPct, status, Motorway vehicle link).
Unknown files work as long as they carry a registration plus retail/days/rating/demand-bar columns
under one of the aliases below.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# canonical name -> accepted source headers (first match wins, compared case-insensitively)
ALIASES: dict[str, list[str]] = {
    "vrm": ["VRM", "Registration", "Reg"],
    "make": ["Make"],
    "model": ["Model"],
    "year": ["Year"],
    "mileage": ["Mileage"],
    "owners": ["Number of owners", "Owners"],
    "service_history": ["Service history"],
    "grade": ["Exterior grade", "Grade"],
    "colour": ["Colour"],
    "body": ["Body type"],
    "fuel": ["Fuel"],
    "transmission": ["Transmission"],
    "location": ["Location"],
    "on_finance": ["On finance", "Finance declared"],
    "mech_issues": ["Mechanical issues disclosed by seller", "Mechanical issues"],
    "equipment": ["Equipment"],
    "extras": ["Additional specifications"],
    "condition_notes": ["Additional condition and damage information"],
    "mech_notes": ["Additional mechanical information"],
    "reserve": ["reserve", "Reserve price", "Reserve GBP", "Reserve"],
    "cap_clean": ["capClean", "CAP clean value"],
    "retail": ["retail", "AT retail GBP", "AT retail", "Retail value"],
    "avg_days": ["avgDays", "AT average days", "Days to sell"],
    "rating": ["retailRating", "AT retail rating", "AT rating"],
    "demand_label": ["demandLabel", "Market condition", "Demand"],
    "demand_bar": ["demandBarPct", "Demand bar position %", "Demand bar %"],
    "status": ["status", "Check status"],
    "error": ["error"],
    "portal_vehicle": ["portalVehicle", "Portal vehicle"],
    "link": ["Motorway vehicle link", "portalUrl", "Portal URL"],
}

# Typical demand bar % for each Auto Trader demand label (averages from the 8 Oct 2026 Motorway check),
# used when a file only gives the label.
LABEL_TO_BAR = {
    "higher demand than normal": 60.0,
    "lower supply than normal": 63.0,
    "balanced market": 50.0,
    "higher supply than normal": 42.0,
    "lower demand than normal": 40.0,
}

NUMERIC = ["year", "mileage", "owners", "grade", "reserve", "cap_clean", "retail", "avg_days", "rating", "demand_bar"]
MONEY_CHARS = r"[£,\s]"


def _read_raw(path: Path) -> pd.DataFrame:
    """Excel files sometimes arrive without an extension, so detect them by their zip header."""
    with open(path, "rb") as fh:
        is_xlsx = fh.read(2) == b"PK"
    if is_xlsx:
        return _read_excel(path)
    return pd.read_csv(path, keep_default_na=False, na_values=[""], dtype=str)


def _read_excel(path: Path) -> pd.DataFrame:
    """Use the 'All cars' sheet when present, otherwise the first sheet with a VRM header row."""
    with pd.ExcelFile(path, engine="openpyxl") as xl:
        sheets = (["All cars"] if "All cars" in xl.sheet_names else []) + list(xl.sheet_names)
        for sheet in sheets:
            raw = xl.parse(sheet, header=None, dtype=str, keep_default_na=False, na_values=[""])
            hits = raw.index[raw.apply(lambda r: r.astype(str).str.strip().eq("VRM").any(), axis=1)]
            if len(hits):
                df = raw.iloc[hits[0] + 1:].reset_index(drop=True)
                df.columns = [str(c).strip() for c in raw.iloc[hits[0]]]
                return df.dropna(how="all")
    raise ValueError(f"No sheet with a VRM header row found in {path.name}")


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Map any supported layout onto the canonical columns."""
    lookup = {str(c).strip().lower(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for canon, names in ALIASES.items():
        src = next((lookup[n.lower()] for n in names if n.lower() in lookup), None)
        out[canon] = df[src] if src is not None else pd.NA
    if out["vrm"].isna().all():
        raise ValueError("Could not find a VRM / registration column")

    for col in NUMERIC:
        out[col] = pd.to_numeric(out[col].astype("string").str.replace(MONEY_CHARS, "", regex=True)
                                 .str.rstrip("%"), errors="coerce")
    # Some exports give the demand bar as a 0-1 fraction.
    bar = out["demand_bar"].dropna()
    if len(bar) and bar.max() <= 1.0:
        out["demand_bar"] = out["demand_bar"] * 100
    estimate = out["demand_label"].astype("string").str.strip().str.lower().map(LABEL_TO_BAR)
    out["demand_bar_estimated"] = out["demand_bar"].isna() & estimate.notna()
    out["demand_bar"] = out["demand_bar"].fillna(estimate)

    for col in ["on_finance", "mech_issues"]:
        out[col] = out[col].astype("string").str.strip().str.lower().isin(["yes", "true", "y", "1"])
    out["vrm"] = out["vrm"].astype("string").str.strip().str.upper()
    out["service_history"] = out["service_history"].astype("string").str.strip()
    out = out[out["vrm"].notna() & (out["vrm"] != "")]
    # Keep the copy with a price when the same plate appears twice.
    out = (out.assign(_unpriced=out["retail"].isna()).sort_values("_unpriced", kind="stable")
              .drop_duplicates("vrm", keep="first").drop(columns="_unpriced").sort_index())
    return out.reset_index(drop=True)


def load_stock_file(path: str | Path) -> pd.DataFrame:
    return normalise(_read_raw(Path(path)))
