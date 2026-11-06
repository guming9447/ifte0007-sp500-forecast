"""Controlled M1 input locking and offline candidates; never submits or approves.

The frozen estimator remains in m1_forecast.py. Evidence review is a human
attestation, not a timestamp inferred from an observation date or a file hash.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import shutil
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import m1_forecast as m1
from storage_policy import ensure_storage

SP_OFFICIAL_START = date(2016, 10, 31)
SOURCE_POLICY = "D031_OPTION2_OFFICIAL_HISTORY_FIXED_PREFIX"


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def ensure_e(path):
    # Retain the existing helper name for callers; the public default is portable.
    ensure_storage(path)


def daily(path, column):
    """Read FRED-format canonical data without reducing source precision."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    days = [date.fromisoformat(r["observation_date"]) for r in rows]
    if not days or days != sorted(set(days)):
        raise ValueError("Empty, duplicate or unsorted daily dates")
    values = {}
    for d, row in zip(days, rows):
        if row[column] in ("", "."):
            continue
        value = Decimal(row[column])
        if not value.is_finite() or value <= 0:
            raise ValueError("Nonpositive or nonfinite price")
        values[d] = value
    return values


def calendar(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    days = [date.fromisoformat(r["session_date"]) for r in rows]
    if not days or days != sorted(set(days)):
        raise ValueError("Duplicate, unsorted or empty calendar")
    closes = {d: utc(r["close_utc"]) for d, r in zip(days, rows)}
    if any(c.astimezone(m1.NY).date() != d or c.astimezone(m1.NY).time() not in (time(13), time(14), time(16))
           for d, c in closes.items()):
        raise ValueError("Unexpected NYSE close date/time")
    return closes


def check_evidence(record, cutoff, now=None):
    now = now or datetime.now(timezone.utc)
    retrieved = utc(record["retrieved_utc"])
    public = utc(record["available_before_utc"])
    if retrieved > now or public > now or public >= cutoff:
        raise ValueError("Future acquisition or unavailable at cutoff")
    if record.get("review_status") != "verified" or not record.get("reviewer") or not record.get("basis"):
        raise ValueError("Availability needs identified reviewer and basis")
    if not record.get("url", "").startswith("https://"):
        raise ValueError("Source URL required")
    if not record.get("evidence_path") or not record.get("evidence_sha256"):
        raise ValueError("Availability evidence file/hash required, including late acquisition")


def merge(old, new, sessions, baseline, name, required_start=None, allow_nonsession=False):
    """Preserve old history even if a fresh FRED window has lost its left edge."""
    revised = [d for d in old.keys() & new.keys() if d < baseline and old[d] != new[d]]
    if revised:
        raise ValueError(f"HISTORY_REVISION {name}: {revised[:10]}; student resolution required")
    if new:
        vanished = [d for d in old if min(new) <= d <= min(max(new), baseline) and d not in new]
        if vanished:
            raise ValueError(f"HISTORY_DISAPPEARED {name}: {vanished[:10]}")
    result = {d: v for d, v in (old | new).items() if d < baseline}
    if not result:
        raise ValueError("Empty history")
    if any(d not in sessions for d in result) and not allow_nonsession:
        raise ValueError(f"NONSESSION_VALUE {name}")
    start = required_start or min(result)
    result = {d: v for d, v in result.items() if d in sessions and d >= start}
    gaps = [d for d in sessions if start <= d < baseline and d not in result]
    if gaps:
        raise ValueError(f"HISTORY_GAP {name}: {gaps[:10]}; investigate, no filling")
    return result


def write_daily(path, column, sessions, values):
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["observation_date", column])
        writer.writerows((d.isoformat(), str(values[d]) if d in values else "") for d in sessions)


def official_sp_history(old, official, crosscheck, sessions, baseline, reviews, hashes):
    """Option 2: official history from the approved fixed boundary; old prefix only.

    FRED is comparison-only, including when absent or published after cutoff.
    A review is bound to the exact two input versions, date and values.
    """
    if not old or max(old) >= baseline:
        raise ValueError("Unexpected frozen SP boundary")
    required = [d for d in sessions if SP_OFFICIAL_START <= d <= baseline]
    gaps = [d for d in required if d not in official]
    if gaps:
        raise ValueError(f"OFFICIAL_SP_GAP {gaps[:10]}; manual official export required, no FRED filling")
    if any(d not in sessions for d in official if d <= baseline):
        raise ValueError("NONSESSION_VALUE official SP500")
    for d in old.keys() & official.keys():
        if d >= SP_OFFICIAL_START and old[d] != official[d]:
            raise ValueError(f"FROZEN_OFFICIAL_CONFLICT SP500 {d}; separate student historical resolution required")
    # Never extend the five-day exception when a rolling download loses dates.
    values = {d: v for d, v in old.items() if d < SP_OFFICIAL_START}
    values.update({d: official[d] for d in required})
    if any(d not in values for d in sessions if min(old) <= d <= baseline):
        raise ValueError("HISTORY_GAP frozen SP500")
    comparisons = []
    for d in sorted(values):
        if d not in crosscheck:
            comparisons.append({"series": "SP500", "date": d, "status": "FRED_MISSING_NOT_INPUT"})
            continue
        reference = official.get(d, values[d])
        mismatch = m1.money(reference) != m1.money(crosscheck[d])
        review = reviews.get(str(d), {})
        if mismatch:
            if d not in official:
                raise ValueError(f"SP_CROSSCHECK_CONFLICT {d}; obtain official overlap before resolving")
            expected = {"official_sha256": hashes["official"], "crosscheck_sha256": hashes["crosscheck"],
                        "official": str(reference), "crosscheck": str(crosscheck[d])}
            if (not isinstance(review, dict) or not review.get("reviewer") or not review.get("basis")
                    or review.get("resolution") != "use_official"
                    or any(review.get(k) != v for k, v in expected.items())):
                raise ValueError(f"SP_CROSSCHECK_CONFLICT {d}; investigate and record version-bound official resolution")
        comparisons.append({"series": "SP500", "date": d, "different_2dp": mismatch,
                            "official": str(official[d]) if d in official else None,
                            "retained": str(values[d]), "crosscheck": str(crosscheck[d]),
                            "resolution": review if mismatch else None})
    return values, comparisons


def bind_file(record, base):
    path = (base / record["path"]).resolve()
    if not path.is_file() or m1.sha(path) != record["sha256"]:
        raise ValueError("Input hash mismatch: " + str(path))
    return path


def lock(config_path, output):
    ensure_e(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("New run directory required")
    config_bytes = config_path.read_bytes()
    cfg = json.loads(config_bytes)
    if cfg.get("source_policy") != SOURCE_POLICY:
        raise ValueError("Explicit D031 option-2 policy required; old source controls must be migrated")
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    base = config_path.parent
    origin = date.fromisoformat(cfg["origin"])
    if origin not in (date(2026, 11, 6), date(2026, 11, 13)):
        raise ValueError("Only approved rehearsal/final origins")
    cutoff = datetime.combine(origin, time(7), m1.NY).astimezone(timezone.utc)
    inputs = cfg["inputs"]
    if "sp_history" in inputs or "sp_crosscheck" in cfg["availability"]:
        raise ValueError("D031: use sp_crosscheck as comparison-only, without input availability attestation")
    paths = {k: bind_file(r, base) for k, r in inputs.items()}
    closes = calendar(paths["calendar"])
    baseline = max(d for d in closes if d < origin)
    if max(closes) < origin + m1.timedelta(days=7):
        raise ValueError("Calendar does not cover target")
    # Prove the new calendar preserves every frozen date and close, not just counts.
    frozen_calendar = calendar(paths["frozen_calendar"])
    if {d: c for d, c in closes.items() if min(frozen_calendar) <= d <= max(frozen_calendar)} != frozen_calendar:
        raise ValueError("Calendar differs on frozen overlap")
    for key, (_, expected) in m1.INPUTS.items():
        role = {"SP500": "frozen_sp", "VIXCLS": "frozen_vix", "calendar": "frozen_calendar"}[key]
        if inputs[role]["sha256"] != expected:
            raise ValueError("Research baseline identity changed")
    evidence = cfg["availability"]
    for role in ("vix_history", "sp_official"):
        if role not in evidence:
            raise ValueError("Missing availability evidence: " + role)
    for role, record in evidence.items():
        check_evidence(record, cutoff)
        if record.get("input_sha256") != inputs[role]["sha256"]:
            raise ValueError("Availability attestation is not bound to input version")
        if role.endswith("_official"):
            if utc(record["available_before_utc"]) < closes[baseline]:
                raise ValueError("Official daily close claimed public before session close")
            raw_role = record.get("raw_role")
            if raw_role not in paths:
                raise ValueError("Original official response/export must be archived")
            if raw_role != role:
                conversion_role = record.get("conversion_receipt_role")
                if conversion_role not in paths:
                    raise ValueError("Converted input needs source-linked conversion receipt")
                conversion = json.loads(paths[conversion_role].read_text(encoding="utf-8"))
                if (conversion["source_sha256"] != inputs[raw_role]["sha256"] or
                        conversion["converted_sha256"] != inputs[role]["sha256"]):
                    raise ValueError("Broken original-to-converted input linkage")
        ep = (base / record["evidence_path"]).resolve()
        if m1.sha(ep) != record["evidence_sha256"]:
            raise ValueError("Availability evidence hash mismatch")
    if not cfg.get("historical_availability_assumption_acknowledged"):
        raise ValueError("Historical current-vintage assumption must be disclosed")
    series = {}
    provenance = []
    comparisons = []
    unused = {}
    for name, oldkey, newkey, officialkey in (
        ("SP500", "frozen_sp", "sp_crosscheck", "sp_official"),
        ("VIXCLS", "frozen_vix", "vix_history", "vix_official"),
    ):
        old = daily(paths[oldkey], name)
        new = daily(paths[newkey], name) if newkey in paths else {}
        required_start = min(series["SP500"]) if name == "VIXCLS" else None
        unused[name] = {"non_cash_session_dates": [str(d) for d in sorted(old.keys() | new.keys()) if d not in closes],
                        "after_baseline_dates": [str(d) for d in sorted(old.keys() | new.keys()) if d > baseline]}
        official = daily(paths[officialkey], name) if officialkey in paths else {}
        if officialkey in paths and officialkey not in evidence:
            raise ValueError("Official source availability not reviewed")
        if name == "SP500":
            values, checks = official_sp_history(old, official, new, closes, baseline,
                cfg.get("sp_difference_reviews", {}),
                {"official": inputs[officialkey]["sha256"],
                 "crosscheck": inputs.get(newkey, {}).get("sha256")})
            comparisons.extend(checks)
            for d in sorted(values):
                source = oldkey if d < SP_OFFICIAL_START else officialkey
                provenance.append({"series": name, "date": d, "source_role": source,
                                   "source_sha256": inputs[source]["sha256"]})
            series[name] = values
            continue
        values = merge(old, new, closes, baseline, name, required_start, True)
        for d in values.keys() & official.keys():
            if m1.money(values[d]) != m1.money(official[d]):
                raise ValueError(f"OFFICIAL_HISTORY_CONFLICT {name} {d}; student resolution required")
        if baseline not in official:
            if name == "SP500" or not cfg.get("missing_current_vix_reason"):
                raise ValueError("Official current value absent; manual P0 / VIX reason required")
        else:
            if baseline in new:
                mismatch = m1.money(new[baseline]) != m1.money(official[baseline])
                comparisons.append({"series": name, "date": baseline, "different_2dp": mismatch,
                                    "official": str(official[baseline]), "crosscheck": str(new[baseline])})
                if mismatch and not cfg.get("current_difference_review", {}).get(name):
                    raise ValueError("CURRENT_CONFLICT: investigate and record before official override")
            values[baseline] = official[baseline]
        for d in sorted(values):
            source = officialkey if d == baseline else (newkey if d in new else oldkey)
            provenance.append({"series": name, "date": d, "source_role": source,
                               "source_sha256": inputs[source]["sha256"]})
        series[name] = values
    start = min(series["SP500"])
    days = [d for d in closes if start <= d <= baseline]
    output.mkdir(parents=True, exist_ok=True)
    archived = output / "source_files"
    archived.mkdir()
    manifest = {"origin": origin, "cutoff_utc": cutoff, "baseline": baseline,
                "source_policy": cfg["source_policy"],
                "sp_frozen_through": max(daily(paths["frozen_sp"], "SP500")),
                "sp_official_from": SP_OFFICIAL_START,
                "sp_crosscheck_is_not_forecast_input": True,
                "created_utc": datetime.now(timezone.utc), "state": "LOCKED_NOT_FORECAST_APPROVAL",
                "availability": evidence, "files": {}, "historical_availability": "ASSUMED_NOT_PIT_VERIFIED"}
    for role, path in paths.items():
        destination = archived / (role + path.suffix)
        shutil.copy2(path, destination)
        if m1.sha(destination) != inputs[role]["sha256"] or m1.sha(path) != inputs[role]["sha256"]:
            raise ValueError("Source changed while locking: " + role)
        manifest["files"][destination.relative_to(output).as_posix()] = m1.sha(destination)
    for role, record in evidence.items():
        src = base / record["evidence_path"]
        dest = archived / ("evidence_" + role + src.suffix)
        shutil.copy2(src, dest)
        if m1.sha(dest) != record["evidence_sha256"]:
            raise ValueError("Evidence changed while locking")
        manifest["files"][dest.relative_to(output).as_posix()] = m1.sha(dest)
    shutil.copy2(config_path, output / "source_control.json")
    if m1.sha(output / "source_control.json") != config_hash:
        raise ValueError("Control file changed while locking")
    write_daily(output / "SP500.csv", "SP500", days, series["SP500"])
    write_daily(output / "VIXCLS.csv", "VIXCLS", days, series["VIXCLS"])
    shutil.copy2(paths["calendar"], output / "session_calendar.csv")
    m1.save(output / "row_provenance.json", provenance)
    m1.save(output / "source_comparisons.json", comparisons)
    m1.save(output / "unused_observations.json", unused)
    for name in ("SP500.csv", "VIXCLS.csv", "session_calendar.csv", "source_control.json", "row_provenance.json", "source_comparisons.json", "unused_observations.json"):
        manifest["files"][name] = m1.sha(output / name)
    m1.save(output / "lock_manifest.json", manifest)
    print("LOCKED_INPUTS=" + str(output))


def training_hash(dates):
    return hashlib.sha256(",".join(str(d) for d in dates).encode("utf-8")).hexdigest()


def calculate(folder, output):
    ensure_e(output)
    if output.exists():
        raise FileExistsError("Candidate output must be new")
    manifest = json.loads((folder / "lock_manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder.resolve()) or m1.sha(path) != expected:
            raise ValueError("Locked input changed: " + relative)
    sp = {d: float(v) for d, v in daily(folder / "SP500.csv", "SP500").items()}
    vix = {d: float(v) for d, v in daily(folder / "VIXCLS.csv", "VIXCLS").items()}
    current, rows = m1.prepare(date.fromisoformat(manifest["origin"]), sp, vix, calendar(folder / "session_calendar.csv"))
    estimates = {str(w or "expanding"): m1.estimate(current, rows, w) for w in (260, 156, None)}
    default = estimates["260"]
    output.mkdir(parents=True)
    m1.save(output / "candidate.json", {"status": "CANDIDATE_NEEDS_CLAUDE_AND_STUDENT",
        "final_forecast_approved": False, "lock_manifest_sha256": m1.sha(folder / "lock_manifest.json"),
        "input_hashes": {n: manifest["files"][n] for n in ("SP500.csv", "VIXCLS.csv", "session_calendar.csv")},
        "code_hashes": {p.name: m1.sha(p) for p in (Path(__file__), Path(m1.__file__))},
        **current, "default": default, "train_origins_sha256": training_hash(default["train_origins"]),
        "report_only": estimates, "scenario_scores": m1.scenarios(current["P0"], default["forecast_full_precision"])})
    print("CANDIDATE_ONLY " + default["forecast_2dp"] + " OUTPUT=" + str(output))


def compare(candidate, claude):
    """Compare canonical-input run; raw-source run must be reconciled separately."""
    a = candidate["default"]
    b = claude["deployed_W260"]
    checks = {
        "P0": candidate["P0"] == b["P0"],
        "n_train": a["n_train"] == b["n_rows"],
        "train_dates": [str(d) for d in a["train_origins"]] == b["train_origins"],
        "train_hash": candidate["train_origins_sha256"] == b["train_origins_sha256"],
        "full_precision": abs(a["forecast_full_precision"] - b["F_full"]) <= .005,
        "rounded": a["forecast_2dp"] == b["F_2dp"],
        "branch": ("VRP-eligible" if a["uses_vrp_sample_membership"] else "labelled") == b["row_set"],
        "sp_hash": candidate["input_hashes"]["SP500.csv"] == claude["input_sha256"].get("canonical_spx"),
        "vix_hash": candidate["input_hashes"]["VIXCLS.csv"] == claude["input_sha256"].get("canonical_vix"),
        "origin": str(candidate["origin"]) == b["origin"],
        "claude_ok": claude.get("status") == "OK",
    }
    return checks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("lock")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("compare")
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--claude", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("calculate")
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "lock":
            lock(args.config.resolve(), args.output.resolve())
        elif args.command == "calculate":
            calculate(args.inputs.resolve(), args.output.resolve())
        else:
            ensure_e(args.output)
            checks = compare(json.loads(args.candidate.read_text()), json.loads(args.claude.read_text()))
            m1.save(args.output, {"checks": checks, "all_match": all(checks.values()),
                "candidate_sha256": m1.sha(args.candidate), "claude_sha256": m1.sha(args.claude),
                "student_approval": False, "raw_source_independent_run_still_required": True})
            if not all(checks.values()):
                raise ValueError("Independent calculation mismatch")
            print("MATCHED; not student approval")
    except Exception as error:
        ensure_e(args.output)
        stop = args.output.parent / (args.output.name + "_STOP_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
        stop.parent.mkdir(parents=True, exist_ok=True)
        m1.save(stop, {"status": "STOP", "error": str(error), "command": args.command,
                      "utc": datetime.now(timezone.utc), "approved": False})
        raise
