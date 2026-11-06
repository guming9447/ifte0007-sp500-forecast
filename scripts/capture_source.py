"""On-demand HTTPS source capture to a NEW E-drive folder; no scheduler.

The receipt proves acquisition time, not first publication. An HTTP error or
HTML challenge is never a validated market observation. S&P export is manual
when the public page cannot provide a verifiable downloadable value.
"""
import argparse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from final_day_pipeline import ensure_e
from m1_forecast import save, sha

SOURCES = {
    "fred_sp500": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500&cosd=2016-10-24&coed=2026-11-12",
    "fred_vix": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd=1990-01-02&coed=2026-11-12",
    "cboe_vix": "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "sp_page": "https://www.spglobal.com/spdji/en/indices/equity/sp-500/",
}


def capture(source, output):
    ensure_e(output)
    output.mkdir(parents=True, exist_ok=False)
    url = SOURCES[source]
    record = {"source": source, "url": url, "started_utc": datetime.now(timezone.utc),
              "validated_observation": False, "availability_review": "PENDING", "mode": "NEW_VERSION"}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (coursework source verification)"})
        with urllib.request.urlopen(request, timeout=45) as response:
            if urlsplit(response.url).scheme != "https":
                raise ValueError("Non-HTTPS redirect")
            body = response.read(32 * 1024 * 1024 + 1)
            if len(body) > 32 * 1024 * 1024:
                raise ValueError("Response size exceeds limit")
            path = output / ("response.html" if source == "sp_page" else "response.csv")
            path.write_bytes(body)
            record.update({"status": response.status, "final_url": response.url,
                           "sha256": sha(path), "file": path.name,
                           "http_date": response.headers.get("Date"),
                           "last_modified": response.headers.get("Last-Modified")})
    except Exception as error:
        record["error"] = str(error)
        raise
    finally:
        record["retrieved_utc"] = datetime.now(timezone.utc)
        save(output / "receipt.json", record)
    print("CAPTURED_NOT_VALIDATED=" + str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture(args.source, args.output)
