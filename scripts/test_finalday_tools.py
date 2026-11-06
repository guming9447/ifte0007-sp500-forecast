"""Offline tests; injected HTTP responses use synthetic bytes, not market data."""

import argparse
import io
import json
import math
import shutil
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

import m1_forecast as m1
import download_evidence_inputs as dl

ROOT = Path(__file__).resolve().parents[2]


class SyntheticModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = date(2020, 1, 1)
        days = [start+timedelta(days=i) for i in range(2400) if (start+timedelta(days=i)).weekday() < 5]
        cls.sp = {d: 100+0.05*i+math.sin(i/9) for i, d in enumerate(days)}
        cls.vix = {d: 20+i%13 for i, d in enumerate(days)}
        cls.closes = {d: datetime.combine(d, time(16), m1.NY) for d in days}
        cls.origin = date(2026, 4, 3)

    def test_future_prices_and_vix_cannot_change_forecast(self):
        current, rows = m1.prepare(self.origin, self.sp, self.vix, self.closes)
        modified = {d: (100000 if d >= self.origin else v) for d, v in self.sp.items()}
        changed_vix = {d: (99999 if d >= self.origin else v) for d, v in self.vix.items()}
        other, later = m1.prepare(self.origin, modified, changed_vix, self.closes)
        self.assertEqual(m1.estimate(current, rows), m1.estimate(other, later))

    def test_current_target_price_not_required(self):
        current, rows = m1.prepare(self.origin, self.sp, self.vix, self.closes)
        truncated = {d: v for d, v in self.sp.items() if d < self.origin}
        other, later = m1.prepare(self.origin, truncated, self.vix, self.closes)
        self.assertEqual(m1.estimate(current, rows), m1.estimate(other, later))

    def test_holiday_previous_label_available(self):
        calendar = {d: v for d, v in self.closes.items() if d != self.origin}
        current, rows = m1.prepare(self.origin, self.sp, self.vix, calendar)
        self.assertIn(date(2026, 3, 27), [r["origin"] for r in rows])
        self.assertTrue(all(r["target_close"] < current["cutoff"] for r in rows))

    def test_missing_current_vix_falls_back_to_label_sample(self):
        current, rows = m1.prepare(self.origin, self.sp, {}, self.closes)
        result = m1.estimate(current, rows)
        self.assertFalse(result["uses_vrp_sample_membership"])
        self.assertEqual(result["n_train"], 260)

    def test_insufficient_training_stops(self):
        short = {d: v for d, v in self.sp.items() if d >= date(2026, 1, 1)}
        with self.assertRaises(ValueError):
            m1.prepare(self.origin, short, self.vix, self.closes)

    def test_missing_baseline_stops(self):
        prices = {d: v for d, v in self.sp.items() if d != date(2026, 4, 2)}
        with self.assertRaises(ValueError):
            m1.prepare(self.origin, prices, self.vix, self.closes)

    def test_negative_mu_is_not_clipped(self):
        rows = [{"origin": date(2020, 1, 1)+timedelta(days=i), "target_close": datetime(2021, 1, 1, tzinfo=m1.UTC), "r": -.01, "VRP": None} for i in range(156)]
        result = m1.estimate({"P0": 100, "current_VRP": None}, rows)
        self.assertLess(result["forecast_full_precision"], 100)

    def test_scenarios_cover_both_models_and_required_moves(self):
        rows = m1.scenarios(100, 100.25)
        self.assertEqual([r["hypothetical_move_percent"] for r in rows], [-10,-5,-3,-2,-1,0,1,2,3,5,10])
        self.assertEqual(rows[0]["M0_score"], "0.00")
        self.assertNotEqual(rows[5]["M1_full_precision_score"], rows[5]["M0_score"])

    def test_round_half_up(self):
        self.assertEqual(m1.money(100.005), "100.01")
        self.assertEqual(m1.score(100, 100), "50.00")

    def test_live_date_is_disabled_before_reading_files(self):
        with self.assertRaisesRegex(ValueError, "Final-day execution is disabled"):
            m1.historical_run(Path("missing"), Path("missing-output"), date(2026,11,13))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = b"date,value\n2020-01-01,123\n"
        self.record = {"id": "synthetic", "relative_path": "private/synthetic.csv", "sha256": dl.digest(self.body), "url": "https://example.org/synthetic.csv", "downloadable": True}

    def test_accept_matching_body(self):
        path = dl.download_one(self.record, self.root, lambda url: (200, self.body, url))
        self.assertEqual(path.read_bytes(), self.body)

    def test_mismatch_stops_before_target(self):
        with self.assertRaisesRegex(ValueError, "HASH_MISMATCH"):
            dl.download_one(self.record, self.root, lambda url: (200, b"changed", url))
        self.assertFalse((self.root/self.record["relative_path"]).exists())
        receipts = list(self.root.rglob("receipt.json"))
        self.assertFalse(json.loads(receipts[0].read_text())["accepted"])

    def test_existing_matching_file_never_fetches(self):
        path = self.root/self.record["relative_path"]
        path.parent.mkdir()
        path.write_bytes(self.body)
        with patch.object(dl, "fetch_https", side_effect=AssertionError("must not fetch")):
            self.assertEqual(dl.download_one(self.record, self.root, lambda url: self.fail("fetch")), path)

    def test_existing_mismatch_never_overwritten(self):
        path = self.root/self.record["relative_path"]
        path.parent.mkdir()
        path.write_bytes(b"previous")
        with self.assertRaises(ValueError):
            dl.download_one(self.record, self.root, lambda url: self.fail("fetch"))
        self.assertEqual(path.read_bytes(), b"previous")

    def test_http_error_stops_even_with_matching_body(self):
        with self.assertRaisesRegex(ValueError, "HTTP_403"):
            dl.download_one(self.record, self.root, lambda url: (403, self.body, url))

    def test_network_failure_is_saved(self):
        def broken(url):
            raise OSError("simulated offline")
        with self.assertRaises(OSError):
            dl.download_one(self.record, self.root, broken)
        self.assertEqual(len(list(self.root.rglob("receipt.json"))), 1)

    def test_path_traversal_stops(self):
        self.record["relative_path"] = "../escape.csv"
        with self.assertRaises(ValueError):
            dl.download_one(self.record, self.root, lambda url: self.fail("fetch"))

    def test_failed_historical_response_not_downloadable(self):
        self.record["downloadable"] = False
        with self.assertRaises(ValueError):
            dl.download_one(self.record, self.root, lambda url: self.fail("fetch"))

    def test_credentials_and_plain_http_rejected(self):
        for url in ("http://example.org", "https://user:password@example.org"):
            with self.assertRaises(ValueError):
                dl.validate_url(url)


class SavedRegressionTests(unittest.TestCase):
    def test_saved_selection_930_rows_and_default_holdout52(self):
        sp, vix, closes, _ = m1.load_inputs(ROOT)
        folder = ROOT/"04_outputs/tables/2026-10-25_SP500_backtest_v04_A1_v01"
        checks = [("selection_per_origin.json",260), ("sensitivity_window156_per_origin.json",156),
                  ("sensitivity_expanding_per_origin.json",None), ("holdout_per_origin.json",260)]
        count = 0
        for filename, window in checks:
            for saved in json.loads((folder/filename).read_text(encoding="utf-8")):
                current, rows = m1.prepare(date.fromisoformat(saved["origin"]), sp, vix, closes)
                result = m1.estimate(current, rows, window)
                self.assertEqual(result["mu_log_comparable_horizon"], saved["mu"])
                self.assertEqual(result["forecast_full_precision"], saved["F"]["M1"])
                self.assertEqual([d.isoformat() for d in result["train_origins"]], saved["train_origins"])
                self.assertEqual(int(100*float(m1.score(result["forecast_full_precision"], saved["A"]))+.001), saved["score_cents"]["M1"])
                count += 1
        self.assertEqual(count, 982)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--synthetic-only", action="store_true", help="Portable tests without private market files")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in
                               ((SyntheticModelTests, DownloadTests) if args.synthetic_only else
                                (SyntheticModelTests, DownloadTests, SavedRegressionTests)))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (args.output/"tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    m1.save(args.output/"test_results.json", {"tests": result.testsRun, "passed": result.wasSuccessful(),
                                            "errors": len(result.errors), "failures": len(result.failures), "network_access": False})
    for path in (Path(__file__), Path(m1.__file__), Path(dl.__file__)):
        shutil.copy2(path, args.output/path.name)
    print(stream.getvalue())
    raise SystemExit(0 if result.wasSuccessful() else 1)
