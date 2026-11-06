"""Claude's independent final-day M1 calculator — v2.2 (6 Nov 2026: full-precision frozen-vs-official stop; review records bound to date, values and file hashes; v2.1 6 Nov: D-031 option 2 default; v2 3 Nov; v1 kept as rep_final_v1_2026-11-03_superseded.py).

Frozen M1 rule (spec v04 §4 last paragraph, A1/v05 unchanged; D-023):
  - origin t (a Friday), cutoff 07:00 New York (12:00 London) on t;
  - P0 = close of the last NYSE session b before t;
  - training rows = weekly labels whose target close occurred before the cutoff;
  - mu = mean log return over the latest <=260 VRP-eligible rows when the current VRP is
    available, >=156 such rows exist and their VRP varies; otherwise latest <=260 labelled rows;
  - F = P0 * exp(mu), reported to 2 dp (ROUND_HALF_UP).

Source control (D-025, D-026 items 1-4; v2 fixes raised by Codex on 3 Nov):
  * History (sessions before b) comes from FRED. The P0-session value comes from the official file
    (S&P DJI for the S&P 500; Cboe for VIX), EVEN IF FRED already holds that date (v1 let FRED win).
  * Any 2-dp difference between FRED and an official file on a history date is a STOP (v1 only printed it).
    A 2-dp difference on the P0 session is a STOP unless a --diff-review JSON entry bound to that date, both values
    and both file hashes records the investigation (v2.2, 6 Nov);
    the official value is then used (rule 2).
  * Optional previous snapshots (--spx-prev/--vix-prev): a changed value or a vanished session is a STOP (rule 4).
  * Every NYSE session in the window the computation can touch must be present (no fill); otherwise STOP.
  * No S&P DJI value for b: STOP (rule 1, manual download). No Cboe value for b: the frozen
    'current VRP unavailable' branch runs and the reason is recorded (rule 3).
  * All series are truncated to dates <= b before computing; dropped later dates are listed.
  * --canonical: a single normalised CSV (date,spx_close,vix_close) prepared after source control, so
    both agents compute from byte-identical input; no merging is done in that mode.
Without official files the run is labelled a FRED-only TEST and is not valid for the final day.
On STOP a JSON with status STOP and the reasons is written (no forecast) and the exit code is 2.
Uses rep_core (Claude's own code); never reads Codex code. Output file is exclusive-create."""
import argparse, csv, json, math, hashlib, sys
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import rep_core as rc

LOOKBACK_SESSIONS = 300 * 5 + 30   # covers 260 weekly labels + 22-session VRP windows with margin


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def r2(x):
    return Decimal(repr(float(x))).quantize(Decimal("0.01"), ROUND_HALF_UP)


def load_spdji_csv(p):
    """S&P DJI export as CSV: either the LibreOffice conversion of the original XLS (rows 'MM/DD/YYYY,value')
    or Codex's receipt-linked conversion in FRED format (header 'observation_date,SP500', ISO dates)."""
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        first = fh.readline().strip().lower()
    if first.startswith("observation_date"):
        return rc.load_fred_csv(p)
    out = {}
    for r in csv.reader(open(p, encoding="utf-8", errors="replace")):
        if len(r) >= 2 and len(r[0]) == 10 and r[0][2] == "/" and r[1].strip():
            out[datetime.strptime(r[0], "%m/%d/%Y").date()] = float(r[1])
    return out


def load_cboe_csv(p):
    """Cboe VIX_History.csv: DATE (MM/DD/YYYY), OPEN, HIGH, LOW, CLOSE."""
    return {datetime.strptime(r["DATE"], "%m/%d/%Y").date(): float(r["CLOSE"]) for r in csv.DictReader(open(p))}


def load_canonical(p):
    """Normalised input: header date,spx_close,vix_close; ISO dates; vix_close may be blank."""
    spx, vix = {}, {}
    rd = csv.DictReader(open(p, newline="", encoding="utf-8"))
    if rd.fieldnames[:3] != ["date", "spx_close", "vix_close"]:
        raise SystemExit("STOP: canonical file header must be date,spx_close,vix_close")
    for r in rd:
        d = date.fromisoformat(r["date"])
        if r["spx_close"].strip():
            spx[d] = float(r["spx_close"])
        if r["vix_close"].strip():
            vix[d] = float(r["vix_close"])
    return spx, vix


def diffs_2dp(a, b, dates):
    return [{"date": str(d), "a": a[d], "b": b[d]} for d in sorted(dates) if r2(a[d]) != r2(b[d])]


class DiffReview:
    """Investigation records for cross-source differences (D-026 item 2). Each entry must name the series, date,
    both values and the SHA-256 of both current input files, plus reviewer and basis; a record made for another
    file version or another value does not clear a difference. (v2.2, 6 Nov, after Codex's review.)"""

    def __init__(self, path, hashes):
        self.path, self.hashes, self.entries = path, hashes, []
        if path:
            self.entries = json.load(open(path, encoding="utf-8")).get("reviews", [])

    def clears(self, series, d, off_val, cross_val, off_key, cross_key):
        for e in self.entries:
            try:
                if (e.get("series") == series and e.get("date") == str(d)
                        and float(e["official_value"]) == off_val and float(e["crosscheck_value"]) == cross_val
                        and e.get("official_sha256") == self.hashes.get(off_key)
                        and e.get("crosscheck_sha256") == self.hashes.get(cross_key)
                        and str(e.get("reviewer", "")).strip() and str(e.get("basis", "")).strip()):
                    return True
            except (KeyError, ValueError, TypeError):
                continue
        return False

    def split(self, series, diffs, off_key, cross_key):
        """diffs: [{'date','a'=crosscheck,'b'=official}] -> (cleared, uncleared)."""
        ok, bad = [], []
        for x in diffs:
            (ok if self.clears(series, x["date"], x["b"], x["a"], off_key, cross_key) else bad).append(x)
        return ok, bad


def control(name, fred, official, b, stops, notes, reviewed, required):
    """History (< b) from FRED, b from the official file. Returns the controlled series."""
    hist = {d: v for d, v in fred.items() if d < b}
    rec = {"series": name, "history_from": "FRED", "p0_session_from": None}
    if official is None:
        rec["p0_session_from"] = "FRED (TEST ONLY: no official file supplied)"
        if b in fred:
            hist[b] = fred[b]
        return hist, rec
    hd = diffs_2dp(fred, official, [d for d in set(fred) & set(official) if d < b])
    rec["history_overlap_days"] = len([d for d in set(fred) & set(official) if d < b])
    rec["history_diffs_2dp"] = hd
    if hd:
        stops.append(f"{name}: {len(hd)} history date(s) differ at 2 dp between FRED and the official file "
                     f"(rule 4: investigate, student approves; no automatic choice)")
    if b in official:
        hist[b] = official[b]
        rec["p0_session_from"] = "official"
        rec["p0_official"] = official[b]
        if b in fred:
            rec["p0_fred_crosscheck"] = fred[b]
            if r2(fred[b]) != r2(official[b]):
                keys = ("spx_official", "spx_fred") if name == "S&P 500" else ("vix_cboe", "vix_fred")
                ok, bad = reviewed.split(name, [{"date": str(b), "a": fred[b], "b": official[b]}], *keys)
                if ok:
                    notes.append(f"{name}: P0-session 2-dp difference FRED {fred[b]} vs official {official[b]}; "
                                 f"official used; review record bound to date, values and file hashes")
                else:
                    stops.append(f"{name}: P0-session values differ at 2 dp (FRED {fred[b]} vs official {official[b]}); "
                                 f"investigate (rule 2), then add a bound entry to --diff-review")
    elif required:
        stops.append(f"{name}: official file has no value for the P0 session {b} (rule 1: manual download)")
    else:
        rec["p0_session_from"] = "none (official value unavailable)"
        notes.append(f"{name}: no official value for {b}; frozen 'current VRP unavailable' branch applies (rule 3)")
    return hist, rec


D031_BOUNDARY = date(2016, 10, 31)   # D-031 option 2: official S&P history from this session onward


def control_sp_d031(frozen, official, fred_new, b, boundary, cal, stops, notes, reviewed):
    """D-031 (student, 6 Nov; option 2): S&P 500 closes from the S&P DJI official export for every session
    from `boundary` to the P0 session b; only sessions before `boundary` keep the frozen FRED values.
    New FRED data are a cross-check only and never fill a gap."""
    rec = {"series": "S&P 500", "policy": "D-031 option 2", "official_from": str(boundary),
           "prefix_from": "frozen FRED (sessions before the boundary only)", "p0_session_from": "official"}
    sessions = [d for d in cal.sessions if boundary <= d <= b]
    missing = [str(d) for d in sessions if d not in official]
    nonsession = [str(d) for d in official if boundary <= d <= b and not cal.is_session(d)]
    rec["official_missing_sessions"] = missing
    rec["official_nonsession_dates"] = nonsession
    if missing:
        stops.append(f"S&P 500: official export lacks {len(missing)} session(s) between {boundary} and {b} "
                     f"(rule 1: manual export; FRED never fills)")
    if nonsession:
        stops.append(f"S&P 500: official export has {len(nonsession)} value(s) on non-session dates (investigate)")
    ov = [d for d in set(frozen) & set(official) if boundary <= d < b]
    hd = diffs_2dp(frozen, official, ov)
    rec["frozen_overlap_days"] = len(ov)
    rec["frozen_vs_official_diffs_2dp"] = hd
    exact = [{"date": str(d), "frozen": frozen[d], "official": official[d]} for d in sorted(ov) if frozen[d] != official[d]]
    rec["frozen_vs_official_exact_diffs"] = exact
    if exact:   # D-032: ANY difference stops, including sub-cent ones (v2.2; v2.1 stopped only on 2-dp differences)
        stops.append(f"S&P 500: {len(exact)} date(s) differ between the frozen snapshot and the official export "
                     f"at full precision (D-032: stop and investigate; no automatic choice)")
    if fred_new is not None:
        ov2 = [d for d in set(fred_new) & set(official) if boundary <= d <= b]
        d2 = diffs_2dp(fred_new, official, ov2)
        rec["new_fred_crosscheck_days"] = len(ov2)
        rec["new_fred_vs_official_diffs_2dp"] = d2
        if d2:
            ok, bad = reviewed.split("S&P 500", d2, "spx_official", "spx_fred_new")
            rec["new_fred_diffs_cleared_by_review"] = [x["date"] for x in ok]
            if ok:
                notes.append(f"S&P 500: {len(ok)} 2-dp difference(s) new FRED vs official cleared by bound review records; official used")
            if bad:
                stops.append(f"S&P 500: {len(bad)} 2-dp difference(s) between new FRED and the official export without a "
                             f"bound review record ({', '.join(x['date'] for x in bad[:5])}); investigate (rule 2)")
    series = {d: v for d, v in frozen.items() if d < boundary}
    series.update({d: v for d, v in official.items() if boundary <= d <= b})
    rec["prefix_sessions"] = sum(1 for d in series if d < boundary)
    rec["official_sessions_used"] = sum(1 for d in series if d >= boundary)
    if b in official:
        rec["p0_official"] = official[b]
    return series, rec


def check_prev(name, prev, new, b, stops):
    """Rule 4: a revision of the SAME series is checked at full precision (any change stops);
    the 2-dp tolerance of rule 2 applies only to cross-source comparisons. (Changed 5 Nov per Codex.)"""
    common = [d for d in set(prev) & set(new) if d < b]
    changed = [{"date": str(d), "prev": prev[d], "new": new[d]} for d in sorted(common) if prev[d] != new[d]]
    vanished = sorted(str(d) for d in prev if d < b and d not in new)
    if changed:
        stops.append(f"{name}: {len(changed)} history value(s) changed vs the previous snapshot (rule 4)")
    if vanished:
        stops.append(f"{name}: {len(vanished)} session(s) in the previous snapshot are missing now (rule 4)")
    return {"series": name, "overlap_days": len(common), "changed_full_precision": changed, "vanished": vanished}


def duplicate_dates(path):
    """Dates appearing more than once in the first column of a CSV (header skipped)."""
    seen, dup = set(), set()
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = csv.reader(fh)
        next(rows, None)
        for r in rows:
            if r and r[0].strip():
                k = r[0].strip()
                (dup if k in seen else seen).add(k)
    return sorted(dup)


def m1(origins, cal, spx, vix, t, W, Wmin=156):
    b = cal.last_session_before(t)
    use = rc._usable(origins, t, cal)
    lab = use if W is None else use[-W:]
    if len(use) < Wmin:
        raise SystemExit(f"STOP: only {len(use)} usable training rows (< {Wmin})")
    cur_vrp = rc.vrp(cal, spx, vix, b)
    f2_all = [s for s in use if s.vrp is not None]
    f2 = f2_all if W is None else f2_all[-W:]
    f2_ok = cur_vrp is not None and len(f2) >= Wmin and rc._ols([s.vrp for s in f2], [s.r for s in f2]) is not None
    rows = f2 if f2_ok else lab
    mu = sum(s.r for s in rows) / len(rows)
    P0 = spx[b]
    F = P0 * math.exp(mu)
    F2 = Decimal(repr(F)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    dates = [str(r.t) for r in rows]
    return {"origin": str(t), "cutoff_NY": str(rc.cutoff_dt(t)), "P0_session": str(b), "P0": P0,
            "current_vrp_available": cur_vrp is not None,
            "row_set": "VRP-eligible" if f2_ok else "labelled", "n_rows": len(rows),
            "first_row_origin": dates[0], "last_row_origin": dates[-1],
            "last_row_target_close": str(cal.close_dt(rows[-1].T)),
            "train_origins_sha256": hashlib.sha256(",".join(dates).encode()).hexdigest(),
            "train_origins": dates,
            "mu": mu, "mu_pct": round(100 * mu, 6), "F_full": F, "F_2dp": str(F2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", help="normalised CSV date,spx_close,vix_close (replaces all source inputs)")
    ap.add_argument("--canonical-spx", help="Codex format: normalised FRED-style CSV observation_date,SP500 (with --canonical-vix)")
    ap.add_argument("--canonical-vix", help="Codex format: normalised FRED-style CSV observation_date,VIXCLS")
    ap.add_argument("--spx", help="FRED SP500 CSV"); ap.add_argument("--vix", help="FRED VIXCLS CSV")
    ap.add_argument("--spx-official", help="S&P DJI export as CSV (supplies the P0 session)")
    ap.add_argument("--vix-cboe", help="Cboe VIX_History.csv (supplies the P0-session VIX)")
    ap.add_argument("--spx-prev", help="previous FRED SP500 snapshot (rule 4 check)")
    ap.add_argument("--vix-prev", help="previous FRED VIXCLS snapshot (rule 4 check)")
    ap.add_argument("--diff-review", help="JSON {reviews:[{series,date,official_value,crosscheck_value,official_sha256,"
                                          "crosscheck_sha256,reviewer,basis}]}; each entry clears one difference only")
    ap.add_argument("--policy", choices=["D031", "D025"], default="D031",
                    help="S&P source policy: D031 (default; official history from 2016-10-31, frozen FRED prefix, "
                         "new FRED cross-check only) or D025 (superseded: FRED history, official P0 only)")
    ap.add_argument("--spx-fred-new", help="D031: newly downloaded FRED SP500, cross-check only")
    ap.add_argument("--official-from", default=str(D031_BOUNDARY), help="D031 boundary (fixed by D-031; override only in tests)")
    ap.add_argument("--label", default="", help="e.g. REHEARSAL 10 Nov")
    ap.add_argument("--origin", required=True, help="Friday, YYYY-MM-DD")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t = date.fromisoformat(a.origin)
    if t.weekday() != 4:
        raise SystemExit("STOP: origin must be a Friday")
    stops, notes, controls, prev_checks = [], [], [], []
    files = {}
    if a.canonical_spx or a.canonical_vix:
        if not (a.canonical_spx and a.canonical_vix) or a.canonical:
            raise SystemExit("STOP: give both --canonical-spx and --canonical-vix, and not --canonical")
        a.canonical = "two-file"
    if a.canonical == "two-file":
        if any([a.spx, a.vix, a.spx_official, a.vix_cboe]):
            raise SystemExit("STOP: canonical input cannot be combined with source files")
        spx, vix = rc.load_fred_csv(a.canonical_spx), rc.load_fred_csv(a.canonical_vix)
        files.update({"canonical_spx": a.canonical_spx, "canonical_vix": a.canonical_vix})
        mode = "canonical input (Codex two-file format)"
    elif a.canonical:
        if any([a.spx, a.vix, a.spx_official, a.vix_cboe]):
            raise SystemExit("STOP: --canonical cannot be combined with source files")
        spx, vix = load_canonical(a.canonical)
        files["canonical"] = a.canonical
        mode = "canonical input"
    else:
        if not (a.spx and a.vix):
            raise SystemExit("STOP: --spx and --vix (FRED) are required without --canonical")
        files.update({"spx_fred": a.spx, "vix_fred": a.vix})
        spx, vix = rc.load_fred_csv(a.spx), rc.load_fred_csv(a.vix)
        mode = "source control" if a.spx_official else "FRED-only TEST (not valid for the final day)"
    cal = rc.NYSECalendar(date(min(spx).year - 1, 1, 1), date(t.year + 1, 12, 31))
    b = cal.last_session_before(t)
    if not a.canonical:
        off_s = load_spdji_csv(a.spx_official) if a.spx_official else None
        off_v = load_cboe_csv(a.vix_cboe) if a.vix_cboe else None
        if a.spx_official: files["spx_official"] = a.spx_official
        if a.vix_cboe: files["vix_cboe"] = a.vix_cboe
        if a.spx_official and not a.vix_cboe:
            notes.append("VIX: no Cboe file supplied; P0-session VIX treated as unavailable (rule 3)")
            off_v = {}
        new_spx, new_vix = spx, vix
        raw_after = {"spx": sorted({str(d) for src in (spx, off_s or {}) for d in src if d > b}),
                     "vix": sorted({str(d) for src in (vix, off_v or {}) for d in src if d > b})}
        if a.spx_fred_new:
            files["spx_fred_new"] = a.spx_fred_new
        hashes = {k: sha(v) for k, v in files.items()}
        review = DiffReview(a.diff_review, hashes)
        if a.diff_review:
            files["diff_review"] = a.diff_review
        if a.spx_official and a.policy == "D031":
            boundary = date.fromisoformat(a.official_from)
            if boundary != D031_BOUNDARY:
                notes.append(f"TEST ONLY: official boundary overridden to {boundary} (D-031 fixes {D031_BOUNDARY})")
            fred_new = None
            if a.spx_fred_new:
                fred_new = rc.load_fred_csv(a.spx_fred_new)
                raw_after["spx"] = sorted(set(raw_after["spx"]) | {str(d) for d in fred_new if d > b})
            spx, rs = control_sp_d031(spx, off_s, fred_new, b, boundary, cal, stops, notes, review)
            mode = "source control, D-031 option 2"
        else:
            if a.spx_official:
                notes.append("Policy D025 (superseded by D-031) used on request")
                mode = "source control, D-025 (superseded)"
            spx, rs = control("S&P 500", spx, off_s, b, stops, notes, review, required=True)
        controls.append(rs)
        vix, rv = control("VIX", vix, off_v, b, stops, notes, review, required=False); controls.append(rv)
        if a.spx_prev:
            files["spx_prev"] = a.spx_prev; prev_checks.append(check_prev("S&P 500", rc.load_fred_csv(a.spx_prev), new_spx, b, stops))
        if a.vix_prev:
            files["vix_prev"] = a.vix_prev; prev_checks.append(check_prev("VIX", rc.load_fred_csv(a.vix_prev), new_vix, b, stops))
    for k, path in files.items():
        if k == "diff_review":
            continue
        dup = duplicate_dates(path)
        if dup:
            stops.append(f"{k}: duplicate dates in input file ({', '.join(dup[:5])}{' …' if len(dup) > 5 else ''})")
    dropped = raw_after if not a.canonical else {"spx": sorted(str(d) for d in spx if d > b),
                                                 "vix": sorted(str(d) for d in vix if d > b)}
    spx = {d: v for d, v in spx.items() if d <= b}
    vix = {d: v for d, v in vix.items() if d <= b}
    if b not in spx:
        stops.append(f"S&P 500: P0 session {b} missing")
    i = cal.sessions.index(b)
    window = [d for d in cal.sessions[max(0, i - LOOKBACK_SESSIONS): i + 1] if d >= min(spx)]
    gaps = {"spx": [str(d) for d in window if d not in spx],
            "vix_before_P0": [str(d) for d in window[:-1] if d >= min(vix) and d not in vix]}
    if gaps["spx"]:
        stops.append(f"S&P 500: {len(gaps['spx'])} expected session(s) missing in the computation window (rule 4; no fill)")
    if gaps["vix_before_P0"]:
        stops.append(f"VIX: {len(gaps['vix_before_P0'])} expected session(s) missing before P0 (rule 4; no fill)")
    res = {"run_by": "Claude (independent final-day M1, rep_final v2)", "label": a.label, "mode": mode,
           "run_at_utc": datetime.now(timezone.utc).isoformat(),
           "origin": str(t), "P0_session_expected": str(b),
           "input_sha256": {k: sha(v) for k, v in files.items()}, "input_paths": files,
           "source_control": controls, "previous_snapshot_checks": prev_checks,
           "dropped_after_P0_session": dropped, "session_gaps": gaps,
           "window_checked": [str(window[0]), str(window[-1])], "notes": notes, "stops": stops}
    if stops:
        res["status"] = "STOP"
        with open(a.out, "x") as fh:
            json.dump(res, fh, indent=2, default=str)
        print("STOP:"); [print(" -", s) for s in stops]
        sys.exit(2)
    origins = rc.build_origins(cal, spx, vix, None, rc.Config())
    res["status"] = "OK"
    res["deployed_W260"] = m1(origins, cal, spx, vix, t, 260)
    res["report_only"] = {"W156": m1(origins, cal, spx, vix, t, 156), "expanding": m1(origins, cal, spx, vix, t, None)}
    for k in ("W156", "expanding"):
        res["report_only"][k].pop("train_origins")
    F2 = Decimal(res["deployed_W260"]["F_2dp"]); P0 = Decimal(repr(res["deployed_W260"]["P0"]))
    res["scenarios_2dp_forecast"] = {f"{k:+d}%": str(rc.score(float(F2), float(P0 * (1 + Decimal(k) / 100))))
                                    for k in (-10, -5, -3, -2, -1, 0, 1, 2, 3, 5, 10)}
    with open(a.out, "x") as fh:
        json.dump(res, fh, indent=2, default=str)
    d = res["deployed_W260"]
    print(f"[{mode}] origin {d['origin']}  P0 {d['P0_session']} = {d['P0']}  mu {d['mu_pct']}%  "
          f"n {d['n_rows']} ({d['row_set']})  F = {d['F_2dp']}  dates-sha {d['train_origins_sha256'][:12]}")
    print("W156:", res["report_only"]["W156"]["F_2dp"], " expanding:", res["report_only"]["expanding"]["F_2dp"])
    for n in notes:
        print("note:", n)


if __name__ == "__main__":
    main()
