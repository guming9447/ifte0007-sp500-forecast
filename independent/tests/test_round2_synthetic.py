"""Synthetic tests for rep_round2 (v05). No real data. Run: python3 -I tests/test_round2_synthetic.py"""
import math, os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rep_core as rc
import rep_round2 as r2
import test_synthetic as ts

R = []
def check(n, c, d=""): R.append((n, bool(c), d))

CAL = ts.CAL
spx, vix = ts.synth_market(41)
vix3m = {d: v * 1.08 for d, v in vix.items()}

# §2 F3 uses calendar index (20 sessions), not record count; missing endpoint -> None
b = date(2026, 4, 9)                       # Thursday after Good Friday week
i = CAL.sessions.index(b); s20 = CAL.sessions[i - 20]
check("F3: 20 NYSE sessions back (calendar index)", abs(r2.x3_mom20(CAL, spx, b) - math.log(spx[b] / spx[s20])) < 1e-15)
spx_gap = dict(spx); del spx_gap[s20]
check("F3: missing endpoint -> None (no interpolation)", r2.x3_mom20(CAL, spx_gap, b) is None)
spx_mid = dict(spx); del spx_mid[CAL.sessions[i - 10]]
check("F3: missing intermediate close does not matter (endpoints only)", r2.x3_mom20(CAL, spx_mid, b) is not None)

# §2 F4_TS same date, no forward fill
v3 = dict(vix3m); del v3[b]
check("F4_TS: VIX3M missing on b -> None (no mixing dates)", r2.x4_ts(vix, v3, b) is None)
check("F4_TS: value = ln(VIX/VIX3M)", abs(r2.x4_ts(vix, vix3m, b) - math.log(vix[b] / vix3m[b])) < 1e-15)

# §4: no truncation, fallback to fixed M1 drift, M1 unaffected by round-2 availability
cfg = rc.Config(intermediate_volume_check=False)
allo = rc.build_origins(CAL, spx, vix, None, cfg)
pop = rc.estimate_and_forecast(allo, CAL, cfg)
m1_before = {o.t: o.fc["M1"] for o in pop}
v3_holes = {d: v for d, v in vix3m.items() if d.month != 3}       # knock out every March
r2.add_round2(pop, allo, CAL, spx, vix, v3_holes)
check("M1 fixed: unchanged after adding round-2 models", all(o.fc["M1"] == m1_before[o.t] for o in pop))
neg = [o for o in pop if o.info.get("M6_computable") and o.info["b3"] > 0 and o.info["g3"] < 0]
fb = [o for o in pop if o.info.get("M6_computable") and o.info["b3"] <= 0]
check("§4: negative g allowed when b>0 (no truncation) — exists or vacuous", all(o.fc["M6"] < o.P0 for o in neg))
check("§4: b<=0 -> g equals fixed M1 drift", all(abs(o.info["g3"] - o.info["mu"]) < 1e-18 for o in fb))
march = [o for o in pop if o.b.month == 3]
check("F4_TS missing -> M7 not computable", all("M7" not in o.fc for o in march) and len(march) > 0)

# §5 branches with fabricated scores
class O:  # minimal origin stand-in
    def __init__(s, sc, b3, b4): s.fc = sc; s.A = 100.3; s.info = {"b3": b3, "b4": b4}  # M0 err 0.3%, M1 err 0.2%
def mk(n, f6, f7, b3=1.0, b4=1.0):
    return [O({"M0": 100.0, "M1": 100.5, "M6": f6, "M7": f7}, b3, b4) for _ in range(n)]
check("§5: neither passes -> M1", r2.select_round2(mk(60, 101.0, 101.0))["D"] == "M1")
check("§5: one passes (M6) -> M6", r2.select_round2(mk(60, 100.25, 101.0))["D"] == "M6")
check("§5: both pass -> higher common mean (M7)", r2.select_round2(mk(60, 100.25, 100.3))["D"] == "M7")
check("§5: tie -> M6", r2.select_round2(mk(60, 100.3, 100.3))["D"] == "M6")
check("§5: b>0 share < 80% blocks", r2.select_round2(mk(60, 100.25, 101.0, b3=-1.0))["D"] == "M1")
check("§5: < 40 common origins -> not eligible", r2.select_round2(mk(39, 100.25, 101.0))["D"] == "M1")

w = max(len(n) for n, _, _ in R)
for n, ok, d in R: print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(w)}  {d}")
nf = sum(1 for _, ok, _ in R if not ok); print(f"\n{len(R)-nf}/{len(R)} passed"); sys.exit(1 if nf else 0)
