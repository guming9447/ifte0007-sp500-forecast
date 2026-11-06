"""Lossless-text Cboe conversion or S&P XLS numeric export with source linkage.

S&P file alone does not prove USD/Price Return: retain its page screenshot too.
The source-control reviewer must confirm field/date, origin and availability.
"""
import argparse
import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from final_day_pipeline import ensure_e, write_daily
from m1_forecast import save, sha


def convert(source, kind, output):
    ensure_e(output)
    if output.exists():
        raise FileExistsError("New converted file required")
    values = {}
    def add(day, raw):
        value = Decimal(str(raw))
        if day in values or not value.is_finite() or value <= 0:
            raise ValueError("Duplicate or invalid observation")
        values[day] = value
    if kind == "cboe":
        with source.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                add(datetime.strptime(row["DATE"], "%m/%d/%Y").date(), row["CLOSE"])
        column = "VIXCLS"
    else:
        import xlrd
        book = xlrd.open_workbook(source)
        sheet = book.sheet_by_index(0)
        # Locate the date/value table; do not assume workbook title row offsets.
        for r in range(sheet.nrows):
            cell = sheet.cell(r, 0)
            try:
                if cell.ctype == xlrd.XL_CELL_DATE:
                    day = xlrd.xldate_as_datetime(cell.value, book.datemode).date()
                else:
                    day = datetime.strptime(str(cell.value).strip(), "%m/%d/%Y").date()
            except ValueError:
                continue
            if sheet.ncols < 2 or sheet.cell(r, 1).ctype != xlrd.XL_CELL_NUMBER:
                raise ValueError("Dated row without numeric level")
            add(day, sheet.cell_value(r, 1))
        column = "SP500"
    if not values:
        raise ValueError("No dated observations; inspect source manually")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_daily(output, column, sorted(values), values)
    save(output.with_suffix(".provenance.json"), {"source_name": source.name, "source_sha256": sha(source),
        "converted_sha256": sha(output), "converter_sha256": sha(Path(__file__)), "rows": len(values),
        "first": min(values), "last": max(values), "kind": kind,
        "field_and_availability_review": "PENDING", "no_market_rows_published": True})
    print("CONVERTED_NOT_SOURCE_APPROVED=" + str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--kind", choices=("cboe", "sp_xls"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    convert(args.source, args.kind, args.output)
