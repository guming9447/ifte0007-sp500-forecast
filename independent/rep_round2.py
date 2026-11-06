"""Claude's independent replication of the v05 round-2 module (F3_MOM20, F4_TS; models M6, M7).

Written from 03_Factors_and_Model/06_Method_Specification_v05_Round2.md only; builds on Claude's own
rep_core (v04 + A1). No Codex code is used. Section references (§) are to v05.
"""
from __future__ import annotations

import math
from datetime import date

import rep_core as rc


def x3_mom20(cal: rc.NYSECalendar, spx: dict, b: date):
    """§2: ln(P_b / P_s20), s20 = 20 NYSE sessions before b (calendar index). Endpoints must exist."""
    if b not in cal.session_set:
        return None
    i = cal.sessions.index(b)
    if i < 20:
        return None
    s20 = cal.sessions[i - 20]
    if b not in spx or s20 not in spx:
        return None
    return math.log(spx[b] / spx[s20])


def x4_ts(vix: dict, vix3m: dict, b: date):
    """§2: ln(VIX_b / VIX3M_b), same date, no forward fill."""
    if b in vix and b in vix3m and vix[b] > 0 and vix3m[b] > 0:
        return math.log(vix[b] / vix3m[b])
    return None


def add_round2(pop, all_origins, cal, spx, vix, vix3m, W=260, Wmin=156):
    """Attach x3/x4, estimate M6/M7 per §4. `pop` comes from rc.estimate_and_forecast (v04 + A1),
    so o.info['mu'] is the FIXED M1 drift (§4). `all_origins` supplies training rows (label rule §1)."""
    for o in all_origins:
        o.info.setdefault("x3", x3_mom20(cal, spx, o.b))
        o.info.setdefault("x4", x4_ts(vix, vix3m, o.b))
    for o in pop:
        use = rc._usable(all_origins, o.t, cal)
        for k, key, model in ((3, "x3", "M6"), (4, "x4", "M7")):
            xt = o.info[key]
            rows = [s for s in use if s.info.get(key) is not None]
            rows = rows if W is None else rows[-W:]
            o.info[f"n_{key}"] = len(rows)
            if xt is None or len(rows) < Wmin:
                o.info[f"{model}_computable"] = False
                continue
            fit = rc._ols([s.info[key] for s in rows], [s.r for s in rows])
            if fit is None:
                o.info[f"{model}_computable"] = False
                continue
            a, b = fit
            mu = o.info["mu"]                      # fixed M1 drift
            if b <= 0:
                g, flag = mu, "fallback_drift"
            else:
                g, flag = a + b * xt, ""           # §4 step 4: NO non-negativity truncation
            o.info.update({f"a{k}": a, f"b{k}": b, f"g{k}": g, f"flag{k}": flag, f"{model}_computable": True})
            o.fc[model] = o.P0 * math.exp(g)
    return pop


def select_round2(sel, min_n=40):
    """§5. Returns per-model pass details and D."""
    res = {}
    passed = []
    for model, k in (("M6", 3), ("M7", 4)):
        d0 = rc.delta(sel, model, "M0", need_min=min_n)
        d1 = rc.delta(sel, model, "M1", need_min=min_n)
        comp = [o for o in sel if model in o.fc and "M0" in o.fc]
        frac = (sum(1 for o in comp if o.info[f"b{k}"] > 0) / len(comp)) if comp else 0.0
        ok = d0["pass"] and d1["pass"] and frac >= 0.8
        res[model] = {"vs_M0": d0, "vs_M1": d1, "frac_b_pos": frac, "n_compared": len(comp), "pass": ok}
        if ok:
            passed.append(model)
    if not passed:
        D = "M1"
    elif len(passed) == 1:
        D = passed[0]
    else:
        common = [o for o in sel if "M6" in o.fc and "M7" in o.fc]
        m6, m7 = rc.mean_score(common, "M6"), rc.mean_score(common, "M7")
        D = "M7" if (m7 is not None and m6 is not None and m7 > m6) else "M6"
    res["D"] = D
    return res


def holdout_round2(hold, D):
    def avg(xs):
        return sum(xs) / len(xs) if xs else None
    used = [(o, D if D in o.fc else "M1") for o in hold]   # §6 fallback to M1
    out = {"n": len(hold), "D": D,
           "D_as_deployed_mean": avg([float(rc.score(o.fc[u], o.A)) for o, u in used]),
           "M1_mean": avg([float(rc.score(o.fc["M1"], o.A)) for o in hold]),
           "M0_mean": avg([float(rc.score(o.fc["M0"], o.A)) for o in hold])}
    for m in ("M6", "M7"):
        xs = [o for o in hold if m in o.fc]
        out[f"{m}_coverage"] = len(xs) / len(hold) if hold else None
        out[f"{m}_mean_on_own"] = avg([float(rc.score(o.fc[m], o.A)) for o in xs])
        out[f"{m}_minus_M1_on_own"] = avg([float(rc.score(o.fc[m], o.A) - rc.score(o.fc["M1"], o.A)) for o in xs])
    return out
