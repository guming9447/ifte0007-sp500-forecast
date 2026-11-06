"""Approved round-2 module. Keep v04/A1 unchanged and M1 exactly fixed.

The numbered sections separate data, factors, estimation, selection and reporting.
Run only after the independent data gate and conditional approval have been saved.
"""

import argparse
import csv
import json
import math
import os
import shutil
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import backtest_method_v04 as base

ROOT = base.ROOT
GATE = ROOT / "07_evidence/runs/2026-10-25_SP500_round2_data_gate_v02/data_gate.json"
VXV = ROOT / "02_data/raw/2026-10-25_SP500_round2_data_gate_v02/VXVCLS_fred_original.csv"
VXV_HASH = "096fe38107b5f2faac0ccf105d6ebaf56eff84432ac0799aa168a9ebd97d39e9"
OLD_OUTPUT = ROOT / "04_outputs/tables/2026-10-25_SP500_backtest_v04_A1_v01"
MODELS = ("M6", "M7")
FEATURE = {"M6": "x3", "M7": "x4"}
DOCS = [
    base.CLAUDE / "03_Factors_and_Model/06_Method_Specification_v05_Round2.md",
    base.CLAUDE / "00_Admin/14_Message_to_Codex_09_CN.md",
    base.CLAUDE / "99_Cross_Review/R-10_Response_to_Codex_R-11.md",
    ROOT / "99_Cross_Review/R-12_method_v05_prerun_review_CN.md",
    ROOT / "07_evidence/2026-10-25_SP500_v05_authorization_v07.md",
    GATE,
    ROOT / "03_analysis/scripts/test_method_v05.py",
    GATE.parent / "v05_preflight_tests.txt",
    ROOT / "07_evidence/runs/2026-10-25_SP500_v05_old_regression_v01/test_results.json",
]


# 1. Read the gate before touching F4 values; verify immutable source hashes.
def load_vxv():
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    if gate["gate"] == "DROP_F4_TRAINING_LT156":
        return {}, False
    if gate["gate"] != "PASS" or gate["factor_values_computed"]:
        raise ValueError("Missing/invalid pre-factor data gate")
    if base.digest(VXV) != VXV_HASH or gate["raw"]["sha256"] != VXV_HASH:
        raise ValueError("VXVCLS hash changed")
    with VXV.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    values = {date.fromisoformat(r["observation_date"]): float(r["VXVCLS"])
              for r in records if r["VXVCLS"] not in ("", ".")}
    if not all(math.isfinite(v) and v > 0 for v in values.values()):
        raise ValueError("Invalid VXVCLS value")
    return values, True


def positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


# 2. Build features from the baseline session only. Missing is never filled.
def feature_at(b, sessions, positions, sp, vix, vxv, f4_enabled=True):
    index = positions[b]
    s20 = sessions[index-20] if index >= 20 else None
    x3 = math.log(sp[b]/sp[s20]) if s20 is not None and positive(sp.get(b)) and positive(sp.get(s20)) else None
    x4 = None
    if f4_enabled and positive(vix.get(b)) and positive(vxv.get(b)):
        x4 = math.log(vix[b]/vxv[b])
    return {"x3": x3, "x4": x4, "s20": s20,
            "P_s20": sp.get(s20), "VIX_same_date": vix.get(b),
            "VIX3M_same_date": vxv.get(b) if f4_enabled else None}


def add_features(rows, sp, vix, closes, vxv, f4_enabled):
    sessions = sorted(closes)
    positions = {d: i for i, d in enumerate(sessions)}
    return [{**r, **feature_at(r["baseline"], sessions, positions, sp, vix, vxv, f4_enabled)} for r in rows]


# 3. Refit each new model on mature labels only. M1 is NOT refitted here.
def fit_one(row, rows, model, mu, window=260):
    feature = FEATURE[model]
    prior = [r for r in rows if r["target_close"] < row["cutoff"] and r[feature] is not None]
    train = prior[-window:] if window is not None else prior
    result = {"available": False, "reason": None, "a": None, "b": None,
              "g": None, "F": None, "fallback_drift": False, "n_train": len(train),
              "train_origins": [r["origin"] for r in train],
              "latest_train_target_close": max((r["target_close"] for r in train), default=None)}
    if row[feature] is None:
        result["reason"] = "current_factor_missing"
        return result
    if len(train) < 156:
        result["reason"] = "training_lt156"
        return result
    fitted = base.ols([r[feature] for r in train], [r["r"] for r in train])
    if fitted is None:
        result["reason"] = "zero_training_variance"
        return result
    a, b = fitted
    g = mu if b <= 0 else a+b*row[feature]
    prediction = row["P0"] * math.exp(g)
    if not all(math.isfinite(v) for v in (a, b, g, prediction)) or prediction <= 0:
        raise ValueError("Numerical failure: stop, do not silently change the model")
    result.update(available=True, a=a, b=b, g=g, F=prediction, fallback_drift=b <= 0)
    return result


def evaluate(rows, sample, window=260):
    output = []
    for row in rows:
        if row["sample"] != sample:
            continue
        fixed = base.forecasts(row, rows)  # Always default v04 M1, even in sensitivities.
        if fixed is None:
            continue
        fields = ("origin", "baseline", "target", "cutoff", "cash_close", "target_close", "P0", "A", "r", "sample",
                  "x3", "x4", "s20", "P_s20", "VIX_same_date", "VIX3M_same_date")
        result = {k: row[k] for k in fields}
        result.update(mu=fixed["mu"], fixed_M1_n_train=fixed["n_mu"],
                      fixed_M1_train_origins=fixed["train_origins"],
                      fixed_M1_uses_vrp_rows=fixed["f2_available"],
                      F={m: fixed["F"][m] for m in ("M0", "M1")}, models={})
        for model in MODELS:
            result["models"][model] = fit_one(row, rows, model, fixed["mu"], window)
            result["F"][model] = result["models"][model]["F"]
        output.append(base.add_metrics(result))
    return output


# 4. Integer score differences implement strict gains and exact score ties.
def compare(rows, candidate, baseline):
    common = sorted([r for r in rows if r["sample"] == "selection" and
                     r["F"][candidate] is not None and r["F"][baseline] is not None], key=lambda r: r["origin"])
    differences = [r["score_cents"][candidate]-r["score_cents"][baseline] for r in common]
    half = len(common)//2
    groups = [differences, differences[:half], differences[half:]]
    sums = [sum(g) for g in groups]
    return {"n": len(common), "origins": [r["origin"] for r in common],
            "half_counts": [half, len(common)-half], "sum_delta_cents": sums,
            "delta": [sum(g)/len(g)/100 if g else None for g in groups],
            "eligible": len(common) >= 40,
            "pass": len(common) >= 40 and all(s > 0 for s in sums)}


def select_model(rows):
    comparisons, admission = {}, {}
    for m in MODELS:
        for benchmark in ("M0", "M1"):
            comparisons[f"{m}_vs_{benchmark}"] = compare(rows, m, benchmark)
        common = [r for r in rows if r["sample"] == "selection" and r["F"][m] is not None]
        positive_count = sum(r["models"][m]["b"] > 0 for r in common)
        sign_pass = bool(common) and positive_count*5 >= len(common)*4
        passed = sign_pass and all(comparisons[f"{m}_vs_{b}"]["pass"] for b in ("M0", "M1"))
        admission[m] = {"n": len(common), "positive_b_count": positive_count,
                        "positive_b_share": positive_count/len(common) if common else None,
                        "positive_sign_pass": sign_pass, "pass": passed}
    passed = [m for m in MODELS if admission[m]["pass"]]
    if len(passed) == 0:
        D, reason = "M1", "neither_new_model_passes"
    elif len(passed) == 1:
        D, reason = passed[0], "only_one_new_model_passes"
    else:
        head = compare(rows, "M6", "M7")
        comparisons["M6_vs_M7"] = head
        if not head["eligible"]:
            D, reason = "M1", "both_fail_required_head_to_head_eligibility"
        else:
            D = "M6" if head["sum_delta_cents"][0] >= 0 else "M7"
            reason = "higher_common_mean_or_exact_tie_M6"
    return {"D": D, "reason": reason, "admission": admission,
            "comparisons": comparisons, "holdout_used_for_selection": False,
            "multiple_testing_control": False, "third_round_authorized": False}


# 5. Native predictions keep nulls. Deployed predictions always fall back to M1.
def deploy(rows, decision):
    for row in rows:
        row["deployment"] = {}
        for name, selected in (("M6_deployed", "M6"), ("M7_deployed", "M7"), ("D", decision["D"])):
            component = selected if row["F"][selected] is not None else "M1"
            row["deployment"][name] = component
            row["F"][name] = row["F"][component]
        base.add_metrics(row)
    return rows


def report(rows):
    return {
        "population": len(rows),
        "coverage": {m: {"native_available": sum(r["models"][m]["available"] for r in rows),
                         "positive_slope": sum(r["models"][m]["b"] is not None and r["models"][m]["b"] > 0 for r in rows),
                         "unavailable_reasons": dict(Counter(r["models"][m]["reason"] for r in rows if not r["models"][m]["available"]))}
                     for m in MODELS},
        "performance_vs_M0": {m: base.performance(rows, m) for m in ("M0", "M1", "M6", "M7", "M6_deployed", "M7_deployed", "D")},
        "performance_vs_M1": {m: base.performance(rows, m, "M1") for m in ("M6", "M7", "M6_deployed", "M7_deployed", "D")},
    }


def baseline_invariance(rows, sample):
    old = json.loads((OLD_OUTPUT / f"{sample}_per_origin.json").read_text(encoding="utf-8"))
    assert [r["origin"].isoformat() for r in rows] == [r["origin"] for r in old]
    for new, previous in zip(rows, old):
        assert new["mu"] == previous["mu"]
        assert new["fixed_M1_n_train"] == previous["n_mu"]
        assert [d.isoformat() for d in new["fixed_M1_train_origins"]] == previous["train_origins"]
        for model in ("M0", "M1"):
            assert new["F"][model] == previous["F"][model]
            assert new["score_cents"][model] == previous["score_cents"][model]
    return {"sample": sample, "origins": len(rows), "exact_M0_M1_mu_training_match": True}


def protected_hashes():
    files = [ROOT / relative for relative, _ in base.INPUTS.values()]
    files += [VXV, Path(base.__file__)]
    for label in ("2026-10-25_SP500_backtest_v04_v01", "2026-10-25_SP500_backtest_v04_A1_v01"):
        for parent in ("04_outputs/tables", "07_evidence/runs"):
            files.extend(p for p in (ROOT / parent / label).rglob("*") if p.is_file())
    return {str(p): base.digest(p) for p in files}


# 6. A fresh run folder preserves history. Selection is saved before holdout fitting.
def main(label):
    from storage_policy import ensure_storage
    ensure_storage(ROOT)
    output, run = ROOT / "04_outputs/tables" / label, ROOT / "07_evidence/runs" / label
    if output.exists() or run.exists():
        raise FileExistsError("New labels only; preserve prior evidence")
    old_hashes = protected_hashes()
    sp, vix, closes, hours, sources = base.load_inputs()
    vxv, enabled = load_vxv()
    output.mkdir(parents=True)
    run.mkdir(parents=True)
    copies = []
    for path in [Path(__file__), Path(base.__file__), *DOCS]:
        shutil.copy2(path, run / path.name)
        copies.append({"original": str(path), "sha256": base.digest(path)})
    manifest = {"started_utc": datetime.now(base.UTC), "python": sys.version, "executable": sys.executable,
                "sources": sources, "VXVCLS": {"path": str(VXV), "sha256": VXV_HASH}, "f4_enabled": enabled,
                "frozen_inputs_and_prior_evidence": old_hashes, "copies": copies,
                "as_of_for_labels": base.AS_OF, "output": str(output), "network_access": False,
                "runtime_directories": {k: os.environ.get(k) for k in ("TEMP", "TMP", "TMPDIR", "MPLCONFIGDIR", "IPYTHONDIR", "JUPYTER_RUNTIME_DIR", "JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR", "PIP_CACHE_DIR", "PYTHONPYCACHEPREFIX", "XDG_CACHE_HOME")},
                "final_forecast": None}
    base.save_json(run / "manifest.json", manifest)
    rows = add_features(base.build_rows(sp, vix, closes, hours, amendment_a1=True), sp, vix, closes, vxv, enabled)
    selection = evaluate(rows, "selection")
    checks = [baseline_invariance(selection, "selection")]
    decision = select_model(selection)
    decision["saved_before_holdout_utc"] = datetime.now(base.UTC)
    base.save_json(output / "selection_decision.json", decision)
    decision_hash = base.digest(output / "selection_decision.json")
    print(json.dumps({"stage": "selection_saved_before_holdout", "D": decision["D"], "admission": decision["admission"]}), flush=True)

    holdout = evaluate(rows, "holdout")
    checks.append(baseline_invariance(holdout, "holdout"))
    deploy(selection, decision)
    deploy(holdout, decision)
    base.save_json(output / "selection_per_origin.json", selection)
    base.save_json(output / "holdout_per_origin.json", holdout)
    base.save_json(output / "embargo_not_evaluated.json", [{"origin": r["origin"], "target_close": r["target_close"]} for r in rows if r["sample"] == "embargo"])
    summary = {"selection": report(selection), "holdout": report(holdout),
               "holdout_description": "Not used for numerical selection in round2; previously seen period, not an independent clean sample.",
               "statistics": "Rounded-score differences and descriptive Newey-West statistics; no formal multiple-testing control or significance claim.",
               "availability": "Current-vintage histories; same-evening Cboe availability assumed, not verified. Final-day official pair is mandatory or M1 fallback.",
               "final_forecast": None}
    base.save_json(output / "summary.json", summary)

    sensitivities = {}
    for name, window in (("window156", 156), ("expanding", None)):
        result = {}
        for sample in ("selection", "holdout"):
            variant = deploy(evaluate(rows, sample, window), decision)
            baseline_invariance(variant, sample)
            base.save_json(output / f"sensitivity_{name}_{sample}_per_origin.json", variant)
            result[sample] = report(variant)
        sensitivities[name] = {"window": window, "D_frozen": decision["D"], "M1_unchanged": True, **result}
    base.save_json(output / "sensitivities_report_only.json", sensitivities)
    assert old_hashes == protected_hashes()
    assert decision_hash == base.digest(output / "selection_decision.json")
    completion = {"completed_utc": datetime.now(base.UTC), "baseline_invariance": checks,
                  "protected_files_unchanged": len(old_hashes), "decision_sha256": decision_hash,
                  "selection_unchanged_after_holdout_and_sensitivities": True,
                  "output_hashes": {p.name: base.digest(p) for p in output.glob("*.json")}, "final_forecast": None}
    base.save_json(run / "completion.json", completion)
    print(json.dumps({"D": decision["D"], "selection": summary["selection"]["performance_vs_M1"]["D"],
                      "holdout": summary["holdout"]["performance_vs_M1"]["D"], "output": str(output)}, default=str, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    main(parser.parse_args().label)
