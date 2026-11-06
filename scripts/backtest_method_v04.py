"""Reproducible local-only implementation of the approved v04 specification.

Sections follow the research workflow: immutable inputs, time-safe features,
rolling estimation, selection, then hold-out reporting. No network or installs.
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
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT.parent / "Claudeeee/Claude_Work"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
BOUNDARY = datetime(2025, 10, 17, 7, tzinfo=NY)
AS_OF = datetime(2026, 10, 23, tzinfo=UTC)
MODELS = ("M0", "M1", "M2", "M3", "M4", "M5")
ES_MODELS = {"M2", "M4", "M5"}
INPUTS = {
    "sp": ("02_data/raw/2026-10-23_SP500_NDX_benchmark_v02/SP500_fred_original.csv",
           "e9bcd388470935a8131bc8101271205f83ee953aecc527822e64365669b38cc2"),
    "vix": ("02_data/raw/2026-10-23_SP500_factor_feasibility_v02/VIXCLS_fred_original.csv",
            "c998f6faeb5a203dc65c02a70cb4f0e8955c258b4c92cd719f2ba9899137e90d"),
    "es": ("02_data/raw/2026-10-23_SP500_factor_feasibility_v03/ES_yahoo_60m_729d_original.json",
           "a5a64ad408a38ff785d4839b5053a93d9a347a5a63b5a37476fb01788c69b959"),
    "calendar": ("02_data/processed/2026-10-23_SP500_NDX_benchmark_v02/session_calendar.csv",
                 "a2197c758f732d484c5d4d062d998b752a8363fde40c0a7cf81421caad47bb82"),
}


# 1. Read and verify the saved inputs; never download a changing replacement.
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False,
                               default=lambda x: x.isoformat()) + "\n", encoding="utf-8")


def load_inputs():
    manifest = {}
    for key, (relative, expected) in INPUTS.items():
        path = ROOT / relative
        actual = digest(path)
        if actual != expected:
            raise ValueError(f"Frozen source hash mismatch: {path}; stop, do not replace silently")
        manifest[key] = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}

    def daily(key, column):
        with (ROOT / INPUTS[key][0]).open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        days = [r["observation_date"] for r in rows]
        if len(days) != len(set(days)) or days != sorted(days):
            raise ValueError("Duplicate/unsorted daily observations")
        values = {date.fromisoformat(r["observation_date"]): float(r[column])
                  for r in rows if r[column] not in ("", ".")}
        if not all(math.isfinite(v) and v > 0 for v in values.values()):
            raise ValueError("Invalid nonpositive daily input")
        return values

    with (ROOT / INPUTS["calendar"][0]).open(encoding="utf-8-sig", newline="") as f:
        calendar_rows = list(csv.DictReader(f))
    closes = {date.fromisoformat(r["session_date"]): datetime.fromisoformat(r["close_utc"])
              for r in calendar_rows}
    assert len(closes) == len(calendar_rows)
    chart = json.loads((ROOT / INPUTS["es"][0]).read_bytes())["chart"]["result"][0]
    stamps, quote = chart["timestamp"], chart["indicators"]["quote"][0]
    assert stamps == sorted(set(stamps))
    assert all(len(v) == len(stamps) for v in quote.values())
    assert chart["meta"]["exchangeTimezoneName"] == "America/New_York"
    hours = {s: {k: quote[k][i] for k in ("open", "close", "volume")}
             for i, s in enumerate(stamps)}
    return daily("sp", "SP500"), daily("vix", "VIXCLS"), closes, hours, manifest


# 2. F1 depends only on the completed bars in this origin's own window.
def good_bar(bar):
    return bool(bar and all(isinstance(bar.get(k), (int, float)) and
                           math.isfinite(bar[k]) and bar[k] > 0
                           for k in ("open", "close", "volume")))


def f1_window(origin, cash_close, hours, signal_hour=6, amendment_a1=False):
    signal_end = datetime.combine(origin, time(signal_hour), NY)
    start, end = int(cash_close.timestamp()) - 3600, int(signal_end.timestamp()) - 3600
    result = {"valid": False, "ON": None, "anchor_label_epoch": start,
              "signal_label_epoch": end, "signal_end": signal_end,
              "ES_anchor": hours.get(start, {}).get("close"),
              "ES_signal": hours.get(end, {}).get("close"), "reasons": [],
              "max_abs_log_gap": None, "missing_labels": []}
    if not good_bar(hours.get(start)) or not good_bar(hours.get(end)):
        result["reasons"].append("missing_or_nonpositive_endpoint")
    # A1 changes only intermediate-bar volume checks; endpoints stay strict.
    def path_bar(bar):
        if not amendment_a1:
            return good_bar(bar)
        return bool(bar and all(isinstance(bar.get(k), (int, float)) and
                               math.isfinite(bar[k]) and bar[k] > 0
                               for k in ("open", "close")))

    # Ignore empty maintenance bars, but require every other expected hour.
    expected = [s for s in range(start, end + 1, 3600)
                if datetime.fromtimestamp(s, NY).hour != 17]
    result["missing_labels"] = [s for s in expected if not path_bar(hours.get(s))]
    if result["missing_labels"]:
        result["reasons"].append("missing_required_hour")
    present = sorted(s for s, bar in hours.items() if start <= s <= end and path_bar(bar))
    gaps = []
    for previous, current in zip(present, present[1:]):
        p, c = datetime.fromtimestamp(previous, NY), datetime.fromtimestamp(current, NY)
        maintenance = (p.date() == c.date() and p.hour == 16 and c.hour == 18
                       and p.minute == c.minute == p.second == c.second == 0
                       and current - previous == 7200)
        if current - previous != 3600 and not maintenance:
            result["reasons"].append("irregular_spacing")
        gaps.append(abs(math.log(hours[current]["open"] / hours[previous]["close"])))
    if gaps:
        result["max_abs_log_gap"] = max(gaps)
        if max(gaps) > 0.0025:
            result["reasons"].append("suspected_splice_or_genuine_gap")
    result["reasons"] = sorted(set(result["reasons"]))
    result["valid"] = not result["reasons"]
    if result["valid"]:
        result["ON"] = math.log(result["ES_signal"] / result["ES_anchor"])
    return result


def vrp_feature(baseline_index, sessions, sp, vix, n=22):
    b = sessions[baseline_index]
    needed = sessions[max(0, baseline_index-n):baseline_index+1]
    rv = None
    if len(needed) == n + 1 and all(d in sp for d in needed):
        rv = 252 / n * math.fsum((100 * math.log(sp[d] / sp[p])) ** 2
                                for p, d in zip(needed, needed[1:]))
    v = vix.get(b)
    return {"VIX": v, "RV": rv, "VRP": v*v-rv if v is not None and rv is not None else None,
            "rv_dates": needed}


def sample_name(origin, target_close):
    if target_close < BOUNDARY:
        return "selection"
    if origin >= BOUNDARY.date():
        return "holdout"
    return "embargo"


def build_rows(sp, vix, closes, hours, rv_n=22, signal_hour=6, amendment_a1=False):
    sessions, rows = sorted(closes), []
    origin = min(sp) + timedelta(days=(4-min(sp).weekday()) % 7)
    while origin + timedelta(days=7) <= max(sessions):
        cutoff = datetime.combine(origin, time(7), NY)
        if cutoff >= AS_OF:
            break
        bi = bisect.bisect_left(sessions, origin)-1
        target = sessions[bisect.bisect_right(sessions, origin+timedelta(days=7))-1]
        b = sessions[bi]
        if b in sp and target in sp and closes[target] < AS_OF:
            feature = vrp_feature(bi, sessions, sp, vix, rv_n)
            rows.append({"origin": origin, "baseline": b, "target": target,
                         "cutoff": cutoff, "cash_close": closes[b], "target_close": closes[target],
                         "P0": sp[b], "A": sp[target], "r": math.log(sp[target]/sp[b]),
                         "sample": sample_name(origin, closes[target]), **feature,
                         "f1": f1_window(origin, closes[b], hours, signal_hour, amendment_a1)})
        origin += timedelta(days=7)
    return rows


# 3. Fit on labels already known at each cutoff, never on the current target.
def usable_rows(rows, cutoff):
    return [r for r in rows if r["target_close"] < cutoff]


def ols(x, y):
    xbar, ybar = statistics.fmean(x), statistics.fmean(y)
    denominator = math.fsum((v-xbar)**2 for v in x)
    if denominator == 0:
        return None
    slope = math.fsum((a-xbar)*(b-ybar) for a, b in zip(x, y)) / denominator
    return ybar-slope*xbar, slope


def forecasts(row, previous, window=260, shrinkage=1.0, restricted=True):
    prior = usable_rows(previous, row["cutoff"])
    if len(prior) < 156:
        return None
    tail = lambda values: values[-window:] if window is not None else values
    base_train = tail(prior)
    factor_train = tail([r for r in prior if r["VRP"] is not None])
    fit = None
    if row["VRP"] is not None and len(factor_train) >= 156:
        fit = ols([r["VRP"] for r in factor_train], [r["r"] for r in factor_train])
    chosen_train = factor_train if fit is not None else base_train
    mu = statistics.fmean(r["r"] for r in chosen_train)
    a = b = g = None
    flags = []
    if fit is not None:
        a, b = fit
        g = a+b*row["VRP"]
        if restricted and b <= 0:
            g = mu
            flags.append("fallback_drift")
        if restricted and g < 0:
            g = 0.0
            flags.append("truncated")
        g = mu + shrinkage*(g-mu)
    on = row["f1"]["ON"]
    logs = {"M0": 0, "M1": mu, "M2": on, "M3": g,
            "M4": on+g if on is not None and g is not None else None,
            "M5": on+mu if on is not None else None}
    predictions = {m: row["P0"]*math.exp(v) if v is not None else None for m, v in logs.items()}
    return {**row, "f2_available": fit is not None, "mu": mu, "a": a, "b": b, "g": g,
            "flags": flags, "n_labelled": len(prior), "n_mu": len(chosen_train),
            "n_vrp": len(factor_train), "train_origins": [r["origin"] for r in chosen_train],
            "latest_train_target_close": max(r["target_close"] for r in chosen_train),
            "F": predictions}


def score_cents(forecast, actual):
    f, a = Decimal(str(forecast)), Decimal(str(actual))
    score = max(Decimal(0), Decimal(50)-500*abs(f-a)/a)
    return int(score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)*100)


def add_metrics(row):
    row["score_cents"] = {m: score_cents(f, row["A"]) if f is not None else None
                          for m, f in row["F"].items()}
    row["ape_percent"] = {m: 100*abs(f-row["A"])/row["A"] if f is not None else None
                          for m, f in row["F"].items()}
    return row


# 4. Selection only. Every comparison uses its own chronological common dates.
def compare(rows, candidate, baseline):
    common = [r for r in rows if r["sample"] == "selection" and
              r["F"][candidate] is not None and r["F"][baseline] is not None]
    differences = [r["score_cents"][candidate]-r["score_cents"][baseline] for r in common]
    h = len(common)//2
    groups = [differences, differences[:h], differences[h:]]
    eligible = len(common) >= (40 if {candidate, baseline} & ES_MODELS else 2)
    return {"candidate": candidate, "baseline": baseline, "n": len(common),
            "origins": [r["origin"] for r in common], "half_counts": [h, len(common)-h],
            "delta": [statistics.fmean(g)/100 if g else None for g in groups],
            "eligible": eligible,
            "pass": eligible and all(g and sum(g) > 0 for g in groups)}


def select_model(rows):
    pairs = [("M1", "M0"), ("M3", "M0"), ("M3", "M1"), ("M2", "M0"),
             ("M5", "M2"), ("M4", "M2"), ("M4", "M3"), ("M2", "M3")]
    comparisons = {f"{a}_vs_{b}": compare(rows, a, b) for a, b in pairs}
    passes = lambda key: comparisons[key]["pass"]
    B = "M1" if passes("M1_vs_M0") else "M0"
    f2_rows = [r for r in rows if r["sample"] == "selection" and r["F"]["M3"] is not None]
    positive = sum(r["b"] > 0 for r in f2_rows)
    f2 = bool(f2_rows) and positive*5 >= len(f2_rows)*4 and passes("M3_vs_M0") and passes("M3_vs_M1")
    f1 = passes("M2_vs_M0")
    if f1 and f2:
        if passes("M4_vs_M2") and passes("M4_vs_M3"):
            D = "M4"
        else:
            common = comparisons["M2_vs_M3"]
            if not common["eligible"]:
                raise ValueError("Both factors pass, but M2/M3 common set <40: selection needs clarification")
            D = "M2" if common["delta"][0] >= 0 else "M3"
    elif f2:
        D = "M3"
    elif f1:
        D = "M5" if B == "M1" and passes("M5_vs_M2") else "M2"
    else:
        D = B
    return {"B": B, "D": D, "F1_pass": f1, "F2_pass": f2,
            "positive_b_count": positive, "f2_compared_count": len(f2_rows),
            "positive_b_share": positive/len(f2_rows) if f2_rows else None,
            "comparisons": comparisons, "holdout_used_for_selection": False}


def deployed_model(D, B, f1, f2):
    if D == "M4":
        return "M4" if f1 and f2 else "M3" if f2 else "M2" if f1 else B
    if D == "M3":
        return "M3" if f2 else B
    if D == "M2":
        return "M2" if f1 else B
    if D == "M5":
        return "M5" if f1 else "M1"
    return D


# 5. Reporting never feeds back into model selection.
def dm_statistic(differences, lag=1):
    """Descriptive loss-difference t statistic with Bartlett Newey-West variance."""
    n = len(differences)
    if n <= lag + 1:
        return None
    mean = statistics.fmean(differences)
    z = [d-mean for d in differences]
    variance = math.fsum(v*v for v in z)/n
    for k in range(1, lag+1):
        covariance = math.fsum(z[i]*z[i-k] for i in range(k, n))/n
        variance += 2*(1-k/(lag+1))*covariance
    return mean/math.sqrt(variance/n) if variance > 0 else None


def performance(rows, candidate, baseline="M0"):
    chosen = [r for r in rows if r["F"].get(candidate) is not None and r["F"].get(baseline) is not None]
    if not chosen:
        return {"n": 0}
    scores = [r["score_cents"][candidate]/100 for r in chosen]
    differences = [(r["score_cents"][candidate]-r["score_cents"][baseline])/100 for r in chosen]
    log_errors = [math.log(r["F"][candidate]/r["A"]) for r in chosen]
    # DM uses the same rounded-score loss as selection: positive favours candidate.
    return {"n": len(chosen), "start": chosen[0]["origin"], "end": chosen[-1]["origin"],
            "mean_score": statistics.fmean(scores), "mean_delta": statistics.fmean(differences),
            "median_delta": statistics.median(differences),
            "wins": sum(d > 0 for d in differences), "losses": sum(d < 0 for d in differences),
            "ties": sum(d == 0 for d in differences),
            "MAE_log": statistics.fmean(abs(e) for e in log_errors),
            "RMSE_log": math.sqrt(statistics.fmean(e*e for e in log_errors)),
            "share_score_below25": sum(s < 25 for s in scores)/len(scores),
            "DM_score_loss_NW1": dm_statistic(differences),
            "DM_score_loss_NW4": dm_statistic(differences, 4),
            "worst5": [{"origin": r["origin"], "score": r["score_cents"][candidate]/100,
                        "log_error": math.log(r["F"][candidate]/r["A"])}
                       for r in sorted(chosen, key=lambda x: (x["score_cents"][candidate], x["origin"]))[:5]]}


def evaluate(rows, sample=None, **settings):
    results = []
    for row in rows:
        if sample is not None and row["sample"] != sample:
            continue
        fitted = forecasts(row, rows, **settings)
        if fitted is not None:
            results.append(add_metrics(fitted))
    return results


def deploy(rows, decision):
    for r in rows:
        model = deployed_model(decision["D"], decision["B"], r["f1"]["valid"], r["f2_available"])
        assert r["F"][model] is not None
        r["deployed_component"] = model
        r["F"]["D"] = r["F"][model]
        r["score_cents"]["D"] = r["score_cents"][model]
        r["ape_percent"]["D"] = r["ape_percent"][model]
    return rows


def coverage(rows):
    return {"population": len(rows), "f1_valid": sum(r["f1"]["valid"] for r in rows),
            "f2_available": sum(r["f2_available"] for r in rows),
            "f1_failure_reasons_overlapping": dict(Counter(reason for r in rows for reason in r["f1"]["reasons"])),
            "deployed_components": dict(Counter(r["deployed_component"] for r in rows if "deployed_component" in r))}


def main(label, amendment_a1=False):
    from storage_policy import ensure_storage
    ensure_storage(ROOT)
    output = ROOT / "04_outputs/tables" / label
    run = ROOT / "07_evidence/runs" / label
    if output.exists() or run.exists():
        raise FileExistsError("Use a new run label; never overwrite prior evidence")
    sp, vix, closes, hours, sources = load_inputs()
    run.mkdir(parents=True)
    output.mkdir(parents=True)
    shutil.copyfile(__file__, run / "backtest_method_v04_at_run.py")
    docs = [CLAUDE / "03_Factors_and_Model/02_Method_Specification_v04.md",
            CLAUDE / "03_Factors_and_Model/02_Method_Specification_v03.md",
            CLAUDE / "03_Factors_and_Model/03_Assumptions_and_Limitations_Register.md",
            CLAUDE / "00_Admin/12_Message_to_Codex_07_CN.md",
            ROOT / "99_Cross_Review/R-07_method_v04_conditional_approval_CN.md",
            ROOT / "07_evidence/2026-10-24_SP500_method_v04_authorization_v05.md"]
    if amendment_a1:
        docs.extend([CLAUDE / "03_Factors_and_Model/04_Spec_Amendment_A1.md",
                     CLAUDE / "00_Admin/13_Message_to_Codex_08_CN.md",
                     ROOT / "99_Cross_Review/R-09_A1_pre_run_review_CN.md",
                     ROOT / "07_evidence/2026-10-25_SP500_A1_authorization_v06.md"])
    copies = []
    for path in docs:
        shutil.copyfile(path, run / path.name)
        copies.append({"original": str(path), "copy": str(run/path.name), "sha256": digest(path)})
    manifest = {"started_utc": datetime.now(UTC), "as_of_for_labels": AS_OF,
                "python": sys.version, "executable": sys.executable,
                "sources": sources, "instruction_copies": copies,
                "code_sha256": digest(Path(__file__)), "output": str(output),
                "network_access": False, "final_forecast": None, "amendment_a1": amendment_a1,
                "runtime_directories": {k: os.environ.get(k) for k in
                    ("TEMP", "TMP", "TMPDIR", "MPLCONFIGDIR", "IPYTHONDIR", "JUPYTER_RUNTIME_DIR",
                     "JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR", "PIP_CACHE_DIR", "PYTHONPYCACHEPREFIX", "XDG_CACHE_HOME")}}
    save_json(run / "manifest.json", manifest)
    rows = build_rows(sp, vix, closes, hours, amendment_a1=amendment_a1)
    selection = evaluate(rows, "selection")
    decision = select_model(selection)
    decision["saved_before_holdout_utc"] = datetime.now(UTC)
    save_json(output / "selection_decision.json", decision)
    decision_hash = digest(output / "selection_decision.json")
    print(json.dumps({"stage": "selection_saved_before_holdout", "B": decision["B"],
                      "D": decision["D"], "F1_pass": decision["F1_pass"], "F2_pass": decision["F2_pass"]}))

    holdout = deploy(evaluate(rows, "holdout"), decision)
    deploy(selection, decision)
    embargo = evaluate(rows, "embargo")
    save_json(output / "selection_per_origin.json", selection)
    save_json(output / "holdout_per_origin.json", holdout)
    save_json(output / "embargo_not_evaluated.json", [{"origin": r["origin"], "target_close": r["target_close"]} for r in embargo])
    summary = {"selection": {m: performance(selection, m) for m in (*MODELS, "D")},
               "holdout": {m: performance(holdout, m) for m in ("D", decision["B"], "M0")},
               "holdout_D_vs_B": performance(holdout, "D", decision["B"]),
               "coverage": {"selection": coverage(selection), "holdout": coverage(holdout)},
               "selection_ON_mean": statistics.fmean(r["f1"]["ON"] for r in selection if r["f1"]["valid"]) if any(r["f1"]["valid"] for r in selection) else None,
               "loss_definition_DM": "50 minus rounded score; positive statistic favours candidate; descriptive, not a significance claim",
               "final_forecast": None}
    save_json(output / "summary.json", summary)

    variants = {"window156": {"window": 156}, "expanding": {"window": None},
                "rv5": {"rv_n": 5}, "lambda0": {"shrinkage": 0.0},
                "lambda_half": {"shrinkage": 0.5}, "restrictions_off": {"restricted": False},
                "signal05": {"signal_hour": 5}}
    sensitivity = {}
    for name, settings in variants.items():
        variant_rows = build_rows(sp, vix, closes, hours, settings.get("rv_n", 22), settings.get("signal_hour", 6), amendment_a1)
        fit_settings = {k: v for k, v in settings.items() if k not in ("rv_n", "signal_hour")}
        result = deploy(evaluate(variant_rows, "selection", **fit_settings), decision)
        save_json(output / f"sensitivity_{name}_per_origin.json", result)
        sensitivity[name] = {"settings": settings, "D_frozen": decision["D"],
                             "D_vs_M0": performance(result, "D"),
                             "candidate_vs_M0": {m: performance(result, m) for m in MODELS},
                             "coverage": coverage(result), "November13_point_change": None}
    save_json(output / "sensitivities_report_only.json", sensitivity)

    # Descriptive stress examples only; no event-specific causal claim or refitting.
    all_results = sorted(selection+holdout, key=lambda r: r["origin"])
    pandemic = [r for r in all_results if date(2020, 2, 1) <= r["origin"] <= date(2020, 3, 31)]
    large_on = sorted([r for r in all_results if r["f1"]["valid"]],
                      key=lambda r: (-abs(r["f1"]["ON"]), r["origin"]))[:5]
    scenarios = {"F2_regime_break_Feb_Mar_2020": {m: performance(pandemic, m) for m in ("M0", "M3", "D")},
                 "largest5_abs_ON_descriptive_not_a_selection_threshold": [
                     {"origin": r["origin"], "ON": r["f1"]["ON"], "r": r["r"],
                      "r_minus_ON": r["r"]-r["f1"]["ON"], "sample": r["sample"]} for r in large_on],
                 "event_shocks_arithmetic_only": [
                     {"hypothetical_actual_over_P0": 1+s,
                      "unchanged_level_score": score_cents(100, 100*(1+s))/100}
                     for s in (-0.03, -0.02, -0.01, 0.01, 0.02, 0.03)],
                 "event_warning": "Hypothetical +/-1,2,3 percent moves are illustrative, not estimated PPI/NVIDIA reactions or probabilities. Event dates/times need final source checks."}
    save_json(output / "adverse_scenarios_descriptive.json", scenarios)
    assert digest(output / "selection_decision.json") == decision_hash
    load_inputs()  # Verify immutable inputs again after all calculations.
    save_json(run / "completion.json", {"completed_utc": datetime.now(UTC), "selection_sha256_unchanged": decision_hash,
              "source_hashes_unchanged": True, "B": decision["B"], "D": decision["D"],
              "final_forecast": None, "independent_replication": "pending",
              "artifacts": [{"path": str(p), "sha256": digest(p), "bytes": p.stat().st_size}
                            for p in sorted(output.glob("*.json"))]})
    print(json.dumps({"output": str(output), "run_evidence": str(run), "coverage": summary["coverage"],
                      "holdout": summary["holdout"]}, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--amendment-a1", action="store_true", help="Approved intermediate-bar volume relaxation only")
    args = parser.parse_args()
    if not args.label or Path(args.label).name != args.label or args.label in (".", ".."):
        parser.error("label must be one folder name")
    main(args.label, args.amendment_a1)
