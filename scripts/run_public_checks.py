"""Offline, invented-data demonstration and independent synthetic checks."""
import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from storage_policy import ensure_storage


def run(output):
    ensure_storage(output)
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    for key in ("TEMP", "TMP", "TMPDIR", "MPLCONFIGDIR", "PIP_CACHE_DIR", "PYTHONPYCACHEPREFIX"):
        folder = output / "cache" / key.lower()
        folder.mkdir(parents=True)
        env[key] = str(folder)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONPATH", None)
    package = Path(__file__).resolve().parents[1]
    commands = [
        ["scripts/test_storage_policy.py"],
        ["scripts/test_finalday_tools.py", "--synthetic-only", "--output", str(output / "core_tests")],
        ["scripts/test_source_controls.py", "--output", str(output / "control_tests")],
        ["independent/tests/test_synthetic.py"],
        ["independent/tests/test_round2_synthetic.py"],
        ["independent/tests/test_final_v2_source_control.py"],
    ]
    records = []
    for i, command in enumerate(commands):
        done = subprocess.run([sys.executable, "-B", *command], cwd=package,
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        log = output / f"check_{i}.txt"
        log.write_text(done.stdout + done.stderr, encoding="utf-8")
        records.append({"command": ["python", "-B", *[x.replace(str(output), "<OUTPUT>") for x in command]],
                        "exit_code": done.returncode,
                        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()})
        print(f"{command[0]}: {'PASS' if done.returncode == 0 else 'FAIL'}", flush=True)
        if done.returncode:
            raise RuntimeError(f"Check failed; inspect {log}")

    # All numeric observations below are invented, not transformed market data.
    import m1_forecast as m
    with (package / "metadata/final_session_calendar.csv").open(encoding="utf-8-sig", newline="") as f:
        closes = {date.fromisoformat(r["session_date"]): datetime.fromisoformat(r["close_utc"])
                  for r in csv.DictReader(f)}
    days = [d for d in sorted(closes) if date(2016, 10, 24) <= d <= date(2026, 10, 8)]
    sp = {d: 100 + 0.01*i + 0.3*math.sin(i/13) for i, d in enumerate(days)}
    vix = {d: 20 + 2*math.cos(i/17) for i, d in enumerate(days)}
    invented = {"status": "SYNTHETIC_NOT_MARKET_DATA", "rows": [
        {"date": d.isoformat(), "invented_price": sp[d], "invented_vix": vix[d]} for d in days]}
    m.save(output / "invented_inputs.json", invented)
    current, rows = m.prepare(date(2026, 10, 9), sp, vix, closes)
    estimate = m.estimate(current, rows)
    result = {"status": "SYNTHETIC_NOT_A_SUBMISSION_FORECAST", "origin": "2026-10-09",
              "forecast_2dp": estimate["forecast_2dp"], "forecast_full_precision": estimate["forecast_full_precision"],
              "n_train": estimate["n_train"], "invented_input_sha256": m.sha(output / "invented_inputs.json"),
              "calendar_sha256": m.sha(package / "metadata/final_session_calendar.csv")}
    m.save(output / "synthetic_demo.json", result)
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "platform": platform.system(), "network_used": False, "commands": records,
              "synthetic_demo": result,
              "code_sha256": {p.relative_to(package).as_posix(): m.sha(p)
                              for p in sorted(package.rglob("*.py"))}}
    m.save(output / "public_check_record.json", report)
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
