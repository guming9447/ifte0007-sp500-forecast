"""Synthetic tests for rep_final v2 source control (3 Nov 2026). No real market data is used.
Run from the replication_v04 folder: python3 tests/test_final_v2_source_control.py"""
import csv, json, os, random, subprocess, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rep_core as rc

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGIN, B = "2026-11-06", date(2026, 11, 5)
cal = rc.NYSECalendar(date(2018, 1, 1), date(2027, 12, 31))
random.seed(7)
S = [d for d in cal.sessions if date(2019, 1, 2) <= d <= date(2026, 11, 9)]   # data run past b (6 and 9 Nov)
spx, vix, p, v = {}, {}, 3000.0, 18.0
for d in S:
    p *= 1 + random.gauss(0.0004, 0.01); v = max(9.0, v + random.gauss(0, 0.8))
    spx[d], vix[d] = round(p, 2), round(v, 2)
tmp = tempfile.mkdtemp()


def fred(name, data, col="VALUE"):
    f = os.path.join(tmp, name)
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["observation_date", col])
        for d in sorted(data): w.writerow([d.isoformat(), f"{data[d]:.2f}"])
    return f


def spdji(name, data):
    f = os.path.join(tmp, name)
    with open(f, "w") as fh:
        fh.write("Effective date ,S&P 500\n")
        for d in sorted(data): fh.write(f"{d.strftime('%m/%d/%Y')},{data[d]}\n")
    return f


def cboe(name, data):
    f = os.path.join(tmp, name)
    with open(f, "w") as fh:
        fh.write("DATE,OPEN,HIGH,LOW,CLOSE\n")
        for d in sorted(data): fh.write(f"{d.strftime('%m/%d/%Y')},0,0,0,{data[d]:.6f}\n")
    return f


def run(*args):
    if "--spx-official" in args and "--policy" not in args:
        args = args + ("--policy", "D025")   # tests 1-10 exercise the superseded D-025 path
    out = os.path.join(tmp, f"o{random.random()}.json")
    r = subprocess.run([sys.executable, os.path.join(HERE, "rep_final.py"), "--origin", ORIGIN, "--out", out, *args],
                       capture_output=True, text=True, cwd=HERE)
    return r.returncode, json.load(open(out)) if os.path.exists(out) else r.stderr


hist_s = {d: x for d, x in spx.items() if d < B}; hist_v = {d: x for d, x in vix.items() if d < B}
recent = {d: x for d, x in spx.items() if d >= date(2025, 11, 1)}           # official export: last year incl. b and later
FS, FV = fred("spx.csv", hist_s), fred("vix.csv", hist_v)
OFF, CB = spdji("off.csv", recent), cboe("cboe.csv", {d: x for d, x in vix.items() if d >= date(2000, 1, 1)})
results = []


def sha_of(p):
    import hashlib
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def review(name, entries):
    f = os.path.join(tmp, name)
    json.dump({"reviews": entries}, open(f, "w"))
    return f


def check(name, cond):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name)


# 1 baseline: FRED history, official P0, later dates dropped
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB)
check("1 baseline OK", c == 0 and r["status"] == "OK")
check("1 P0 from official", r["deployed_W260"]["P0"] == spx[B] and r["source_control"][0]["p0_session_from"] == "official")
check("1 dates after b dropped", r["dropped_after_P0_session"]["spx"] == ["2026-11-06", "2026-11-09"])
check("1 VRP branch when Cboe has b", r["deployed_W260"]["row_set"] == "VRP-eligible")
check("1 training labels matured before cutoff", r["deployed_W260"]["last_row_origin"] == "2026-10-23")
base = r["deployed_W260"]

# 2 FRED already holds b with the SAME 2-dp value: official still supplies b, no stop
FS2 = fred("spx_b.csv", {d: x for d, x in spx.items() if d <= B})
c, r = run("--spx", FS2, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB)
check("2 FRED has b (same): OK, official used", c == 0 and r["source_control"][0]["p0_session_from"] == "official" and r["deployed_W260"]["F_full"] == base["F_full"])

# 3 FRED holds b with a DIFFERENT value: STOP; with review note: official value used
bad = dict(spx); bad[B] = spx[B] + 1.0
FS3 = fred("spx_bad_b.csv", {d: x for d, x in bad.items() if d <= B})
c, r = run("--spx", FS3, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB)
check("3 P0 cross-check difference stops", c == 2 and r["status"] == "STOP")
rv3 = review("rv3.json", [{"series": "S&P 500", "date": str(B), "official_value": str(spx[B]), "crosscheck_value": str(bad[B]),
                            "official_sha256": sha_of(OFF), "crosscheck_sha256": sha_of(FS3),
                            "reviewer": "student", "basis": "synthetic test"}])
c, r = run("--spx", FS3, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB, "--diff-review", rv3)
check("3 after bound review: official value used, FRED value not used", c == 0 and r["deployed_W260"]["P0"] == spx[B])
rv3b = review("rv3b.json", [{"series": "S&P 500", "date": str(B), "official_value": str(spx[B]), "crosscheck_value": str(bad[B]),
                             "official_sha256": "0" * 64, "crosscheck_sha256": sha_of(FS3), "reviewer": "student", "basis": "old run"}])
c, r = run("--spx", FS3, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB, "--diff-review", rv3b)
check("3b stale review (other file version) does not clear", c == 2)
rv3c = review("rv3c.json", [{"series": "S&P 500", "date": str(B), "official_value": str(spx[B]), "crosscheck_value": str(bad[B]),
                             "official_sha256": sha_of(OFF), "crosscheck_sha256": sha_of(FS3), "reviewer": "", "basis": "x"}])
c, r = run("--spx", FS3, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB, "--diff-review", rv3c)
check("3c review without reviewer does not clear", c == 2)

# 4 history difference between FRED and official: STOP (v1 printed and continued)
h4 = dict(hist_s); d4 = sorted(d for d in recent if d < B)[100]; h4[d4] += 0.5
c, r = run("--spx", fred("spx_h4.csv", h4), "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB)
check("4 history 2-dp difference stops", c == 2 and any("history date" in s for s in r["stops"]))
h4b = dict(hist_s); h4b[d4] += 0.004   # same at 2 dp after rounding of the official value? official has 2 dp, FRED written at 2 dp
c, r = run("--spx", fred("spx_h4b.csv", h4b), "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB)
check("4b sub-cent noise (rounds equal) passes", c == 0)

# 5 Cboe lacks b: frozen labelled branch, recorded
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF, "--vix-cboe", cboe("cboe_nob.csv", {d: x for d, x in vix.items() if d < B}))
check("5 no Cboe b: labelled branch + note", c == 0 and r["deployed_W260"]["row_set"] == "labelled" and any("rule 3" in n for n in r["notes"]))

# 6 S&P DJI lacks b: STOP (manual download)
c, r = run("--spx", FS, "--vix", FV, "--spx-official", spdji("off_nob.csv", {d: x for d, x in recent.items() if d < B}), "--vix-cboe", CB)
check("6 no official P0: STOP", c == 2 and any("rule 1" in s for s in r["stops"]))

# 7 missing FRED session in the window: STOP, no fill from the official file
h7 = dict(hist_s); del h7[sorted(hist_s)[-10]]
c, r = run("--spx", fred("spx_gap.csv", h7), "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB)
check("7 missing history session stops", c == 2 and r["session_gaps"]["spx"] == [str(sorted(hist_s)[-10])])

# 8 previous snapshot: changed value / vanished session -> STOP; identical -> OK
prev = fred("prev.csv", {d: x for d, x in hist_s.items() if d < date(2026, 10, 1)})
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB, "--spx-prev", prev)
check("8 identical previous snapshot passes", c == 0)
p8 = {d: x for d, x in hist_s.items() if d < date(2026, 10, 1)}; p8[date(2024, 3, 1)] += 1.0
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB, "--spx-prev", fred("prev_bad.csv", p8))
check("8 changed history vs previous snapshot stops", c == 2 and any("changed vs the previous" in s for s in r["stops"]))

# 8b previous snapshot: a sub-cent revision of the same series also stops (full precision, rule 4)
p8b = {d: x for d, x in hist_s.items() if d < date(2026, 10, 1)}; p8b[date(2024, 3, 1)] += 0.001
fprev = os.path.join(tmp, "prev_subcent.csv")
with open(fprev, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["observation_date", "VALUE"])
    for d in sorted(p8b): w.writerow([d.isoformat(), f"{p8b[d]:.3f}"])
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB, "--spx-prev", fprev)
check("8b sub-cent revision vs previous snapshot stops", c == 2 and any("changed vs the previous" in s for s in r["stops"]))

# 8c duplicate dates in an input file stop the run
fdup = os.path.join(tmp, "spx_dup.csv")
with open(fdup, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["observation_date", "VALUE"])
    for d in sorted(hist_s): w.writerow([d.isoformat(), f"{hist_s[d]:.2f}"])
    d0 = sorted(hist_s)[-5]; w.writerow([d0.isoformat(), f"{hist_s[d0]:.2f}"])
c, r = run("--spx", fdup, "--vix", FV, "--spx-official", OFF, "--vix-cboe", CB)
check("8c duplicate dates stop", c == 2 and any("duplicate dates" in s for s in r["stops"]))

# 8d official S&P file in Codex's FRED-format conversion is read the same way
c, r = run("--spx", FS, "--vix", FV, "--spx-official", fred("off_fredfmt.csv", recent, "SP500"), "--vix-cboe", CB)
check("8d official file in FRED format accepted, same result", c == 0 and r["deployed_W260"]["F_full"] == base["F_full"])

# 9 canonical input reproduces the source-control result exactly
can = os.path.join(tmp, "canonical.csv")
with open(can, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["date", "spx_close", "vix_close"])
    for d in sorted(d for d in spx if d <= B): w.writerow([d.isoformat(), spx[d], vix[d]])
c, r = run("--canonical", can)
check("9 canonical = source control (F, n, branch, date-set hash)", c == 0 and all(
    r["deployed_W260"][k] == base[k] for k in ("F_full", "F_2dp", "n_rows", "row_set", "train_origins_sha256", "P0")))

# 9b Codex two-file canonical format gives the same result
c, r = run("--canonical-spx", fred("can_spx.csv", {d: x for d, x in spx.items() if d <= B}, "SP500"),
           "--canonical-vix", fred("can_vix.csv", {d: x for d, x in vix.items() if d <= B}, "VIXCLS"))
check("9b two-file canonical = source control", c == 0 and "two-file" in r["mode"] and all(
    r["deployed_W260"][k] == base[k] for k in ("F_2dp", "n_rows", "row_set", "train_origins_sha256", "P0")))

# 10 FRED-only run is labelled TEST
c, r = run("--spx", FS2, "--vix", FV)
check("10 FRED-only labelled TEST", c == 0 and "TEST" in r["mode"])

# ---------------- D-031 option 2 (default policy since 6 Nov) ----------------
BND = "2019-01-14"                                    # synthetic stand-in for 2016-10-31
bnd = date.fromisoformat(BND)
off_full = {d: x for d, x in spx.items() if d >= bnd}   # official export from the boundary to 9 Nov
OFF31 = spdji("off31.csv", off_full)
D31 = ("--policy", "D031", "--official-from", BND)

c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, *D31)
rs = r["source_control"][0] if isinstance(r, dict) else {}
check("11 D031 baseline OK, same numbers as before", c == 0 and r["deployed_W260"]["F_full"] == base["F_full"]
      and r["deployed_W260"]["train_origins_sha256"] == base["train_origins_sha256"])
check("11 D031 prefix only before boundary; rest official", rs.get("prefix_sessions") == sum(1 for d in spx if d < bnd)
      and rs.get("official_sessions_used") == sum(1 for d in spx if bnd <= d <= B))

o12 = dict(off_full); del o12[sorted(o12)[200]]
c, r = run("--spx", FS, "--vix", FV, "--spx-official", spdji("off31_gap.csv", o12), "--vix-cboe", CB, *D31)
check("12 D031 official missing a session stops (FRED never fills)", c == 2 and any("official export lacks" in x for x in r["stops"]))

h13 = dict(hist_s); d13 = sorted(d for d in hist_s if d >= bnd)[300]; h13[d13] += 0.5
c, r = run("--spx", fred("frozen13.csv", h13), "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, *D31)
check("13 D031 frozen vs official difference stops", c == 2 and any("frozen snapshot and the official" in x for x in r["stops"]))

fn = {d: x for d, x in spx.items() if d <= B}; fn[sorted(fn)[-3]] += 1.0
FN = fred("fred_new14.csv", fn)
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, "--spx-fred-new", FN, *D31)
check("14 D031 new-FRED 2-dp difference stops", c == 2 and any("new FRED and the official" in x for x in r["stops"]))
d14 = sorted(fn)[-3]
ent = {"series": "S&P 500", "date": str(d14), "official_value": str(spx[d14]), "crosscheck_value": str(fn[d14]),
       "official_sha256": sha_of(OFF31), "crosscheck_sha256": sha_of(FN), "reviewer": "student", "basis": "synthetic"}
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, "--spx-fred-new", FN,
           "--diff-review", review("rv14.json", [ent]), *D31)
check("14 after bound review: official values used", c == 0 and r["deployed_W260"]["F_full"] == base["F_full"])
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, "--spx-fred-new", FN,
           "--diff-review", review("rv14b.json", [dict(ent, crosscheck_value=str(fn[d14] + 0.5))]), *D31)
check("14b review naming another value does not clear", c == 2)
c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, "--spx-fred-new", FN,
           "--diff-review", review("rv14c.json", [dict(ent, date=str(sorted(fn)[-4]))]), *D31)
check("14c review for another date does not clear", c == 2)

h13b = dict(hist_s); h13b[d13] += 0.0001                                  # sub-cent revision, Codex's case
f13b = os.path.join(tmp, "frozen13b.csv")
with open(f13b, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["observation_date", "VALUE"])
    for d in sorted(h13b): w.writerow([d.isoformat(), f"{h13b[d]:.4f}"])
c, r = run("--spx", f13b, "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, *D31)
check("13b D031 sub-cent frozen vs official difference stops", c == 2 and any("full precision" in x for x in r["stops"]))

late = {d: x for d, x in spx.items() if d <= sorted(hist_s)[-3]}       # FRED two sessions behind (e.g. 11 Nov not yet posted)
c, r = run("--spx", fred("frozen_short.csv", late), "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB,
           "--spx-fred-new", fred("fred_late.csv", late), *D31)
check("15 D031 late FRED does not stop; official supplies recent history", c == 0 and r["deployed_W260"]["F_full"] == base["F_full"])

c, r = run("--spx", FS, "--vix", FV, "--spx-official", spdji("off31_nob.csv", {d: x for d, x in off_full.items() if d < B}),
           "--vix-cboe", CB, *D31)
check("16 D031 official P0 missing stops", c == 2 and any("official export lacks" in x for x in r["stops"]))

c, r = run("--spx", FS, "--vix", FV, "--spx-official", OFF31, "--vix-cboe", CB, "--policy", "D031")
check("17 D031 fixed boundary 2016-10-31 not covered -> stop", c == 2 and any("official export lacks" in x for x in r["stops"]))

n = sum(ok for _, ok in results)
print(f"\n{n}/{len(results)} passed")
sys.exit(0 if n == len(results) else 1)
