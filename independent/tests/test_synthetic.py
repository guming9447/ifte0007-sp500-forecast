"""Synthetic-data tests for rep_core (spec v04). NO real market data is used here.

Run:  python3 -I tests/test_synthetic.py   (from the replication_v04 folder)

Each test states which spec rule it protects. A test that fails prints the reason and
the script exits non-zero.
"""
import copy
import math
import os
import random
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rep_core as rc  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


# ---------------------------------------------------------------- synthetic data generators
CAL = rc.NYSECalendar(date(2016, 1, 1), date(2027, 1, 31))


def synth_market(seed=1, vrp_signal=0.0, start=date(2016, 10, 24), end=date(2026, 10, 21)):
    """Daily S&P-like closes and VIX. If vrp_signal > 0, next-day expected log return
    rises with today's (VIX^2 - realised var) so that F2 has a genuine (synthetic) edge."""
    rnd = random.Random(seed)
    spx, vix = {}, {}
    p, sig, hist = 2000.0, 0.01, []
    for d in [s for s in CAL.sessions if start <= s <= end]:
        sig = max(0.004, min(0.04, sig * math.exp(rnd.gauss(0, 0.08))))
        v = 100 * sig * math.sqrt(252) * (1.15 + 0.1 * rnd.random())
        rv = (sum(x * x for x in hist[-22:]) * 252 / 22) if len(hist) >= 22 else (100 * sig) ** 2 * 252
        drift = 0.0003 + vrp_signal * (v * v - rv) / 1e6
        ret = rnd.gauss(drift, sig)
        hist.append(100 * ret)
        p *= math.exp(ret)
        spx[d], vix[d] = round(p, 2), round(v, 2)
    return spx, vix


def synth_es(spx, seed=2, start=date(2024, 10, 24), end=date(2026, 10, 22)):
    """Hourly ES bars (start labels, NY time) consistent with the daily closes:
    the 15:00-labelled bar closes at the cash close times a small basis; overnight hours
    random-walk, and the next cash close continues from the overnight path."""
    rnd = random.Random(seed)
    bars = {}
    days = [d for d in CAL.sessions if start <= d <= end]
    for i, d in enumerate(days[:-1]):
        nxt = days[i + 1]
        lab0 = datetime.combine(d, time(15), rc.NY)
        close0 = spx[d] * 1.003
        bars[lab0] = rc.Bar(lab0, close0, close0, 1000)
        lab = datetime.combine(d, time(16), rc.NY)
        px = close0
        end_lab = datetime.combine(nxt, time(14), rc.NY)
        while lab <= end_lab:
            if lab.time() == time(17):          # maintenance break: no bar
                lab += timedelta(hours=1)
                continue
            o = px
            px = px * math.exp(rnd.gauss(0, 0.0012))
            bars[lab] = rc.Bar(lab, o, px, 500)
            lab += timedelta(hours=1)
        # make next cash close consistent with the overnight ES path (ON is informative)
        spx[nxt] = round(bars[datetime.combine(nxt, time(14), rc.NY)].close / 1.003, 2)
    return bars


# ---------------------------------------------------------------- tests
def test_calendar():
    check("cal: Good Friday 2026-04-03 closed", not CAL.is_session(date(2026, 4, 3)))
    check("cal: 2025-01-09 Carter closure", not CAL.is_session(date(2025, 1, 9)))
    check("cal: 2025-07-03 early close 13:00", CAL.close_dt(date(2025, 7, 3)).hour == 13)
    check("cal: 2025-12-24 early close 13:00", CAL.close_dt(date(2025, 12, 24)).hour == 13)
    check("cal: 2025-11-28 (day after Thanksgiving) early close", CAL.close_dt(date(2025, 11, 28)).hour == 13)
    check("cal: ordinary Thursday 16:00", CAL.close_dt(date(2026, 11, 12)).hour == 16)
    check("cal: final baseline b(13 Nov 2026) = 12 Nov", CAL.last_session_before(date(2026, 11, 13)) == date(2026, 11, 12))
    check("cal: final target T = 20 Nov", CAL.last_session_on_or_before(date(2026, 11, 20)) == date(2026, 11, 20))
    check("cal: cutoff 13 Nov 07:00 ET = 12:00 UTC",
          rc.cutoff_dt(date(2026, 11, 13)).astimezone(rc.timezone.utc).hour == 12)


def test_label_rule():
    """Spec §1: availability by actual target close datetime, incl. Good Friday example."""
    spx, vix = synth_market(3)
    origins = rc.build_origins(CAL, spx, vix, None, rc.Config())
    by_t = {o.t: o for o in origins}
    use = rc._usable(origins, date(2026, 4, 3), CAL)
    check("label: s=2026-03-27 usable at Good Friday origin (T(s)=Thu 2 Apr)",
          by_t[date(2026, 3, 27)].T == date(2026, 4, 2) and by_t[date(2026, 3, 27)] in use)
    use2 = rc._usable(origins, date(2026, 3, 13), CAL)
    check("label: s=t-7 NOT usable in an ordinary week", by_t[date(2026, 3, 6)] not in use2)
    check("label: s=t-14 usable in an ordinary week", by_t[date(2026, 2, 27)] in use2)


def test_f1_validity():
    """Spec §3.1 rules on a hand-built window: Thu 12 Nov 2026 15:00 -> Fri 13 Nov 05:00."""
    b, t = date(2026, 11, 12), date(2026, 11, 13)
    base = {}
    lab = datetime.combine(b, time(15), rc.NY)
    px = 6000.0
    while lab <= datetime.combine(t, time(5), rc.NY):
        if lab.time() != time(17):
            base[lab] = rc.Bar(lab, px, px * 1.0005, 100)
            px *= 1.0005
        lab += timedelta(hours=1)
    on, why = rc.f1_window(base, CAL, b, t)
    check("F1: complete window valid (break 16->18 allowed)", on is not None, why)
    exp_on = math.log(base[datetime.combine(t, time(5), rc.NY)].close / base[datetime.combine(b, time(15), rc.NY)].close)
    check("F1: ON = ln(close 05:00 bar / close 15:00 bar)", on is not None and abs(on - exp_on) < 1e-15)

    w = dict(base); del w[datetime.combine(t, time(1), rc.NY)]
    check("F1: missing middle bar -> invalid", rc.f1_window(w, CAL, b, t)[0] is None)
    w = copy.deepcopy(base); w[datetime.combine(t, time(2), rc.NY)].volume = 0
    check("F1: zero-volume bar counts as missing", rc.f1_window(w, CAL, b, t)[0] is None)
    w = copy.deepcopy(base); k = datetime.combine(t, time(3), rc.NY); w[k].open *= 1.006
    check("F1: 0.6% open gap -> suspected splice", "splice" in rc.f1_window(w, CAL, b, t)[1])
    w = copy.deepcopy(base); w[k].open *= 1.002
    check("F1: 0.2% open gap tolerated", rc.f1_window(w, CAL, b, t)[0] is not None)
    w = copy.deepcopy(base); odd = datetime.combine(t, time(3, 31), rc.NY); w[odd] = rc.Bar(odd, w[k].close, w[k].close, 5)
    check("F1: non-hour label inside window -> invalid (literal §3.1 cond. 2)", rc.f1_window(w, CAL, b, t)[0] is None)
    w = copy.deepcopy(base); w[datetime.combine(b, time(18), rc.NY)].volume = 0
    check("F1 v04: zero-volume 18:00 bar -> invalid (v04 as written)", rc.f1_window(w, CAL, b, t)[0] is None)
    on_a1, _ = rc.f1_window(w, CAL, b, t, intermediate_volume_check=False)
    check("F1 A1: zero-volume intermediate bar tolerated, ON unchanged", on_a1 is not None and abs(on_a1 - exp_on) < 1e-15)
    w = copy.deepcopy(base); w[datetime.combine(b, time(15), rc.NY)].volume = 0
    check("F1 A1: zero-volume ANCHOR bar still invalid", rc.f1_window(w, CAL, b, t, intermediate_volume_check=False)[0] is None)
    w = dict(base); del w[datetime.combine(t, time(1), rc.NY)]
    check("F1 A1: absent intermediate bar still invalid", rc.f1_window(w, CAL, b, t, intermediate_volume_check=False)[0] is None)
    w = dict(base); late = datetime.combine(t, time(6), rc.NY); w[late] = rc.Bar(late, 1.0, 99999.0, 100)
    on2, _ = rc.f1_window(w, CAL, b, t)
    check("F1: bars at/after 06:00 label are ignored (point-in-time)", on2 is not None and abs(on2 - exp_on) < 1e-15)


def test_no_lookahead():
    """Perturb everything after X; every forecast for origins with cutoff < X must be unchanged."""
    spx, vix = synth_market(5)
    es = synth_es(spx, 6)
    cfg = rc.Config()
    pop_a = rc.estimate_and_forecast(rc.build_origins(CAL, spx, vix, es, cfg), CAL, cfg)
    X = datetime(2025, 6, 1, tzinfo=rc.NY)
    spx2 = {d: (v * 1.37 if datetime.combine(d, time(0), rc.NY) >= X else v) for d, v in spx.items()}
    vix2 = {d: (v * 2.5 if datetime.combine(d, time(0), rc.NY) >= X else v) for d, v in vix.items()}
    es2 = {k: (rc.Bar(k, v.open * 0.7, v.close * 1.4, v.volume) if k >= X else v) for k, v in es.items()}
    pop_b = rc.estimate_and_forecast(rc.build_origins(CAL, spx2, vix2, es2, cfg), CAL, cfg)
    fb = {o.t: o.fc for o in pop_b}
    bad = [o.t for o in pop_a if rc.cutoff_dt(o.t) < X and o.fc != fb.get(o.t)]
    n_checked = sum(1 for o in pop_a if rc.cutoff_dt(o.t) < X)
    check("leakage: forecasts before X unchanged after perturbing data after X", not bad and n_checked > 100,
          f"checked {n_checked}, changed {bad[:5]}")


def test_f1_point_in_time():
    """Spec §3.1/§1: ON must not depend on any ES bar labelled at or after 06:00 ET on the origin Friday
    (the 06:00 bar ends 07:00 = cutoff; later bars are post-cutoff). Perturb them all; ON must not move."""
    spx, vix = synth_market(21)
    es = synth_es(spx, 22)
    cfg = rc.Config()
    a = rc.build_origins(CAL, spx, vix, es, cfg)
    es2 = {k: (rc.Bar(k, v.open * 1.05, v.close * 0.93, v.volume)
               if (k.weekday() == 4 and k.time() >= time(6)) else v) for k, v in es.items()}
    b = {o.t: o.on for o in rc.build_origins(CAL, spx, vix, es2, cfg)}
    n_valid = sum(1 for o in a if o.on is not None)
    changed = [o.t for o in a if o.on != b.get(o.t)]
    check("F1 point-in-time: ON unchanged when Friday bars >= 06:00 ET are perturbed",
          not changed and n_valid > 50, f"valid ON {n_valid}, changed {changed[:3]}")


def test_samples():
    spx, vix = synth_market(7)
    cfg = rc.Config()
    pop = rc.estimate_and_forecast(rc.build_origins(CAL, spx, vix, None, cfg), CAL, cfg)
    sel, emb, hold = rc.split_samples(pop, CAL, cfg)
    check("samples: embargo is exactly 2025-10-10", [o.t for o in emb] == [date(2025, 10, 10)], str([o.t for o in emb]))
    check("samples: last selection origin 2025-10-03", sel[-1].t == date(2025, 10, 3))
    check("samples: hold-out starts 2025-10-17", hold[0].t == date(2025, 10, 17))
    check("population: first origin needs >=156 usable rows", pop[0].info["n_usable"] >= 156)


def test_fallback():
    exp = {("M4", True, True): "M4", ("M4", False, True): "M3", ("M4", True, False): "M2",
           ("M4", False, False): "M1", ("M3", True, False): "M1", ("M2", False, True): "M1",
           ("M5", False, False): "M1", ("M0", False, False): "M0"}
    ok = all(rc.deployed_model(D, "M1", f1, f2) == m for (D, f1, f2), m in exp.items())
    check("fallback: §5.2 table incl. both-unavailable -> B", ok)
    check("fallback: B=M0 variant", rc.deployed_model("M4", "M0", False, False) == "M0")


def test_signal_detection():
    """Sanity: with a genuine synthetic F2 edge the rule should usually pass F2; with none, M0/M1."""
    cfg = rc.Config()
    spx, vix = synth_market(11, vrp_signal=0.0)
    pop = rc.estimate_and_forecast(rc.build_origins(CAL, spx, vix, None, cfg), CAL, cfg)
    sel, _, _ = rc.split_samples(pop, CAL, cfg)
    r0 = rc.select_model(sel)
    check("sanity: no-signal data -> F2 not selected", not r0["F2_pass"], f"D={r0['D']}")
    spx, vix = synth_market(11, vrp_signal=6.0)
    pop = rc.estimate_and_forecast(rc.build_origins(CAL, spx, vix, None, cfg), CAL, cfg)
    sel, _, _ = rc.split_samples(pop, CAL, cfg)
    r1 = rc.select_model(sel)
    check("sanity: strong synthetic VRP edge -> b_hat>0 mostly", r1["frac_bhat_pos"] > 0.8, f"{r1['frac_bhat_pos']:.2f}")


def test_score():
    check("score: 1% error -> 45.00", rc.score(101.0, 100.0) == rc.Decimal("45.00"))
    check("score: >=10% error -> 0", rc.score(111.0, 100.0) == rc.Decimal("0.00"))


if __name__ == "__main__":
    for f in [test_calendar, test_label_rule, test_f1_validity, test_no_lookahead, test_f1_point_in_time, test_samples,
              test_fallback, test_signal_detection, test_score]:
        try:
            f()
        except Exception as e:  # a crash is a failed test, never silently skipped
            RESULTS.append((f.__name__ + " CRASHED", False, repr(e)))
    width = max(len(n) for n, _, _ in RESULTS)
    for n, ok, d in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(width)}  {d}")
    nfail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - nfail}/{len(RESULTS)} passed")
    sys.exit(1 if nfail else 0)
