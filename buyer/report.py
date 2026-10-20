"""Write the picker's results as CSV files and a self-contained HTML buy list."""
from __future__ import annotations

import html
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

CSV_COLUMNS = [
    "verdict", "score", "tier", "quick_sell", "vrm", "make", "model", "year", "mileage", "owners",
    "service_history", "grade", "fuel", "location", "reserve", "retail", "resale", "costs", "vat",
    "net_at_reserve", "max_bid", "bid_headroom", "roi_pct", "avg_days", "rating", "demand_bar",
    "demand_bar_estimated", "demand_label", "spec", "hard_fails", "warnings", "link",
]


def _money(v) -> str:
    return "—" if pd.isna(v) else f"£{v:,.0f}"


def _num(v, fmt="{:,.0f}") -> str:
    return "—" if pd.isna(v) else fmt.format(v)


def _details(r: pd.Series) -> str:
    parts = [f"{r['mileage']:,.0f} mi" if pd.notna(r["mileage"]) else None,
             f"{r['owners']:.0f} owners" if pd.notna(r["owners"]) else None,
             f"grade {r['grade']:.0f}" if pd.notna(r["grade"]) else None,
             f"{r['service_history']} history" if pd.notna(r["service_history"]) else None]
    return " · ".join(p for p in parts if p)


def _flat(df: pd.DataFrame) -> pd.DataFrame:
    out = df.reindex(columns=CSV_COLUMNS).copy()
    for col in ("hard_fails", "warnings"):
        out[col] = out[col].map(lambda items: "; ".join(items) if isinstance(items, list) else "")
    out["score"] = out["score"].round(1)
    return out


def write_reports(scored: pd.DataFrame, shortlist: pd.DataFrame, out_dir: str | Path, source_name: str,
                  rules_summary: str) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    paths = {
        "all": out_dir / f"picker_all_{stamp}.csv",
        "shortlist": out_dir / f"picker_shortlist_{stamp}.csv",
        "html": out_dir / f"picker_report_{stamp}.html",
    }
    _flat(scored).to_csv(paths["all"], index=False)
    _flat(shortlist).to_csv(paths["shortlist"], index=False)
    paths["html"].write_text(_html(scored, shortlist, source_name, rules_summary), encoding="utf-8")
    return paths


def _reason_group(reason: str) -> str:
    if reason.endswith("owners"):
        return "Too many owners"
    if reason.endswith("miles"):
        return "Mileage too high"
    if reason.startswith("Grade"):
        return "Condition grade too poor"
    if reason.startswith("Over budget"):
        return "Over budget"
    return reason.split(" (")[0]


def skip_reason_counts(scored: pd.DataFrame) -> Counter:
    counts: Counter = Counter()
    for fails in scored.loc[scored["verdict"] == "SKIP", "hard_fails"]:
        for reason in fails or ["Profit or speed below target"]:
            counts[_reason_group(reason)] += 1
    return counts


def _html(scored: pd.DataFrame, shortlist: pd.DataFrame, source_name: str, rules_summary: str) -> str:
    verdicts = scored["verdict"].value_counts()
    rows = []
    for i, r in shortlist.iterrows():
        name = " ".join(str(v) for v in (r["make"], r["model"]) if pd.notna(v)) or "Details on listing"
        car = f"{html.escape(name)} {_num(r['year'], '{:.0f}') if pd.notna(r['year']) else ''}".strip()
        if pd.notna(r["link"]):
            car = f'<a href="{html.escape(str(r["link"]))}" target="_blank" rel="noopener">{car}</a>'
        warns = "".join(f"<li>{html.escape(w)}</li>" for w in r["warnings"]) or "<li>None</li>"
        rows.append(f"""
        <tr>
          <td class="n">{i + 1}</td>
          <td><span class="pill {r['verdict'].lower()}">{r['verdict']}</span></td>
          <td><b>{html.escape(str(r['vrm']))}</b><br>{car}<div class="sub">{html.escape(_details(r))}</div></td>
          <td class="n">{_money(r['reserve'])}</td>
          <td class="n"><b>{_money(r['max_bid'])}</b></td>
          <td class="n">{_money(r['net_at_reserve'])}</td>
          <td class="n">{_num(r['avg_days'])}</td>
          <td class="n">{_num(r['rating'])}</td>
          <td class="n">{'~' if r.get('demand_bar_estimated', False) else ''}{_num(r['demand_bar'], '{:.0f}%')}</td>
          <td class="n">{r['tier']}</td>
          <td><ul>{warns}</ul></td>
        </tr>""")
    skips = "".join(f"<li>{html.escape(k)}: <b>{v}</b></li>" for k, v in skip_reason_counts(scored).most_common(8))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Car Buyer Picks</title>
<style>
:root{{--bg:#f6f7f5;--panel:#fff;--ink:#14201d;--ink2:#51605b;--line:#e1e6e3;--buy:#0f766e;--buyb:#d6f0ec;--con:#9a6700;--conb:#fbefd2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1715;--panel:#17211e;--ink:#e8efec;--ink2:#a5b4ae;--line:#26332f;--buy:#4fd1c0;--buyb:#173a35;--con:#f2c14e;--conb:#3a3017}}}}
body{{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif;margin:0;padding:24px 16px}}
.w{{max-width:1200px;margin:0 auto}} h1{{font-size:24px;margin:0 0 4px}} .sub{{color:var(--ink2);font-size:12px}}
.k{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}} .k div{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px 14px}}
.k b{{display:block;font-size:22px}} .t{{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px}}
table{{border-collapse:collapse;width:100%;min-width:980px}} th,td{{padding:8px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink2)}} .n{{text-align:right;font-variant-numeric:tabular-nums}}
ul{{margin:0;padding-left:16px;font-size:12px;color:var(--ink2)}} a{{color:inherit}}
.pill{{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}} .buy{{background:var(--buyb);color:var(--buy)}} .consider{{background:var(--conb);color:var(--con)}}
.p{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 16px;margin-top:16px}}
</style></head><body><div class="w">
<h1>Car buyer picks</h1>
<div class="sub">Source: {html.escape(source_name)} · generated {datetime.now():%d %b %Y %H:%M} · {html.escape(rules_summary)}</div>
<div class="k"><div><b>{len(scored)}</b>cars checked</div><div><b>{verdicts.get('BUY', 0)}</b>BUY</div>
<div><b>{verdicts.get('CONSIDER', 0)}</b>CONSIDER</div><div><b>{verdicts.get('SKIP', 0)}</b>SKIP</div></div>
<div class="t"><table><thead><tr><th>#</th><th>Verdict</th><th>Car</th><th class="n">Reserve</th><th class="n">Max bid</th>
<th class="n">Net at reserve</th><th class="n">Days</th><th class="n">Rating</th><th class="n">Bar</th><th class="n">Tier</th><th>Check before bidding</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="11">No car passed the rules. Loosen config/buyer_rules.json or try another file.</td></tr>'}</tbody></table></div>
<div class="p"><b>Why cars were skipped</b><ul>{skips}</ul></div>
{'<p class="sub"><b>This file has no reserve prices.</b> BUY means worth buying at or below the max bid.</p>' if scored['reserve'].isna().all() else ''}
{'<p class="sub"><b>This file has no make, model, condition or finance details.</b> Check each listing before bidding.</p>' if scored['make'].isna().all() else ''}
{'<p class="sub">~ = demand bar estimated from the demand label.</p>' if scored['demand_bar_estimated'].any() else ''}
<p class="sub">Max bid = highest price that still makes the target profit after costs{' and margin-scheme VAT' if 'VAT' in rules_summary else ''}. Reserve and retail are snapshots, not guaranteed prices. Inspect, HPI-check and test-drive every car.</p>
</div></body></html>"""
