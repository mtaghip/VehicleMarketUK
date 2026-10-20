import tempfile
import unittest
from pathlib import Path

import pandas as pd

from buyer import Rules, normalise, pick
from buyer.loader import load_stock_file


def car(**overrides):
    row = {
        "VRM": "AB12CDE", "Make": "Ford", "Model": "Fiesta Titanium", "Year": "2019", "Mileage": "40000",
        "Number of owners": "1", "Service history": "Full", "Exterior grade": "1", "Fuel": "Petrol",
        "On finance": "No", "Mechanical issues disclosed by seller": "No",
        "Equipment": "Sat nav, Heated seats", "Additional specifications": "Apple CarPlay",
        "Additional condition and damage information": "Parcel shelf included",
        "reserve": "10000", "retail": "15000", "avgDays": "25", "retailRating": "92", "demandBarPct": "70",
        "demandLabel": "Higher demand than normal", "status": "Checked",
    }
    row.update(overrides)
    return row


def run(rows, **rule_overrides):
    rules = Rules.load(**rule_overrides)
    scored, _ = pick(normalise(pd.DataFrame(rows)), rules)
    return scored.set_index("vrm")


class EconomicsTests(unittest.TestCase):
    def test_net_profit_and_max_bid_with_margin_scheme_vat(self):
        r = run([car()]).loc["AB12CDE"]
        # resale 14,550; costs 1,050 + 300 prep; VAT (14,550 - 10,000) / 6 = 758
        self.assertEqual(r["resale"], 14550)
        self.assertEqual(r["costs"], 1350)
        self.assertEqual(r["vat"], 758)
        self.assertEqual(r["net_at_reserve"], 2442)
        # Buying at the max bid leaves the 2,000 target: (14,550 - b) * 5/6 - 1,350 = 2,000
        self.assertAlmostEqual(r["max_bid"], 10530, delta=10)
        self.assertEqual(r["verdict"], "BUY")
        self.assertEqual(r["tier"], "A")

    def test_no_vat_option_raises_profit(self):
        r = run([car()], vat_registered=False).loc["AB12CDE"]
        self.assertEqual(r["vat"], 0)
        self.assertEqual(r["net_at_reserve"], 3200)

    def test_demand_bar_fraction_is_converted_to_percent(self):
        cars = normalise(pd.DataFrame([{"VRM": "X1", "Demand bar position %": "0.75", "AT retail GBP": "£9,000"}]))
        self.assertEqual(cars.loc[0, "demand_bar"], 75)
        self.assertEqual(cars.loc[0, "retail"], 9000)

    def test_file_without_reserve_still_gets_a_max_bid(self):
        row = car()
        row.pop("reserve")
        r = run([row]).loc["AB12CDE"]
        self.assertTrue(pd.isna(r["reserve"]))
        self.assertGreater(r["max_bid"], 0)
        self.assertEqual(r["verdict"], "BUY")


    def test_portal_results_format_estimates_demand_bar_from_label(self):
        cars = normalise(pd.DataFrame([
            {"VRM": "V17SPE", "Mileage": "70800", "Retail value": "14836", "AT rating": "82",
             "Days to sell": "32", "Demand": "Lower supply than normal"},
            {"VRM": "NOBAR1", "Mileage": "5000", "Retail value": "9000", "AT rating": "50",
             "Days to sell": "40", "Demand": None},
        ]))
        self.assertEqual(cars.loc[0, "retail"], 14836)
        self.assertEqual(cars.loc[0, "demand_bar"], 63)
        self.assertTrue(cars.loc[0, "demand_bar_estimated"])
        self.assertTrue(pd.isna(cars.loc[1, "demand_bar"]))
        self.assertFalse(cars.loc[1, "demand_bar_estimated"])

    def test_blank_service_history_is_unknown_not_none(self):
        blank = run([car(**{"Service history": ""})]).loc["AB12CDE"]
        none = run([car(**{"Service history": "None"})]).loc["AB12CDE"]
        self.assertEqual(none["costs"] - blank["costs"], 300)
        self.assertNotIn("No service history", blank["warnings"])
        self.assertIn("No service history", none["warnings"])


class RuleTests(unittest.TestCase):
    def test_hard_rejections(self):
        rows = [
            car(VRM="FIN1", **{"On finance": "Yes"}),
            car(VRM="MECH1", **{"Mechanical issues disclosed by seller": "Yes"}),
            car(VRM="OWN1", **{"Number of owners": "5"}),
            car(VRM="GRADE1", **{"Exterior grade": "5"}),
            car(VRM="RES1", reserve="16000"),
            car(VRM="LEASE1", Model="ZOE Dynamique", Fuel="Electric",
                **{"Additional specifications": "Locking Wheel Nut Key, Leased battery."}),
            car(VRM="CAT1", **{"Additional condition and damage information": "Cat N repaired"}),
        ]
        scored = run(rows)
        for vrm in ["FIN1", "MECH1", "OWN1", "GRADE1", "RES1", "LEASE1", "CAT1"]:
            self.assertEqual(scored.loc[vrm, "verdict"], "SKIP", vrm)
            self.assertTrue(scored.loc[vrm, "hard_fails"], vrm)

    def test_stocking_loan_finance_is_only_a_warning(self):
        notes = "Finance flag relates to stocking loan which will be cleared upon collection."
        r = run([car(**{"On finance": "Yes", "Additional condition and damage information": notes})]).loc["AB12CDE"]
        self.assertEqual(r["hard_fails"], [])
        self.assertIn("stocking loan", " ".join(r["warnings"]))

    def test_undamaged_steering_wheel_is_not_flagged(self):
        notes = "Clean and Tidy, No Rips or Tears, Undamaged Steering Wheel"
        r = run([car(**{"Additional condition and damage information": notes})]).loc["AB12CDE"]
        self.assertNotIn("Interior damage", r["warnings"])

    def test_budget_and_slow_cars(self):
        rows = [car(VRM="DEAR1", reserve="30000", retail="40000"),
                car(VRM="SLOW1", avgDays="70", retailRating="10", demandBarPct="30")]
        scored = run(rows, budget_max_buy=20000)
        self.assertEqual(scored.loc["DEAR1", "verdict"], "SKIP")
        self.assertEqual(scored.loc["SLOW1", "tier"], "D")
        self.assertNotEqual(scored.loc["SLOW1", "verdict"], "BUY")

    def test_shortlist_limits_one_fuel_type(self):
        rows = [car(VRM=f"EV{i}", Fuel="Electric") for i in range(8)] + [car(VRM=f"P{i}") for i in range(4)]
        _, shortlist = pick(normalise(pd.DataFrame(rows)), Rules.load(), top=6)
        self.assertLessEqual((shortlist["fuel"] == "Electric").sum(), 3)
        self.assertEqual(len(shortlist), 6)


class LoaderTests(unittest.TestCase):
    def test_excel_without_extension_and_title_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock-check"
            header = ["VRM", "Make", "Model", "Reserve price", "AT retail GBP", "AT average days",
                      "AT retail rating", "Demand bar position %", "Check status"]
            data = [["Stock check - title"] + [""] * 8, [""] * 9, header,
                    ["ZZ99ZZZ", "Kia", "Niro", "9000", "13000", "28", "90", "0.6", "Checked"]]
            try:
                pd.DataFrame(data).to_excel(path.with_suffix(".xlsx"), sheet_name="All cars",
                                            header=False, index=False)
            except ImportError:
                self.skipTest("openpyxl not installed")
            path.with_suffix(".xlsx").rename(path)
            cars = load_stock_file(path)
        self.assertEqual(cars.loc[0, "vrm"], "ZZ99ZZZ")
        self.assertEqual(cars.loc[0, "reserve"], 9000)
        self.assertEqual(cars.loc[0, "demand_bar"], 60)


if __name__ == "__main__":
    unittest.main()
