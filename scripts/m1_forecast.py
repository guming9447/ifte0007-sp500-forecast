"""Offline M1 calculator: historical rehearsal only; no live final-day authority.

Uses only price closes, VIX and the frozen calendar. The v04 training-membership
rule is retained without importing an ES dataset or fitting a new factor model.
"""

import argparse
import bisect
import csv
import hashlib
import json
import math
import os
import shutil
import statistics
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
INPUTS = {
    "SP500": ("02_data/raw/2026-10-23_SP500_NDX_benchmark_v02/SP500_fred_original.csv",
              "e9bcd388470935a8131bc8101271205f83ee953aecc527822e64365669b38cc2"),
    "VIXCLS": ("02_data/raw/2026-10-23_SP500_factor_feasibility_v02/VIXCLS_fred_original.csv",
               "c998f6faeb5a203dc65c02a70cb4f0e8955c258b4c92cd719f2ba9899137e90d"),
    "calendar": ("02_data/processed/2026-10-23_SP500_NDX_benchmark_v02/session_calendar.csv",
                 "a2197c758f732d484c5d4d062d998b752a8363fde40c0a7cf81421caad47bb82"),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, indent=2, allow_nan=False, default=lambda v: v.isoformat())
        stream.write("\n")


# 1. Exact saved-input verification is separate from historical availability.
def load_inputs(root):
    records = {}
    for name, (relative, expected) in INPUTS.items():
        path = root / relative
        if sha(path) != expected:
            raise ValueError(f"HASH_MISMATCH: {name}; do not replace the frozen input")
        records[name] = {"path": str(path), "sha256": expected}
    def daily(name):
        with (root / INPUTS[name][0]).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        days = [date.fromisoformat(r["observation_date"]) for r in rows]
        if days != sorted(set(days)):
            raise ValueError("Duplicate or unsorted daily dates")
        values = {d: float(r[name]) for d, r in zip(days, rows) if r[name] not in ("", ".")}
        if not all(math.isfinite(v) and v > 0 for v in values.values()):
            raise ValueError("Nonpositive/nonfinite source value")
        return values
    with (root / INPUTS["calendar"][0]).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    closes = {date.fromisoformat(r["session_date"]): datetime.fromisoformat(r["close_utc"]) for r in rows}
    if len(closes) != len(rows) or any(v.tzinfo is None for v in closes.values()):
        raise ValueError("Invalid dated/aware calendar")
    return daily("SP500"), daily("VIXCLS"), closes, records


# 2. Preserve v04's VRP-availability rule for M1 sample membership only.
def vrp(b, sessions, positions, sp, vix):
    index = positions[b]
    needed = sessions[max(0, index-22):index+1]
    if b not in vix or len(needed) != 23 or any(d not in sp for d in needed):
        return None
    rv = 252/22 * math.fsum((100*math.log(sp[d]/sp[p]))**2 for p, d in zip(needed, needed[1:]))
    return vix[b]**2-rv


def prepare(origin, sp, vix, closes):
    if origin.weekday() != 4:
        raise ValueError("Forecast origin must be Friday")
    cutoff = datetime.combine(origin, time(7), NY)
    sessions = sorted(closes)
    if not sessions or min(sessions) >= origin or max(sessions) < origin+timedelta(days=7):
        raise ValueError("Calendar must cover baseline and full target week")
    positions = {d: i for i, d in enumerate(sessions)}
    b = sessions[bisect.bisect_left(sessions, origin)-1]
    target = sessions[bisect.bisect_right(sessions, origin+timedelta(days=7))-1]
    if b not in sp or closes[b] >= cutoff:
        raise ValueError("Baseline missing or not closed before cutoff")
    # Later stored observations are deliberately inaccessible to estimation.
    sp = {d: v for d, v in sp.items() if d in closes and closes[d] < cutoff}
    vix = {d: v for d, v in vix.items() if d <= b}
    rows = []
    start = min(sp)
    t = start+timedelta(days=(4-start.weekday()) % 7)
    while t < origin:
        bi = bisect.bisect_left(sessions, t)-1
        if bi < 0:
            t += timedelta(days=7)
            continue
        baseline = sessions[bi]
        end = sessions[bisect.bisect_right(sessions, t+timedelta(days=7))-1]
        if closes[end] < cutoff and baseline in sp and end in sp:
            rows.append({"origin": t, "baseline": baseline, "target": end,
                         "target_close": closes[end], "r": math.log(sp[end]/sp[baseline]),
                         "VRP": vrp(baseline, sessions, positions, sp, vix)})
        t += timedelta(days=7)
    if len(rows) < 156:
        raise ValueError("Insufficient mature labels: fewer than156")
    return {"origin": origin, "cutoff": cutoff, "baseline": b, "target": target,
            "target_close": closes[target], "P0": sp[b], "current_VRP": vrp(b, sessions, positions, sp, vix)}, rows


def estimate(current, rows, window=260):
    tail = lambda x: x[-window:] if window is not None else x
    factor_train = tail([r for r in rows if r["VRP"] is not None])
    values = [r["VRP"] for r in factor_train]
    center = statistics.fmean(values) if values else 0
    variable = bool(values) and math.fsum((x-center)**2 for x in values) > 0
    uses_vrp = current["current_VRP"] is not None and len(factor_train) >= 156 and variable
    train = factor_train if uses_vrp else tail(rows)
    if len(train) < 156:
        raise ValueError("Training window must contain at least156 mature labels")
    mu = statistics.fmean(r["r"] for r in train)
    forecast = current["P0"]*math.exp(mu)
    return {"window": window or "expanding", "mu_log_comparable_horizon": mu,
            "mu_percent": 100*mu, "implied_simple_return_percent": 100*math.expm1(mu),
            "forecast_full_precision": forecast, "forecast_2dp": money(forecast),
            "uplift_points": forecast-current["P0"], "n_train": len(train),
            "uses_vrp_sample_membership": uses_vrp, "train_origins": [r["origin"] for r in train],
            "latest_training_target_close": max(r["target_close"] for r in train)}


def money(value):
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def score(forecast, actual):
    f, a = Decimal(str(forecast)), Decimal(str(actual))
    if a <= 0:
        raise ValueError("Actual must be positive")
    return str(max(Decimal(0), 50-500*abs(f-a)/a).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# 3. Scenarios are arithmetic, not probabilities or estimated event effects.
def scenarios(p0, forecast):
    rows = []
    for percent in (-10, -5, -3, -2, -1, 0, 1, 2, 3, 5, 10):
        actual = Decimal(str(p0))*(1+Decimal(percent)/100)
        rows.append({"hypothetical_move_percent": percent, "hypothetical_actual": str(actual),
                     "M0_score": score(p0, actual), "M1_full_precision_score": score(forecast, actual),
                     "M1_rounded_forecast_score": score(money(forecast), actual)})
    return rows


# 4. The CLI is locked to a historical rehearsal; live final mode is not enabled.
def historical_run(root, output, origin):
    if origin > date(2026, 10, 9):
        raise ValueError("Historical rehearsal only, latest approved origin2026-10-09. Final-day execution is disabled.")
    if output.exists():
        raise FileExistsError("Use a fresh output directory; preserve earlier runs")
    from storage_policy import ensure_storage
    ensure_storage(output)
    sp, vix, closes, sources = load_inputs(root)
    current, rows = prepare(origin, sp, vix, closes)
    estimates = {str(w or "expanding"): estimate(current, rows, w) for w in (260, 156, None)}
    output.mkdir(parents=True)
    record = {"mode": "HISTORICAL_DRY_RUN_NOT_SUBMISSION", "model": "M1", **current,
              "default": estimates["260"], "created_utc": datetime.now(UTC),
              "final_forecast_approved": False, "actual_target_close_not_used": True,
              "availability_caveat": "Retrospective current-vintage snapshots; no per-observation publication verification. This is a computational dry run, not proof of historical data availability.",
              "mu_unit": "log return over calendar-defined baseline-to-next-Friday close, not exactly5 trading days or an equity risk premium"}
    save(output/"forecast_record.json", record)
    save(output/"window_sensitivity.json", {"report_only": True, "deployed_window": 260, "estimates": estimates})
    save(output/"scenario_scores.json", {"basis": "historical rehearsal P0 and mu; not November13 forecast or causal event impacts",
                                         "rows": scenarios(current["P0"], estimates["260"]["forecast_full_precision"])})
    save(output/"input_manifest.json", {"sources": sources, "code_sha256": sha(Path(__file__)),
                                        "network_access": False, "TEMP": os.environ.get("TEMP"), "TMP": os.environ.get("TMP")})
    shutil.copy2(__file__, output/"m1_forecast_at_run.py")
    text = ["# Historical M1 dry run (not a submission forecast)", "", f"Origin: {origin}; P0: {current['P0']}", "",
            "| Window | Log-return mu (%) | Forecast (2dp) |", "|---|---:|---:|"]
    text += [f"| {name} | {e['mu_percent']:.9f} | {e['forecast_2dp']} |" for name, e in estimates.items()]
    text += ["", "Default is260; other windows are report-only. Final-day execution and approval are disabled."]
    (output/"DRY_RUN.md").write_text("\n".join(text)+"\n", encoding="utf-8")
    print("\n".join(text))
    print("OUTPUT="+str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", type=date.fromisoformat, default=date(2026, 10, 9))
    args = parser.parse_args()
    historical_run(args.data_root.resolve(), args.output.resolve(), args.origin)
