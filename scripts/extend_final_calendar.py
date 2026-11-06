"""Generate XNYS metadata with the previously used library, preserving history."""
import argparse
import csv
import importlib.metadata
from datetime import date, datetime, timezone
from pathlib import Path

import exchange_calendars as xc
import m1_forecast as m1
from final_day_pipeline import calendar, ensure_e


def run(old, output):
    ensure_e(output)
    frozen = calendar(old)
    cal = xc.get_calendar("XNYS", start=str(min(frozen)), end="2026-11-30")
    closes = {s.date(): cal.session_close(s).to_pydatetime() for s in cal.sessions}
    if {d: v for d, v in closes.items() if d <= max(frozen)} != frozen:
        raise ValueError("STOP: generated calendar changed frozen history")
    output.mkdir(parents=True, exist_ok=False)
    with (output / "session_calendar.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["session_date", "close_utc"])
        writer.writerows((d.isoformat(), c.isoformat()) for d, c in closes.items())
    m1.save(output / "calendar_manifest.json", {"created_utc": datetime.now(timezone.utc),
        "exchange_calendars": xc.__version__, "old_sha256": m1.sha(old),
        "new_sha256": m1.sha(output / "session_calendar.csv"), "overlap_exact": True,
        "dependencies": {n: importlib.metadata.version(n) for n in
                         ("pandas", "numpy", "tzdata", "pyluach", "toolz", "python-dateutil")},
        "critical_closes": {str(d): closes[d] for d in (date(2026,11,5),date(2026,11,6),date(2026,11,13),date(2026,11,20))}})
    print("Calendar extended through 2026-11-30; frozen overlap exact")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.old, args.output)
