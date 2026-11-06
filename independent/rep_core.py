"""Claude's independent replication of Method Specification v04 (S&P 500 one-week forecast).

Written from the SPEC ONLY (Claude_Work/03_Factors_and_Model/02_Method_Specification_v04.md).
It does not import or read any Codex code. Section references (§) point to the spec.

Structure
  1. NYSE calendar (holiday rules, special closures, early closes)      -> spec §1, D4
  2. Data loaders (FRED CSV, Yahoo chart JSON)                           -> spec §2
  3. Features: F1 point-in-time validity + ON (§3.1); F2 VRP proxy (§3.2)
  4. Estimation and models M0-M5 (§4)
  5. Population, samples, fallback chain (§5)
  6. Selection rule (§6) and hold-out report (§5.4)
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# --------------------------------------------------------------------------------------
# 1. NYSE calendar (built from rules; cross-checked against exchange_calendars in R-03)
# --------------------------------------------------------------------------------------
SPECIAL_CLOSURES = {date(1994, 4, 27), date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13),
                    date(2001, 9, 14), date(2004, 6, 11), date(2007, 1, 2), date(2012, 10, 29),
                    date(2012, 10, 30), date(2018, 12, 5), date(2025, 1, 9)}


def _nth_weekday(y, m, wd, n):
    d = date(y, m, 1)
    d += timedelta((wd - d.weekday()) % 7)
    return d + timedelta(7 * (n - 1))


def _last_weekday(y, m, wd):
    d = (date(y, m + 1, 1) if m < 12 else date(y + 1, 1, 1)) - timedelta(1)
    return d - timedelta((d.weekday() - wd) % 7)


def _easter(y):
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4; f = (b + 8) // 25
    g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    return date(y, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)


def _observed(d, sat_to_fri=True):
    if d.weekday() == 5:
        return d - timedelta(1) if sat_to_fri else None
    if d.weekday() == 6:
        return d + timedelta(1)
    return d


def _holidays(y):
    h = {_observed(date(y, 1, 1), sat_to_fri=False), _nth_weekday(y, 2, 0, 3),
         _easter(y) - timedelta(2), _last_weekday(y, 5, 0), _observed(date(y, 7, 4)),
         _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4), _observed(date(y, 12, 25))}
    if y >= 1998:
        h.add(_nth_weekday(y, 1, 0, 3))
    if y >= 2022:
        h.add(_observed(date(y, 6, 19)))
    return {d for d in h if d}


class NYSECalendar:
    def __init__(self, start: date, end: date):
        hol = set().union(*(_holidays(y) for y in range(start.year, end.year + 1))) | SPECIAL_CLOSURES
        self.sessions = []
        d = start
        while d <= end:
            if d.weekday() < 5 and d not in hol:
                self.sessions.append(d)
            d += timedelta(1)
        self.session_set = set(self.sessions)

    def is_session(self, d):
        return d in self.session_set

    def is_early_close(self, d):
        """13:00 ET closes: day after Thanksgiving, Christmas Eve, 3 July when 4 July is Tue-Fri."""
        if d not in self.session_set:
            return False
        if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(1):
            return True
        if d.month == 12 and d.day == 24:
            return True
        if d.month == 7 and d.day == 3 and d.weekday() <= 3:
            return True
        return False

    def close_dt(self, d) -> datetime:
        return datetime.combine(d, time(13 if self.is_early_close(d) else 16), NY)

    def last_session_before(self, d):
        x = d - timedelta(1)
        for _ in range(15):
            if x in self.session_set:
                return x
            x -= timedelta(1)
        return None

    def last_session_on_or_before(self, d):
        x = d
        for _ in range(15):
            if x in self.session_set:
                return x
            x -= timedelta(1)
        return None

    def sessions_ending(self, d, n):
        """Return the n sessions ending at d (inclusive), oldest first, or None."""
        i = self.sessions.index(d) if d in self.session_set else None
        if i is None or i + 1 < n:
            return None
        return self.sessions[i + 1 - n: i + 1]


def cutoff_dt(friday: date) -> datetime:
    return datetime.combine(friday, time(7), NY)


# --------------------------------------------------------------------------------------
# 2. Loaders
# --------------------------------------------------------------------------------------
def load_fred_csv(path):
    """FRED fredgraph CSV -> {date: float}; blank or '.' values are skipped (missing)."""
    out = {}
    with open(path, newline="") as fh:
        rd = csv.reader(fh)
        next(rd)
        for row in rd:
            v = row[1].strip()
            if v not in ("", "."):
                out[date.fromisoformat(row[0])] = float(v)
    return out


@dataclass
class Bar:
    label: datetime  # bar START time, America/New_York (spec §2: start-time labels)
    open: float
    close: float
    volume: float


def load_yahoo_chart(path):
    """Yahoo chart JSON -> {label_datetime_NY: Bar}. Rows with a missing open/close are dropped."""
    d = json.load(open(path))
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    bars = {}
    for i, ts in enumerate(r["timestamp"]):
        o, c, v = q["open"][i], q["close"][i], q["volume"][i]
        if o is None or c is None:
            continue
        lab = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(NY)
        bars[lab] = Bar(lab, float(o), float(c), float(v or 0))
    return bars


# --------------------------------------------------------------------------------------
# 3. Features
# --------------------------------------------------------------------------------------
GAP_LIMIT = 0.0025


def f1_window(bars: dict, cal: NYSECalendar, b: date, friday: date, signal_label_hour=5,
              intermediate_volume_check=True):
    """Spec §3.1. Returns (ON or None, reason). Uses only bars inside [kappa-1h, Fri 05:00].
    intermediate_volume_check=True  -> v04 as written: every zero-volume bar counts as missing.
    intermediate_volume_check=False -> amendment A1: volume > 0 required only for the two anchor bars."""
    kappa = cal.close_dt(b)
    a_lab = kappa - timedelta(hours=1)
    s_lab = datetime.combine(friday, time(signal_label_hour), NY)
    win = sorted((bar for lab, bar in bars.items() if a_lab <= lab <= s_lab), key=lambda x: x.label)
    if intermediate_volume_check:
        win = [x for x in win if x.volume > 0]  # v04: zero-volume bars count as missing
    labs = {x.label: x for x in win}
    if a_lab not in labs or labs[a_lab].volume <= 0:
        return None, "anchor_bar_missing"
    if s_lab not in labs or labs[s_lab].volume <= 0:
        return None, "signal_bar_missing"
    for prev, cur in zip(win, win[1:]):
        step = (cur.label - prev.label)
        brk = (prev.label.time() == time(16) and cur.label.time() == time(18)
               and cur.label.date() == prev.label.date())
        if step != timedelta(hours=1) and not brk:
            return None, f"incomplete:{prev.label.isoformat()}->{cur.label.isoformat()}"
        if abs(math.log(cur.open / prev.close)) > GAP_LIMIT:
            return None, f"suspected_splice:{cur.label.isoformat()}"
    return math.log(labs[s_lab].close / labs[a_lab].close), "valid"


def vrp(cal: NYSECalendar, spx: dict, vix: dict, b: date, n=22):
    """Spec §3.2: VIX_b^2 - (252/n) * sum of n squared 100*log returns ending at b."""
    if b not in vix:
        return None
    ss = cal.sessions_ending(b, n + 1)
    if ss is None or any(d not in spx for d in ss):
        return None
    rv = sum((100 * math.log(spx[ss[i]] / spx[ss[i - 1]])) ** 2 for i in range(1, n + 1))
    return vix[b] ** 2 - (252 / n) * rv


# --------------------------------------------------------------------------------------
# 4/5. Origins, estimation, models, fallback
# --------------------------------------------------------------------------------------
@dataclass
class Origin:
    t: date
    b: date
    T: date
    P0: float
    A: float
    r: float
    vrp: float | None = None
    on: float | None = None
    on_reason: str = ""
    fc: dict = field(default_factory=dict)      # model -> forecast (computable without fallback)
    info: dict = field(default_factory=dict)    # estimation details


@dataclass
class Config:
    W: int | None = 260          # None = expanding
    Wmin: int = 156
    rv_n: int = 22
    lam: float = 1.0
    restrictions: bool = True
    signal_label_hour: int = 5   # 05:00-labelled bar = ends 06:00 ET
    intermediate_volume_check: bool = True   # True = v04 as written; False = amendment A1
    holdout_start: date = date(2025, 10, 17)


def score(F: float, A: float) -> Decimal:
    Fd, Ad = Decimal(repr(F)), Decimal(repr(A))
    s = Decimal(50) - 5 * (100 * abs(Fd - Ad) / Ad)
    return max(Decimal(0), s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_origins(cal, spx, vix, es_bars, cfg: Config):
    first, last = min(spx), max(spx)
    t = first + timedelta((4 - first.weekday()) % 7)
    out = []
    while t + timedelta(7) <= last:
        b = cal.last_session_before(t)
        T = cal.last_session_on_or_before(t + timedelta(7))
        if b and T and b in spx and T in spx and b >= first:
            o = Origin(t, b, T, spx[b], spx[T], math.log(spx[T] / spx[b]))
            o.vrp = vrp(cal, spx, vix, b, cfg.rv_n) if vix is not None else None
            if es_bars is not None:
                o.on, o.on_reason = f1_window(es_bars, cal, b, t, cfg.signal_label_hour, cfg.intermediate_volume_check)
            else:
                o.on_reason = "no_es_data"
            out.append(o)
        t += timedelta(7)
    return out


def _usable(origins, t, cal):
    c = cutoff_dt(t)
    return [s for s in origins if cal.close_dt(s.T) < c]  # spec §1 label-availability rule


def _ols(x, y):
    n = len(x); mx = sum(x) / n; my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return None
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    return my - b * mx, b


def estimate_and_forecast(origins, cal, cfg: Config):
    for o in origins:
        use = _usable(origins, o.t, cal)
        lab_rows = use if cfg.W is None else use[-cfg.W:]
        info = o.info
        info["n_usable"] = len(use)
        if len(use) < cfg.Wmin:
            info["population"] = False
            continue
        info["population"] = True                      # spec §5.1 (P0, A, >=156 labelled rows)
        f2_rows_all = [s for s in use if s.vrp is not None]
        f2_rows = f2_rows_all if cfg.W is None else f2_rows_all[-cfg.W:]
        f2_ok = (o.vrp is not None and len(f2_rows) >= cfg.Wmin)
        fit = _ols([s.vrp for s in f2_rows], [s.r for s in f2_rows]) if f2_ok else None
        f2_ok = f2_ok and fit is not None
        # mu-hat: F2 row set when F2 available, else most recent W labelled rows (spec §4 last para)
        mu_rows = f2_rows if f2_ok else lab_rows
        mu = sum(s.r for s in mu_rows) / len(mu_rows)
        info.update(mu=mu, n_rows=len(mu_rows), f2_available=f2_ok, f1_valid=o.on is not None)
        o.fc["M0"] = o.P0
        o.fc["M1"] = o.P0 * math.exp(mu)
        if f2_ok:
            a, b = fit
            info.update(a_hat=a, b_hat=b)
            flags = []
            if cfg.restrictions and b <= 0:
                g = mu; flags.append("fallback_drift")
            else:
                g = a + b * o.vrp
            if cfg.restrictions and g < 0:
                g = 0.0; flags.append("truncated")
            g = mu + cfg.lam * (g - mu)
            info.update(g_hat=g, flags=flags)
            o.fc["M3"] = o.P0 * math.exp(g)
        if o.on is not None:
            o.fc["M2"] = o.P0 * math.exp(o.on)
            o.fc["M5"] = o.P0 * math.exp(o.on + mu)
            if f2_ok:
                o.fc["M4"] = o.P0 * math.exp(o.on + info["g_hat"])
    return [o for o in origins if o.info.get("population")]


FALLBACK = {  # spec §5.2: (D, F1 valid, F2 available) -> model actually used; 'B' = base
    "M4": {(True, True): "M4", (False, True): "M3", (True, False): "M2", (False, False): "B"},
    "M3": {(True, True): "M3", (False, True): "M3", (True, False): "B", (False, False): "B"},
    "M2": {(True, True): "M2", (False, True): "B", (True, False): "M2", (False, False): "B"},
    "M5": {(True, True): "M5", (False, True): "M1", (True, False): "M5", (False, False): "M1"},
}


def deployed_model(D, B, f1, f2):
    if D in ("M0", "M1"):
        return D
    m = FALLBACK[D][(f1, f2)]
    return B if m == "B" else m


# --------------------------------------------------------------------------------------
# 6. Samples and selection rule
# --------------------------------------------------------------------------------------
def split_samples(pop, cal, cfg: Config):
    h0 = cutoff_dt(cfg.holdout_start)
    selection = [o for o in pop if cal.close_dt(o.T) < h0]
    holdout = [o for o in pop if o.t >= cfg.holdout_start]
    embargo = [o for o in pop if o not in selection and o not in holdout]
    return selection, embargo, holdout


def delta(origins, X, Y, need_min=None):
    common = [o for o in origins if X in o.fc and Y in o.fc]
    n = len(common)
    if n == 0 or (need_min and n < need_min):
        return {"n": n, "pass": False, "eligible": False}
    d = [float(score(o.fc[X], o.A) - score(o.fc[Y], o.A)) for o in common]
    h = n // 2
    tot, d1, d2 = sum(d) / n, (sum(d[:h]) / h if h else 0.0), sum(d[h:]) / (n - h)
    return {"n": n, "eligible": True, "delta": tot, "delta_H1": d1, "delta_H2": d2,
            "pass": tot > 0 and d1 > 0 and d2 > 0,
            "wins": sum(x > 0 for x in d), "losses": sum(x < 0 for x in d), "ties": sum(x == 0 for x in d)}


def mean_score(origins, X):
    xs = [float(score(o.fc[X], o.A)) for o in origins if X in o.fc]
    return sum(xs) / len(xs) if xs else None


def select_model(sel, min_es=40):
    res = {}
    res["M1_vs_M0"] = delta(sel, "M1", "M0")
    B = "M1" if res["M1_vs_M0"]["pass"] else "M0"
    res["M3_vs_M0"] = delta(sel, "M3", "M0")
    res["M3_vs_M1"] = delta(sel, "M3", "M1")
    f2o = [o for o in sel if "M3" in o.fc and "M0" in o.fc]
    frac_b = (sum(1 for o in f2o if o.info.get("b_hat", 0) > 0) / len(f2o)) if f2o else 0.0
    res["frac_bhat_pos"] = frac_b
    f2_pass = res["M3_vs_M0"]["pass"] and res["M3_vs_M1"]["pass"] and frac_b >= 0.8
    res["M2_vs_M0"] = delta(sel, "M2", "M0", need_min=min_es)
    f1_pass = res["M2_vs_M0"]["pass"]
    if not f1_pass and not f2_pass:
        D = B
    elif f2_pass and not f1_pass:
        D = "M3"
    elif f1_pass and not f2_pass:
        if B == "M0":
            D = "M2"
        else:
            res["M5_vs_M2"] = delta(sel, "M5", "M2", need_min=min_es)
            D = "M5" if res["M5_vs_M2"]["pass"] else "M2"
    else:
        res["M4_vs_M2"] = delta(sel, "M4", "M2", need_min=min_es)
        res["M4_vs_M3"] = delta(sel, "M4", "M3", need_min=min_es)
        if res["M4_vs_M2"]["pass"] and res["M4_vs_M3"]["pass"]:
            D = "M4"
        else:
            common = [o for o in sel if "M2" in o.fc and "M3" in o.fc]
            m2, m3 = mean_score(common, "M2"), mean_score(common, "M3")
            D = "M3" if (m3 is not None and m2 is not None and m3 > m2) else "M2"
    res.update(B=B, F1_pass=f1_pass, F2_pass=f2_pass, D=D)
    return res


def holdout_report(hold, D, B):
    rows = []
    for o in hold:
        used = deployed_model(D, B, o.info["f1_valid"], o.info["f2_available"])
        rows.append((o, used))
    def avg(seq):
        return sum(seq) / len(seq) if seq else None
    return {
        "n": len(hold),
        "D_as_deployed_mean": avg([float(score(o.fc[u], o.A)) for o, u in rows]),
        "B_mean": avg([float(score(o.fc[B], o.A)) for o in hold]),
        "M0_mean": avg([float(score(o.fc["M0"], o.A)) for o in hold]),
        "F1_valid_rate": avg([1.0 if o.info["f1_valid"] else 0.0 for o in hold]),
        "F2_available_rate": avg([1.0 if o.info["f2_available"] else 0.0 for o in hold]),
        "models_used": {m: sum(1 for _, u in rows if u == m) for m in {u for _, u in rows}},
    }
