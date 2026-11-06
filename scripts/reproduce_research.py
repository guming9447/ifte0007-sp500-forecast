"""Offline replay of frozen research functions, without private correspondence.

Requires separately held, hash-matching raw files. All generated per-origin
outputs are PRIVATE. This replay does not authorise a new model selection.
"""
import argparse
import csv
from datetime import date
from pathlib import Path

import backtest_method_v04 as base
import backtest_method_v05 as r2
from final_day_pipeline import ensure_e


def run(data_root, output):
    ensure_e(output)
    if output.exists():
        raise FileExistsError("Fresh private output directory required")
    base.ROOT = data_root.resolve()
    sp, vix, closes, hours, sources = base.load_inputs()
    vxv_path = data_root / "02_data/raw/2026-10-25_SP500_round2_data_gate_v02/VXVCLS_fred_original.csv"
    if base.digest(vxv_path) != r2.VXV_HASH:
        raise ValueError("Frozen VIX3M hash mismatch")
    with vxv_path.open(encoding="utf-8-sig", newline="") as stream:
        vxv = {date.fromisoformat(r["observation_date"]): float(r["VXVCLS"]) for r in csv.DictReader(stream) if r["VXVCLS"] not in ("", ".")}
    output.mkdir(parents=True)
    results = {}
    for label, amendment in (("v04", False), ("A1", True)):
        rows = base.build_rows(sp, vix, closes, hours, amendment_a1=amendment)
        selection = base.evaluate(rows, "selection")
        decision = base.select_model(selection)
        holdout = base.deploy(base.evaluate(rows, "holdout"), decision)
        base.save_json(output / (label + "_selection_PRIVATE.json"), selection)
        base.save_json(output / (label + "_holdout_PRIVATE.json"), holdout)
        results[label] = {"D": decision["D"], "selection": base.performance(selection,"M1"),
                          "holdout": base.performance(holdout,"M1"), "coverage": base.coverage(selection)}
        if amendment:
            feature_rows = r2.add_features(rows,sp,vix,closes,vxv,True)
            selected = r2.evaluate(feature_rows,"selection")
            chosen = r2.select_model(selected)
            later = r2.evaluate(feature_rows,"holdout")
            r2.deploy(selected,chosen); r2.deploy(later,chosen)
            base.save_json(output / "round2_selection_PRIVATE.json",selected)
            base.save_json(output / "round2_holdout_PRIVATE.json",later)
            results["round2"] = {"D":chosen["D"], "selection":r2.report(selected), "holdout":r2.report(later)}
    base.save_json(output / "replay_summary.json",results)
    print("Historical replay only; D=" + results["round2"]["D"] + "; keep outputs private")


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    run(args.data_root,args.output)
